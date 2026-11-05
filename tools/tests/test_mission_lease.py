import ctypes as C
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

TOOLS=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(TOOLS))
from mission_lease import Lease, GuardedMemory, MissionInactive, HEADER
from mission_jobs import BasicLimits, ExtendedLimits, managed_spawn
from mission_supervisor import stop_tree

RUN='1234567890abcdef1234567890abcdef'


class LeaseTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path=Path(temporary.name)/'lease.bin'
        self.owner=Lease(self.path,RUN,create=True)
        self.addCleanup(self.owner.close)

    def test_revocation_and_generation_change_block_stale_writes(self):
        base=io.BytesIO(bytes(12))
        worker=GuardedMemory(base,Lease(self.path,RUN),1)
        self.addCleanup(worker.close)
        with self.assertRaises(MissionInactive): worker.write(b'bad')
        self.owner.activate(1)
        worker.write(b'one')
        self.owner.deactivate()
        with self.assertRaises(MissionInactive): worker.write(b'old')
        self.owner.activate(2)
        with self.assertRaises(MissionInactive): worker.write(b'old')
        self.assertFalse(self.owner.deactivate(1))
        self.assertTrue(self.owner.active(2))
        self.assertEqual(base.getvalue(),b'one'+bytes(9))

    def test_revocation_waits_for_an_in_flight_writer_and_then_blocks_new_writes(self):
        entered,release,revoked=threading.Event(),threading.Event(),threading.Event()
        class SlowMemory(io.BytesIO):
            def write(self,data):
                entered.set()
                if not release.wait(3): raise RuntimeError('test writer not released')
                return super().write(data)
        worker=GuardedMemory(SlowMemory(),Lease(self.path,RUN),1)
        self.addCleanup(worker.close)
        self.owner.activate(1)
        writing=threading.Thread(target=lambda:worker.write(b'first'))
        cancelling=threading.Thread(target=lambda:(self.owner.deactivate(),revoked.set()))
        writing.start(); self.assertTrue(entered.wait(2)); cancelling.start()
        try: self.assertFalse(revoked.wait(.05))
        finally:
            release.set(); writing.join(3); cancelling.join(3)
        self.assertTrue(revoked.is_set())
        with self.assertRaises(MissionInactive): worker.write(b'late')

    def test_cross_process_barrier_revokes_a_separate_process_mapping(self):
        self.owner.activate(1)
        code='''
import sys
sys.path.insert(0,sys.argv[1])
from mission_lease import Lease,MissionInactive
with Lease(sys.argv[2],sys.argv[3]) as lease:
 with lease.guard(1):
  print('locked',flush=True)
  sys.stdin.readline()
 print('released',flush=True)
 sys.stdin.readline()
 try:
  with lease.guard(1): print('unexpected write',flush=True)
 except MissionInactive: print('revoked',flush=True)
'''
        child=subprocess.Popen([sys.executable,'-c',code,str(TOOLS),str(self.path),RUN],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        self.addCleanup(lambda: child.kill() if child.poll() is None else None)
        self.assertEqual(child.stdout.readline().strip(),'locked')
        revoked=threading.Event()
        cancelling=threading.Thread(target=lambda:(self.owner.deactivate(),revoked.set()))
        cancelling.start()
        try:
            self.assertFalse(revoked.wait(.05))
            child.stdin.write('release\n'); child.stdin.flush()
            self.assertEqual(child.stdout.readline().strip(),'released')
            self.assertTrue(revoked.wait(2))
            child.stdin.write('check\n'); child.stdin.flush()
            stdout,stderr=child.communicate(timeout=3)
            self.assertEqual(child.returncode,0,stderr)
            self.assertEqual(stdout.strip(),'revoked')
        finally: cancelling.join(3)

    def test_bad_run_id_and_bad_size_cannot_join_a_lease(self):
        with self.assertRaises(MissionInactive): Lease(self.path,'0'*32)
        bad=self.path.with_name('bad.bin'); bad.write_bytes(b'broken')
        with self.assertRaises(MissionInactive): Lease(bad,RUN)
        self.assertEqual(self.path.stat().st_size,HEADER.size)

    def test_windows_job_structures_have_the_documented_x64_layout(self):
        self.assertEqual(C.sizeof(BasicLimits),64)
        self.assertEqual(C.sizeof(ExtendedLimits),144)
        self.assertEqual(BasicLimits.flags.offset,16)
        self.assertEqual(ExtendedLimits.io.offset,64)

    def test_managed_process_receives_start_gate_and_is_stopped_as_owned_tree(self):
        code="import sys,time; assert sys.stdin.readline().strip()=='start'; print('ready',flush=True); sys.stdin.readline()"
        child=managed_spawn([sys.executable,'-c',code],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        self.addCleanup(lambda:stop_tree(child))
        self.assertEqual(child.stdout.readline().strip(),b'ready')
        stop_tree(child)
        self.assertEqual(child.returncode,0)
        child.stdout.close(); child.stderr.close()

    def test_failed_job_assignment_never_releases_the_child_start_gate(self):
        import mission_jobs
        child=Mock(); child.poll.return_value=None; child._handle=123
        with patch.object(mission_jobs,'os',Mock(name='os')) as fake_os, \
             patch.object(mission_jobs.subprocess,'Popen',return_value=child), \
             patch.object(mission_jobs,'Job',side_effect=OSError('assignment rejected')):
            fake_os.name='nt'
            with self.assertRaisesRegex(OSError,'assignment rejected'): managed_spawn(['worker'])
        child.stdin.write.assert_not_called()
        child.terminate.assert_called_once()

    def test_real_empty_selection_worker_exits_when_its_lease_is_revoked(self):
        root = self.path.parent
        (root / 'data/logs').mkdir(parents=True)
        (root / 'data/combat_patches.txt').write_text('# No memory patches in this process test\n')
        self.owner.activate(1)
        env = {**os.environ, 'ANTHEM_MISSION_LEASE': str(self.path),
               'ANTHEM_MISSION_RUN_ID': RUN, 'ANTHEM_MISSION_GENERATION': '1'}
        child = managed_spawn([sys.executable, '-X', 'utf8', '-I', str(TOOLS / 'auto_patches.py'),
                               str(os.getpid()), '--mission-worker', '1', '--mission-session', RUN],
                              cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(lambda: stop_tree(child))
        self.owner.deactivate()
        stdout, stderr = child.communicate(timeout=8)
        self.assertEqual(child.returncode, 0, stderr.decode())
        self.assertNotIn(b'Traceback', stdout + stderr)


if __name__=='__main__': unittest.main()

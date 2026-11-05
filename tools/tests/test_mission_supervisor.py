import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from mission_lease import Lease
from mission_supervisor import Supervisor

RUN='1234567890abcdef1234567890abcdef'


class Child:
    def __init__(self,pid):
        self.pid, self.returncode = pid, None
    def poll(self): return self.returncode


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.data=Path(temp.name)
        self.owner=Lease(self.data/f'mission_lease_123_{RUN}.bin',RUN,create=True)
        self.addCleanup(self.owner.close)
        self.workers,self.preparers,self.stopped,self.messages=[],[],[],[]
        self.now=0
        self.debugger=Child(999)
        def launch_worker(generation,lease):
            child=Child(100+generation); self.workers.append(child); return child
        def launch_prepare(generation):
            child=Child(200+generation); self.preparers.append(child); return child
        def stop(child):
            if child is not None:
                self.stopped.append(child.pid)
                child.returncode=0
        self.supervisor=Supervisor(123,RUN,self.debugger,launch_worker,launch_prepare,lambda _:True,
            data=self.data,clock=lambda:self.now,stop=stop,debugger_stop=stop,announce=self.messages.append)
        self.addCleanup(self.supervisor.close)

    def state(self,phase,generation,run=RUN):
        (self.data/'mission_state_123.json').write_text(json.dumps({'run_id':run,'phase':phase,'generation':generation,'reason':'fixture'}))
        self.supervisor.step()

    def test_two_cycles_have_one_worker_each_and_cancel_old_followers_before_refresh(self):
        self.state('idle',0)
        self.owner.activate(1)
        self.state('active',1); self.state('active',1)
        self.assertEqual(len(self.workers),1)
        self.owner.deactivate()
        self.state('returning',1)
        self.assertIn(101,self.stopped)
        self.assertEqual(len(self.preparers),1)
        self.preparers[-1].returncode=0
        self.state('idle',1); self.state('idle',1)
        self.assertEqual(len(self.preparers),1)
        self.assertEqual(self.supervisor.prepared_generation,1)
        self.owner.activate(2)
        self.state('active',2)
        self.assertEqual(len(self.workers),2)
        self.assertTrue(self.owner.active(2))

    def test_late_parent_cleanup_does_not_revoke_the_new_generation(self):
        self.owner.activate(1); self.state('active',1)
        self.owner.activate(2); self.state('active',2)
        self.assertIn(101,self.stopped)
        self.assertTrue(self.owner.active(2))
        self.assertEqual(self.supervisor.worker.pid,102)

    def test_new_launch_stops_an_incomplete_return_scan(self):
        self.owner.activate(1); self.state('active',1)
        self.owner.deactivate(); self.state('returning',1)
        self.state('queued',1)
        self.assertIn(201,self.stopped)
        self.assertIsNone(self.supervisor.preparer)

    def test_prepare_timeout_is_reported_without_preventing_another_expedition(self):
        self.state('failed',1)
        self.now=121
        self.state('failed',1)
        self.assertIn('timed out',self.supervisor.prepare_error)
        self.owner.activate(2); self.state('active',2)
        self.assertEqual(self.supervisor.worker.pid,102)

    def test_stale_report_cannot_spawn_a_worker_and_debugger_loss_is_not_ignored(self):
        self.owner.activate(1)
        self.state('active',1,run='0'*32)
        self.assertEqual(self.workers,[])
        self.debugger.returncode=1
        with self.assertRaisesRegex(RuntimeError,'Mission debugger exited'):
            self.supervisor.step()

    def test_failed_worker_is_not_respawned_forever_and_next_mission_can_start(self):
        self.owner.activate(1); self.state('active',1)
        self.workers[0].returncode=1
        self.state('active',1); self.state('active',1)
        self.assertIn('worker exited',self.supervisor.worker_error)
        self.assertEqual(len(self.workers),1)
        self.owner.activate(2); self.state('active',2)
        self.assertEqual(len(self.workers),2)
        self.assertIsNone(self.supervisor.worker_error)


if __name__=='__main__': unittest.main()

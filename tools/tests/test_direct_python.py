import contextlib
import json
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import mission_jobs
import auto_patches
from mission_workers import Children


class DirectPythonTests(unittest.TestCase):
    def test_windows_venv_launch_owns_base_interpreter_and_preserves_environment(self):
        venv = r'C:\repo\.venv-windows\Scripts\python.exe'
        base = r'C:\Python314\python.exe'
        command = [venv, '-I', 'worker.py', '123']
        options = {'env': {'ANTHEM_MISSION_GENERATION': '2'}, 'cwd': 'repo'}
        with patch.object(mission_jobs, 'sys', SimpleNamespace(executable=venv, _base_executable=base)), \
             patch.object(mission_jobs, 'os', SimpleNamespace(name='nt', environ={})), \
             patch.object(mission_jobs.Path, 'is_file', return_value=True):
            result, configured = mission_jobs.direct_python(command, options)
        self.assertEqual(result, [base, '-I', 'worker.py', '123'])
        self.assertEqual(configured['env'], {'ANTHEM_MISSION_GENERATION': '2', '__PYVENV_LAUNCHER__': venv})
        self.assertNotIn('__PYVENV_LAUNCHER__', options['env'])
        self.assertEqual(command[0], venv)

    def test_non_python_commands_are_not_rewritten(self):
        command, options = ['another-tool', 'argument'], {'env': {'test': 'value'}}
        with patch.object(mission_jobs, 'os', SimpleNamespace(name='nt')):
            result, configured = mission_jobs.direct_python(command, options)
        self.assertIs(result, command)
        self.assertIs(configured, options)

    def test_missing_base_interpreter_fails_before_spawning(self):
        with patch.object(mission_jobs, 'sys', SimpleNamespace(executable='venv.exe', _base_executable='base.exe')), \
             patch.object(mission_jobs, 'os', SimpleNamespace(name='nt')), \
             patch.object(mission_jobs.Path, 'is_file', return_value=False):
            with self.assertRaisesRegex(OSError, 'Base Python interpreter not found'):
                mission_jobs.direct_python(['venv.exe', 'worker.py'], {})

    def test_real_patch_runner_preserves_script_path_arguments_and_exit_status(self):
        with tempfile.TemporaryDirectory() as temporary:
            script = Path(temporary) / 'probe.py'
            script.write_text("import sys; assert sys.argv[1:] == ['123', 'poser']; print('patch body ran')")
            with Children() as children, contextlib.redirect_stdout(io.StringIO()):
                result = children.run([sys.executable, '-X', 'utf8', '-I', str(TOOLS/'mission_child.py'), str(script), '123', 'poser'], text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Patch interpreter ready: probe.py', result.stdout)
        self.assertIn('patch body ran', result.stdout)

    def test_patch_pipeline_invokes_runner_before_the_target_script(self):
        class Capture:
            def run(self, command, **options):
                self.command = command
                return subprocess.CompletedProcess(command, 0, 'success', '')
        children = Capture()
        with contextlib.redirect_stdout(io.StringIO()):
            auto_patches.apply_in_order('123', [('server_damage.py', ())], children=children)
        self.assertEqual(children.command[4], str(TOOLS/'mission_child.py'))
        self.assertEqual(children.command[5], str(TOOLS/'combat_patches/server_damage.py'))
        self.assertEqual(children.command[6:], ['123', 'poser'])

    @unittest.skipUnless(os.name == 'nt' and sys.prefix != sys.base_prefix, 'requires a real Windows venv')
    def test_windows_venv_handoff_preserves_prefix_and_owns_executing_pid(self):
        command = [sys.executable, '-I', '-c', 'import json,os,sys,numpy; print(json.dumps([os.getpid(),sys.prefix,numpy.__file__]))']
        command, options = mission_jobs.direct_python(command, {})
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **options) as child:
            stdout, stderr = child.communicate(timeout=15)
            self.assertEqual(child.returncode, 0, stderr.decode())
            pid, prefix, numpy_path = json.loads(stdout)
            self.assertEqual(pid, child.pid)
            self.assertEqual(prefix.lower(), sys.prefix.lower())
            self.assertTrue(numpy_path.lower().startswith(sys.prefix.lower()))

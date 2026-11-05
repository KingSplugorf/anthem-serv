"""Windows job ownership for worker trees; a start gate prevents early escape."""
import ctypes as C
import os
import subprocess
import sys
import ntpath
from pathlib import Path


def direct_python(command, options):
    """Own the interpreter itself, preserving Windows venv path initialization.

    CPython's Windows venv redirector creates another process and its own job.
    Use the same __PYVENV_LAUNCHER__ handoff that redirector uses, so -I and
    venv site-packages remain intact without a wrapper escaping our start gate.
    """
    base = getattr(sys, '_base_executable', sys.executable)
    if (os.name != 'nt' or not command or not base
            or ntpath.normcase(str(command[0])) != ntpath.normcase(sys.executable)
            or ntpath.normcase(base) == ntpath.normcase(sys.executable)):
        return command, options
    if not Path(base).is_file():
        raise OSError(f'Base Python interpreter not found: {base}')
    child_options = dict(options)
    child_env = dict(os.environ if options.get('env') is None else options['env'])
    child_env['__PYVENV_LAUNCHER__'] = sys.executable
    child_options['env'] = child_env
    return [base, *command[1:]], child_options


class BasicLimits(C.Structure):
    _fields_ = [('process_time', C.c_int64), ('job_time', C.c_int64), ('flags', C.c_uint32),
                ('minimum_working_set', C.c_size_t), ('maximum_working_set', C.c_size_t),
                ('active_processes', C.c_uint32), ('affinity', C.c_size_t),
                ('priority', C.c_uint32), ('scheduling', C.c_uint32)]


class ExtendedLimits(C.Structure):
    _fields_ = [('basic', BasicLimits), ('io', C.c_uint64 * 6),
                ('process_memory', C.c_size_t), ('job_memory', C.c_size_t),
                ('peak_process_memory', C.c_size_t), ('peak_job_memory', C.c_size_t)]


class Job:
    def __init__(self, child):
        from windows_memory import kernel, check
        self.api, self.handle = kernel(), None
        definitions = {
            'CreateJobObjectW': ([C.c_void_p, C.c_wchar_p], C.c_void_p),
            'SetInformationJobObject': ([C.c_void_p, C.c_int, C.c_void_p, C.c_uint32], C.c_int),
            'AssignProcessToJobObject': ([C.c_void_p, C.c_void_p], C.c_int),
        }
        for name, (arguments, result) in definitions.items():
            getattr(self.api, name).argtypes = arguments
            getattr(self.api, name).restype = result
        self.handle = self.api.CreateJobObjectW(None, None)
        check(self.handle)
        try:
            limits = ExtendedLimits()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            check(self.api.SetInformationJobObject(self.handle, 9, C.byref(limits), C.sizeof(limits)))
            check(self.api.AssignProcessToJobObject(self.handle, int(child._handle)))
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.handle:
            from windows_memory import check
            check(self.api.CloseHandle(self.handle))
            self.handle = None


class ManagedProcess:
    def __init__(self, process, job):
        self.process, self.job = process, job

    def __getattr__(self, name):
        return getattr(self.process, name)


def managed_spawn(command, **options):
    command, options = direct_python(command, options)
    child = subprocess.Popen(command, stdin=subprocess.PIPE, start_new_session=True, **options)
    job = None
    try:
        if os.name == 'nt':
            job = Job(child)
        # The managed worker/preparer reads this token before spawning children.
        child.stdin.write(b'start\n')
        child.stdin.flush()
        return ManagedProcess(child, job)
    except BaseException:
        if job:
            job.close()
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=3)
        child.stdin.close()
        raise

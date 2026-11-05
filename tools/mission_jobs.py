"""Windows job ownership for worker trees; a start gate prevents early escape."""
import ctypes as C
import os
import subprocess


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

"""Run a scoped patch with early startup and stalled-Python diagnostics."""
import faulthandler
from pathlib import Path
import runpy
import sys


def main():
    script = Path(sys.argv[1]).resolve()
    sys.argv = sys.argv[1:]
    sys.argv[0] = str(script)
    sys.path.insert(0, str(script.parent))
    print(f'Patch interpreter ready: {script.name}; Python {sys.version.split()[0]}; prefix {sys.prefix}', flush=True)
    faulthandler.enable()
    faulthandler.dump_traceback_later(45)
    try:
        runpy.run_path(str(script), run_name='__main__')
    finally:
        faulthandler.cancel_dump_traceback_later()


if __name__ == '__main__':
    main()

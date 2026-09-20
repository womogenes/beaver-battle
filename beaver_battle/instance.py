"""Process-scoped locks; the OS releases them even after an interrupted run."""
import os
from pathlib import Path
import tempfile


def claim(name):
    if os.name != 'posix':
        return None
    import fcntl
    path = Path(tempfile.gettempdir()) / f'beaver-{os.getuid()}-{name}.lock'
    handle = path.open('a')
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise RuntimeError(f'Beaver Battle {name} is already running; close it before starting another copy.') from None
    return handle

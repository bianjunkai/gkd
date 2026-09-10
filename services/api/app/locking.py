import errno
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from .errors import AppError

_registry_guard = threading.Lock()
_registry: dict[str, "WorkspaceLock"] = {}


class WorkspaceLock:
    """Thread-reentrant, process-safe advisory lock with crash release."""

    def __init__(self, path: Path):
        self.path = path
        self.thread_lock = threading.RLock()
        self.depth = 0
        self.fd: int | None = None

    @contextmanager
    def acquire(self, timeout: float):
        deadline = time.monotonic() + timeout
        if not self.thread_lock.acquire(timeout=timeout):
            raise AppError("WORKSPACE_BUSY", "工作空间正在保存，请稍后重试。", 503, retryable=True)
        try:
            if self.depth == 0:
                self.fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
                if os.fstat(self.fd).st_size == 0:
                    os.write(self.fd, b"\0")
                while True:
                    try:
                        os.lseek(self.fd, 0, os.SEEK_SET)
                        if os.name == "nt":
                            import msvcrt
                            msvcrt.locking(self.fd, msvcrt.LK_NBLCK, 1)
                        else:
                            import fcntl
                            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except OSError as exc:
                        if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                            raise
                        if time.monotonic() >= deadline:
                            raise AppError(
                                "WORKSPACE_BUSY", "工作空间正在保存，请稍后重试。",
                                503, retryable=True,
                            ) from exc
                        time.sleep(0.025)
            self.depth += 1
            try:
                yield
            finally:
                self.depth -= 1
                if self.depth == 0 and self.fd is not None:
                    os.lseek(self.fd, 0, os.SEEK_SET)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(self.fd, msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(self.fd, fcntl.LOCK_UN)
        finally:
            if self.depth == 0 and self.fd is not None:
                os.close(self.fd)
                self.fd = None
            self.thread_lock.release()


def workspace_lock(path: Path, timeout: float):
    key = str(path.resolve())
    with _registry_guard:
        lock = _registry.setdefault(key, WorkspaceLock(path))
    return lock.acquire(timeout)

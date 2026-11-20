import os


class ManagerLock:
    """Prevent two managers from owning the same process database."""

    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.file = path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt

                self.file.write(b"0")
                self.file.flush()
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.file.close()
            raise RuntimeError(
                "Another manager is using this database; run exactly one worker"
            ) from exc

    def close(self):
        self.file.close()

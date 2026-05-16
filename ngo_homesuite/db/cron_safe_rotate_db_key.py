"""Cron-safe wrapper for database key rotation across Unix and Windows."""

import logging
import os
from ngo_homesuite.db.connection import DB_PATH

try:  # pragma: no cover - platform-specific import
    import fcntl  # type: ignore
except ImportError:  # pragma: no cover - platform-specific import
    fcntl = None
    import msvcrt  # type: ignore
else:  # pragma: no cover - platform-specific import
    msvcrt = None

LOCKFILE = os.environ.get("NGO_HOMESUITE_ROTATE_LOCKFILE", f"{DB_PATH}.rotate.lock")
LOGFILE = os.environ.get("NGO_HOMESUITE_ROTATE_LOGFILE", f"{DB_PATH}.rotate.log")

logging.basicConfig(
    filename=LOGFILE,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)


def _lock_handle(lock) -> None:
    if fcntl is not None:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return
    lock.seek(0)
    lock.write("0")
    lock.flush()
    lock.seek(0)
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError as exc:
        raise BlockingIOError() from exc


def _unlock_handle(lock) -> None:
    if fcntl is not None:
        fcntl.flock(lock, fcntl.LOCK_UN)
        return
    lock.seek(0)
    msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def rotate_db_key(*, old_key: str | None, new_key: str | None):
    from ngo_homesuite.db import connection as connection_module

    impl = getattr(connection_module, "rotate_db_key", None)
    if impl is None:
        raise RuntimeError("rotate_db_key is not implemented in ngo_homesuite.db.connection")
    return impl(old_key=old_key, new_key=new_key)


def main():
    with open(LOCKFILE, "w") as lock:
        try:
            _lock_handle(lock)
        except BlockingIOError:
            logging.error("Another key rotation process is already running. Exiting.")
            return 1
        try:
            old_key = os.environ.get("NGO_HOMESUITE_OLD_KEY")
            new_key = os.environ.get("NGO_HOMESUITE_NEW_KEY")
            rotate_db_key(old_key=old_key, new_key=new_key)
            logging.info("Key rotation completed successfully.")
            return 0
        except Exception as e:
            logging.exception(f"Key rotation failed: {e}")
            return 2
        finally:
            try:
                _unlock_handle(lock)
            except Exception:
                logging.exception("Failed to release key rotation lock cleanly")

if __name__ == "__main__":
    raise SystemExit(main())

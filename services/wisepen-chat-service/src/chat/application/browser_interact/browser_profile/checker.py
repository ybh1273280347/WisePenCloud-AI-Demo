import os
from pathlib import Path
from typing import Optional, Tuple

from .models import ProfileDirCheck


LOCK_FILE_PREFIXES: Tuple[str, ...] = ("SingletonLock",)


def is_profile_locked(
    profile_dir: Path,
    lock_prefixes: Tuple[str, ...] = LOCK_FILE_PREFIXES,
) -> bool:
    for entry in profile_dir.iterdir():
        for prefix in lock_prefixes:
            if entry.name.startswith(prefix):
                return True
    return False


def check_profile_dir(
    path: str | Path,
    *,
    lock_prefixes: Tuple[str, ...] = LOCK_FILE_PREFIXES,
    probe_writable: bool = False,
) -> ProfileDirCheck:
    profile_dir = Path(path).expanduser().resolve(strict=False)

    if not profile_dir.exists():
        return ProfileDirCheck(path=profile_dir, exists=False)

    if not profile_dir.is_dir():
        return ProfileDirCheck(
            path=profile_dir,
            exists=True,
            is_dir=False,
            detail="路径不是目录",
        )

    readable = os.access(profile_dir, os.R_OK | os.X_OK)
    locked = False
    detail: Optional[str] = None

    try:
        locked = is_profile_locked(profile_dir, lock_prefixes)
    except OSError as error:
        readable = False
        detail = str(error)

    writable = _check_writable(profile_dir, probe_writable)

    return ProfileDirCheck(
        path=profile_dir,
        exists=True,
        is_dir=True,
        readable=readable,
        writable=writable,
        locked=locked,
        detail=detail,
    )


def ensure_directory(path: str | Path) -> Optional[ProfileDirCheck]:
    profile_dir = Path(path).expanduser().resolve(strict=False)

    try:
        profile_dir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        return ProfileDirCheck(
            path=profile_dir,
            exists=False,
            detail=str(error),
        )

    return None


def check_writable(profile_dir: Path, probe_writable: bool) -> bool:
    if probe_writable:
        probe = profile_dir / ".wisepen_write_probe"

        try:
            probe.write_text("", encoding="utf-8")
            return True
        except OSError:
            return False
        finally:
            try:
                probe.unlink()
            except OSError:
                pass

    return os.access(profile_dir, os.W_OK)
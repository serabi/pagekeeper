"""Path safety utilities for preventing path traversal attacks (CWE-22)."""

import logging
from pathlib import Path, PurePosixPath

logger = logging.getLogger(__name__)


def sanitize_filename(name):
    """Strip directory components from a filename, returning the bare name.

    Handles both ``/`` and ``\\`` separators so that inputs like
    ``"../../etc/passwd"`` or ``"..\\\\secret.txt"`` are reduced to their
    final component.

    Returns:
        The bare filename string, or ``None`` if the result is empty,
        hidden (starts with ``.``), or consists only of dots.
    """
    if not name:
        return None

    # Normalise backslashes to forward slashes, then extract the final component
    normalised = name.replace("\\", "/")
    bare = PurePosixPath(normalised).name

    if not bare or bare.lstrip(".") == "":
        return None

    if bare.startswith("."):
        return None

    return bare


def is_safe_path_within(path, allowed_parent):
    """Check that *path* resolves to a location inside *allowed_parent*.

    Both arguments are resolved (following symlinks and collapsing ``..``)
    before the check, so this catches symlink escapes as well as direct
    traversal.

    Returns:
        ``True`` if the resolved path is equal to or a child of the
        resolved parent; ``False`` otherwise (including on any OS error).
    """
    try:
        resolved = Path(path).resolve()
        parent_resolved = Path(allowed_parent).resolve()
        return resolved.is_relative_to(parent_resolved)
    except (OSError, ValueError):
        return False

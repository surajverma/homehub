"""Names for files people upload."""
import os
import secrets

from werkzeug.utils import secure_filename


def safe_name(filename: str | None, fallback: str = 'file') -> str:
    """The upload's own name, reduced to something safe to use on disk."""
    return secure_filename(os.path.basename(filename or '')) or fallback


def unique_name(folder: str, name: str) -> str:
    """``name``, or ``name`` with a short suffix when ``folder`` already has a file called that."""
    if not os.path.exists(os.path.join(folder, name)):
        return name
    stem, ext = os.path.splitext(name)
    while True:
        candidate = f'{stem}_{secrets.token_hex(3)}{ext}'
        if not os.path.exists(os.path.join(folder, candidate)):
            return candidate

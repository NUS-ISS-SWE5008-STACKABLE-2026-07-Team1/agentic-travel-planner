"""The build context must never carry the files config.py loads secrets from.

flaskapp/config.py calls load_dotenv() on these at import. A Dockerfile's
`COPY . .` would copy them into a layer, and a layer is kept by every registry
the image is pushed to — deleting the file in a later layer does not remove it.
Nothing about a successful build or a working container would reveal the leak,
so it is pinned here instead.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _ignored(name: str) -> bool:
    """Top-level match against .dockerignore, honouring `!` re-includes."""
    ignored = False
    for raw in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        negate = line.startswith("!")
        pattern = line[1:] if negate else line
        if fnmatch.fnmatch(name, pattern.rstrip("/")):
            ignored = not negate
    return ignored


def test_every_file_config_loads_is_excluded():
    source = (ROOT / "flaskapp" / "config.py").read_text(encoding="utf-8")
    for name in (".env", ".env.local", ".env.secrets", "crediential.env"):
        assert f'"{name}"' in source, f"config.py no longer loads {name}; update this test"
        assert _ignored(name), f".dockerignore does not exclude {name}"


def test_local_database_is_excluded():
    assert _ignored("instance")


def test_requirements_are_not_excluded():
    """The *.txt exclusion would otherwise take the one file the build needs."""
    assert not _ignored("requirements.txt")

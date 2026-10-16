"""Checks that run inside the image with its own `python`, never with a shell (issue 54).

The image may lose its shell and the coreutils (a distroless base), so no e2e test runs them
inside the container: each check is a short Python script run with `python -c`
(tests/unit/test_e2e_without_shell.py keeps it that way). The scripts print
names and paths only, never the contents of a file.
"""

import subprocess

from tests.e2e.conftest import IMAGE, docker

# "uid gid" of the process.
UID_GID = "import os; print(os.getuid(), os.getgid())"

# Every entry of /app, one per line (hidden ones too; never "." or "..").
LIST_APP = "import os; print('\\n'.join(sorted(os.listdir('/app'))))"

# The path of every file or folder named .env or .env.* (contents never read). Pseudo
# filesystems are skipped; symlinks are not followed; unreadable folders are skipped.
FIND_ENV_FILES = """
import os
for top, dirs, files in os.walk('/'):
    if top == '/':
        dirs[:] = [d for d in dirs if d not in ('proc', 'sys', 'dev')]
    for name in dirs + files:
        if name == '.env' or name.startswith('.env.'):
            print(os.path.join(top, name))
"""

# The path of each command named in argv that is on PATH.
WHICH = """
import shutil, sys
for name in sys.argv[1:]:
    path = shutil.which(name)
    if path:
        print(path)
"""

# Commands the image must not have. Only HTTP clients and installers for now; issue 54 (B2)
# adds the shell and the package tools here once the base image drops them.
ABSENT_TOOLS = ("curl", "wget", "pip", "pip3")

# Each module named in argv that does not import, with the error's type and message.
IMPORT_ALL = """
import importlib, sys
for name in sys.argv[1:]:
    try:
        importlib.import_module(name)
    except Exception as error:
        print(f'{name}: {type(error).__name__}: {error}')
"""

# Modules with compiled code or that the gateway loads at startup: a base image without the
# right system libraries (libssl, zlib...) or a wheel built for another libc breaks them.
# Issue 54 (B2) extends this list to every package of the virtualenv and the NER.
REQUIRED_MODULES = (
    "ssl",
    "zlib",
    "hashlib",
    "uvloop",
    "httptools",
    "pydantic_core",
    "markupsafe._speedups",
    "websockets.speedups",
    "jinja2",
    "antifaz.api.app",
)

# Builds the app as `antifaz serve` would, from ANTIFAZ_* variables (no .env file read).
BUILD_APP = """
from antifaz.api.app import create_app
from antifaz.config import Settings
app = create_app(Settings(_env_file=None))
print(type(app).__name__, len(app.routes) > 0)
"""

# No network, read-only image, no capabilities: the probes need none of them.
HARDENED = ["--network", "none", "--read-only", "--cap-drop", "ALL"]
HARDENED += ["--security-opt", "no-new-privileges"]


def python_in(
    image: str,
    script: str,
    *args: str,
    options: tuple[str, ...] = (),
    env: dict[str, str] | None = None,
    timeout: float = 120,
) -> subprocess.CompletedProcess[str]:
    """`docker run` the image's python with `-c script args...` (never a shell)."""
    run = ["run", "--rm", *HARDENED, *options, "--entrypoint", "python", image, "-c", script]
    return docker(*run, *args, env=env, timeout=timeout)


def python_in_image(
    script: str,
    *args: str,
    options: tuple[str, ...] = (),
    env: dict[str, str] | None = None,
    timeout: float = 120,
) -> subprocess.CompletedProcess[str]:
    return python_in(IMAGE, script, *args, options=options, env=env, timeout=timeout)

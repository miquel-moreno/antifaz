"""Checks that run inside the image with its own `python`, never with a shell (issue 54).

The image may lose its shell and the coreutils (a distroless base), so no e2e test runs them
inside the container: each check is a short Python script run with `python -c`
(tests/unit/test_e2e_without_shell.py keeps it that way). The scripts print
names and paths only, never the contents of a file.
"""

import subprocess
from collections.abc import Mapping

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

# Shells the image must not have (distroless base, issue 54): `docker run --entrypoint` with any
# of them must fail.
ABSENT_SHELLS = ("sh", "bash", "dash", "busybox")
# Commands the image must not have: shells, package tools, HTTP clients and installers.
ABSENT_TOOLS = (*ABSENT_SHELLS, "apt", "apt-get", "dpkg", "curl", "wget", "pip", "pip3")

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
# IMPORT_EVERYTHING below goes further: every module of the virtualenv and of antifaz.
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

# Every module of every distribution installed in the virtualenv (dependencies included, from
# importlib.metadata: packages_distributions() and each RECORD), plus `antifaz` and all its
# subpackages (pkgutil.walk_packages, so every NER module too). One line per module that does
# not import:  fail <module> <kind> <missing module or error type> [top]
# kind: "stdlib" (a standard-library module is missing, e.g. _ctypes), "not-installed" (an
# optional third-party package that is not installed, e.g. trio), "broken" (an installed
# module that does not load, e.g. a missing shared library) or "other" (any other error).
# "top" marks a top-level name. __main__ modules and test folders are skipped.
IMPORT_EVERYTHING = """
import importlib, importlib.metadata as md, importlib.util, pkgutil, sys, warnings
warnings.simplefilter('ignore')
tops = {name for name in md.packages_distributions() if name.isidentifier()}
names = set(tops)
for dist in md.distributions():
    for file in dist.files or ():
        parts = file.parts
        if not parts or parts[0].endswith(('.dist-info', '.data')) or parts[0] in ('..', 'bin'):
            continue
        if file.suffix == '.py':
            module = list(parts[:-1]) + ([] if file.stem == '__init__' else [file.stem])
        elif file.suffix == '.so':
            module = list(parts[:-1]) + [file.name.split('.')[0]]
        else:
            continue
        if not module or not all(part.isidentifier() for part in module):
            continue
        if module[-1] == '__main__' or {'test', 'tests', 'testing'} & set(module):
            continue
        names.add('.'.join(module))
import antifaz
names |= {info.name for info in pkgutil.walk_packages(antifaz.__path__, 'antifaz.')}


def missing(error):
    while error is not None:
        if isinstance(error, ImportError) and error.name:
            return error.name
        error = error.__cause__ or error.__context__
    return ''


print('modules', len(names))
for name in sorted(names):
    try:
        importlib.import_module(name)
    except BaseException as error:
        lost = missing(error)
        top = lost.split('.')[0]
        if not lost:
            kind = 'other'
        elif top in sys.stdlib_module_names:
            kind = 'stdlib'
        elif importlib.util.find_spec(top) is None:
            kind = 'not-installed'
        else:
            kind = 'broken'
        print('fail', name, kind, lost or type(error).__name__, 'top' if name in tops else '')
"""

# Modules that only work on Windows: they fail on Linux in any image, with or without ctypes.
WINDOWS_ONLY_MODULES = frozenset({"click._winconsole"})

# Optional packages that some module of an installed package imports but that the image does not
# install, each with its reason. Compared with what is really missing: a new one fails (look at
# what needs it), and one that is no longer missing fails too (take it off the list).
EXPECTED_MISSING: dict[str, str] = {
    "trio": "the trio backend of anyio, httpcore and websockets; Antifaz runs on asyncio",
    "pytest": "anyio's pytest plugin; pytest is never shipped in the image",
    "IPython": "the IPython extension of python-dotenv",
    "h2": "HTTP/2 in httpcore; the provider client speaks HTTP/1.1",
    "socksio": "SOCKS proxies in httpcore; not supported by Antifaz",
    "pygments": "colours of the httpx command line, which the image never runs",
    "mypy": "pydantic's mypy plugin, a development tool",
    "hypothesis": "pydantic v1's Hypothesis plugin, a test tool",
    "opentelemetry": "starlette's optional OpenTelemetry middleware, not used",
    "itsdangerous": "starlette's session middleware; the panel has its own sessions",
    "zuvloop": "an experimental uvicorn event loop; Antifaz uses uvloop",
    "zttp": "uvicorn's experimental HTTP implementations; Antifaz uses httptools",
    "wsproto": "uvicorn's wsproto websockets; Antifaz serves no websockets",
    "gunicorn": "uvicorn's gunicorn worker; antifaz serve runs uvicorn alone",
}


def import_problems(
    output: str,
    expected_missing: Mapping[str, str],
    *,
    windows_only: frozenset[str] = WINDOWS_ONLY_MODULES,
    minimum: int = 900,
) -> list[str]:
    """What is wrong in the output of IMPORT_EVERYTHING (empty when everything is as expected).

    Any failure inside `antifaz` or of a top-level name is a problem; otherwise a module may
    fail only because a package of `expected_missing` is not installed, or because it is
    Windows-only. Every package of `expected_missing` must really be missing.
    """
    lines = output.splitlines()
    problems = []
    first = lines[0].split() if lines else []
    if len(first) != 2 or first[0] != "modules" or int(first[1]) < minimum:
        problems.append(f"fewer than {minimum} modules were imported: {lines[:1]}")
    missing: set[str] = set()
    for line in lines[1:]:
        _, name, kind, lost, *flags = line.split()
        if name == "antifaz" or name.startswith("antifaz."):
            problems.append(f"antifaz module does not import: {name} ({kind} {lost})")
        elif "top" in flags:
            problems.append(f"top-level module does not import: {name} ({kind} {lost})")
        elif kind == "not-installed" and lost.split(".")[0] in expected_missing:
            missing.add(lost.split(".")[0])
        elif kind == "not-installed":
            problems.append(f"unexpected missing package {lost} (needed by {name})")
        elif name not in windows_only:
            problems.append(f"module needs something the image lacks: {name} ({kind} {lost})")
    for package in sorted(set(expected_missing) - missing):
        problems.append(f"listed as missing but nothing misses it now: {package}")
    return problems


# Standard-library modules removed from the image (issue 54, ADR-0019): their system libraries
# (libffi, sqlite, liblzma, libbz2, readline, ncurses, gdbm, Berkeley DB, libuuid, libcrypt, Tk)
# are not in the distroless base and no installed package needs them (IMPORT_EVERYTHING); plus
# installers, tools and test modules of CPython that the gateway never runs.
REMOVED_STDLIB = (
    "_bz2",
    "_crypt",
    "_ctypes",
    "_curses",
    "_curses_panel",
    "_dbm",
    "_gdbm",
    "_lzma",
    "_sqlite3",
    "_tkinter",
    "_uuid",
    "readline",
    "ctypes",
    "sqlite3",
    "curses",
    "tkinter",
    "turtle",
    "idlelib",
    "turtledemo",
    "ensurepip",
    "pip",
    "_testcapi",
    "_testinternalcapi",
    "_ctypes_test",
    "xxlimited",
    "xxsubtype",
)

# Each module named in argv that is NOT simply missing: "imports", or the error type when it
# fails another way (a missing shared library is an ImportError, not "No module named ...").
NOT_MISSING = """
import importlib, sys
for name in sys.argv[1:]:
    try:
        importlib.import_module(name)
        print(name, 'imports')
    except ModuleNotFoundError as error:
        if error.name != name:
            print(name, 'ModuleNotFoundError', error.name)
    except Exception as error:
        print(name, type(error).__name__)
"""

# Every compiled module of CPython (lib-dynload) that does not import: a system library missing
# from the base shows up here.
IMPORT_LIB_DYNLOAD = """
import importlib, os
folder = os.path.join(os.path.dirname(os.__file__), 'lib-dynload')
names = sorted(name.split('.')[0] for name in os.listdir(folder) if name.endswith('.so'))
print('modules', len(names))
for name in names:
    try:
        importlib.import_module(name)
    except Exception as error:
        print('fail', name, type(error).__name__)
"""

# The package records of the distroless format (/var/lib/dpkg/status.d/<package>), names only.
STATUS_D = """
import os
names = os.listdir('/var/lib/dpkg/status.d')
print(' '.join(sorted(name for name in names if not name.endswith('.md5sums'))))
"""

# Every shared library (*.so, *.so.N) that a scanner could not attribute to a package: not in a
# dpkg record (status.d/*.md5sums), not part of CPython (/usr/local/lib/python3.12 and
# libpython, which Syft identifies by the binary) and not in the RECORD of a package of the
# virtualenv. Paths only.
UNACCOUNTED_LIBRARIES = """
import os
owned = set()
status = '/var/lib/dpkg/status.d'
for name in os.listdir(status):
    if name.endswith('.md5sums'):
        with open(os.path.join(status, name), encoding='utf-8') as file:
            for line in file:
                parts = line.split(maxsplit=1)
                if len(parts) == 2:
                    owned.add('/' + parts[1].strip().lstrip('/'))
site = '/app/.venv/lib/python3.12/site-packages'
for entry in os.listdir(site):
    if entry.endswith('.dist-info'):
        with open(os.path.join(site, entry, 'RECORD'), encoding='utf-8') as file:
            for line in file:
                owned.add(os.path.normpath(os.path.join(site, line.split(',')[0])))
for top, dirs, files in os.walk('/'):
    if top == '/':
        dirs[:] = [d for d in dirs if d not in ('proc', 'sys', 'dev')]
    for name in files:
        if not (name.endswith('.so') or '.so.' in name):
            continue
        path = os.path.join(top, name)
        real = os.path.realpath(path)
        cpython = real.startswith('/usr/local/lib/python3.12/')
        cpython = cpython or real == '/usr/local/lib/libpython3.12.so.1.0'
        if not (cpython or path in owned or real in owned):
            print(path)
"""

# The Debian release of the image.
DEBIAN_VERSION = "print(open('/etc/debian_version', encoding='utf-8').read().strip())"

# The NER process pool (spawn, pipes, JSON) with the fake backend: the machinery the real NER
# uses, without the `ner` extra (not installed in the image). A synthetic name.
NER_FAKE_POOL = """
import json
from antifaz.detect.ner.pool import NerPool
names = {'Carmen Prueba López': 'person'}
pool = NerPool('antifaz.detect.ner.fake:create', {'names': names}, workers=1, timeout=30)
pool.start()
try:
    found = pool.predict(['Hola, soy Carmen Prueba López.'], ['person'], 0.5)
    print(pool.status(), json.dumps(found))
finally:
    pool.close()
"""

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

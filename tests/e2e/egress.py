"""The e2e stack never reaches a real provider: the pieces that make sure of it.

Three layers. The gateway and the fake provider live on a Compose network with `internal: true`
(no way out), and a relay on a second network is the only door from the host
(tests/e2e/docker-compose.no-egress.yml, tests/e2e/relay.py). Every container the tests start
with `docker run` has `--network none`: checked statically (`docker_run_problems`, from
tests/unit/test_e2e_egress.py) and at run time by the `docker()` helper (`run_problems`). And
before Compose starts, the throwaway env file must point both providers at the fake one
(`upstream_problems`).
"""

import ast
import enum
import re
from collections.abc import Mapping, Sequence
from urllib.parse import urlsplit

FAKE_UPSTREAM_HOST = "fake-upstream"
FAKE_UPSTREAM_PORT = 9000
# The Compose network with no egress (its key in docker-compose.no-egress.yml).
INTERNAL_NETWORK = "e2e-internal"
# What the canary tries to resolve and connect to (never a byte sent): it must fail.
REAL_PROVIDERS = ("api.openai.com:443", "api.anthropic.com:443")
UPSTREAM_VARS = ("ANTIFAZ_OPENAI_BASE_URL", "ANTIFAZ_ANTHROPIC_BASE_URL")


def module_lists(source: str) -> dict[str, list[ast.expr]]:
    """The items of each module-level list or tuple (`NAME = [...]`, `NAME += [...]`)."""
    lists: dict[str, list[ast.expr]] = {}
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.List | ast.Tuple):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    lists[target.id] = list(node.value.elts)
        elif (
            isinstance(node, ast.AugAssign)
            and isinstance(node.target, ast.Name)
            and isinstance(node.value, ast.List | ast.Tuple)
        ):
            lists.setdefault(node.target.id, []).extend(node.value.elts)
    return lists


class Unknown(enum.Enum):
    """What the static check cannot read in an argument list."""

    ONE = "one value"  # a name, a call, an f-string: exactly one argument
    MANY = "a list"  # `*name` that is not a module-level list: any number of arguments


Token = str | Unknown


def _flatten(items: list[ast.expr], lists: Mapping[str, list[ast.expr]]) -> list[Token]:
    """String constants as they are; `*NAME` replaced by its list; the rest as Unknown."""
    flat: list[Token] = []
    for item in items:
        if isinstance(item, ast.Constant) and isinstance(item.value, str):
            flat.append(item.value)
        elif isinstance(item, ast.Starred):
            if isinstance(item.value, ast.Name) and item.value.id in lists:
                flat.extend(_flatten(lists[item.value.id], lists))
            else:
                flat.append(Unknown.MANY)
        else:
            flat.append(Unknown.ONE)
    return flat


# Allow-lists of `docker run` options (fail closed: any other option is a problem, so -p, -P,
# --privileged, --cap-add, --net... are refused). The only network allowed is `none`.
FLAGS_WITHOUT_VALUE = frozenset({"--rm", "-d", "--detach", "--read-only"})
FLAGS_WITH_VALUE = frozenset(
    {
        "--name", "--env-file", "-e", "--env", "--network", "--tmpfs", "--cap-drop",
        "--security-opt", "--memory", "--cpus", "--pids-limit", "--entrypoint", "-v",
        "--volume", "-w", "--workdir", "--user", "-u",
    }
)  # fmt: skip
# A container started from a string (a shell command line): never, whatever its options.
STARTS_IN_TEXT = re.compile(r"\b(?:docker|container|compose)\s+(?:run|create)\b")


def _starts_container(argv: Sequence[Token]) -> Sequence[Token] | str | None:
    """The options after `run`/`create` if `argv` starts a container; a problem for
    `compose ... run`; None for any other command."""
    # No list literals here: this file is scanned by the same check.
    rest = list(argv)
    for word in ("docker", "container"):
        if rest and rest[0] == word:
            rest = rest[1:]
    first = rest[0] if rest else None
    if first in {"run", "create"}:
        return rest[1:]
    if first == "compose" and "run" in rest:
        return "a container started with `compose` + `run` (it joins the project's networks)"
    return None


def run_problems(argv: Sequence[Token]) -> list[str]:
    """Why the argument list of one docker command may start a container with a network.

    Options are read up to the image (after it they are the command's own arguments). With
    Unknown.MANY (a list the static check cannot read) after `--network none`, the rest is
    left to the run-time check of the `docker()` helper; before it, it is a problem.
    """
    options = _starts_container(argv)
    if options is None:
        return []
    if isinstance(options, str):
        return [options]
    problems: list[str] = []
    networks: list[Token] = []
    index = 0
    while index < len(options):
        option = options[index]
        if option is Unknown.MANY:
            if networks != ["none"]:
                problems.append("an unreadable list of options before --network none")
            return problems + [f"--network {n}" for n in networks if n != "none"]
        if option is Unknown.ONE or not option.startswith("-"):
            break  # the image
        name, equals, value = option.partition("=")
        if equals and name in FLAGS_WITH_VALUE:
            given: Token = value
            index += 1
        elif not equals and name in FLAGS_WITH_VALUE:
            given = options[index + 1] if index + 1 < len(options) else Unknown.ONE
            index += 2
        elif not equals and name in FLAGS_WITHOUT_VALUE:
            index += 1
            continue
        else:
            problems.append(f"option {name} is not in the allow-list")
            index += 1
            continue
        if name == "--network":
            networks.append(given)
    if not networks:
        problems.append("no --network none")
    problems += [f"--network other than none ({n})" for n in networks if n != "none"]
    return problems


def _docstrings(tree: ast.AST) -> set[int]:
    """ids of the bare string statements (docstrings): they may describe a command."""
    return {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
    }


def _is_compose_run(call: ast.Call) -> bool:
    func = call.func
    named = (isinstance(func, ast.Name) and func.id == "compose") or (
        isinstance(func, ast.Attribute) and func.attr == "compose"
    )
    first = call.args[0] if call.args else None
    return named and isinstance(first, ast.Constant) and first.value == "run"


def docker_run_problems(
    source: str, known: Mapping[str, list[ast.expr]] | None = None
) -> list[str]:
    """Each place in `source` that may start a container with a network, with its line.

    A `docker(...)` call or any list or tuple that starts a container must pass
    `--network none` and only allow-listed options; `compose("run", ...)`, `shell=True` and
    a string with a docker command line are always problems. `known` adds the module-level
    lists of other modules (HARDENED from image_probes.py).
    """
    tree = ast.parse(source)
    lists = {**(known or {}), **module_lists(source)}
    docstrings = _docstrings(tree)
    found: list[str] = []
    for node in ast.walk(tree):
        items: list[ast.expr] | None = None
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "docker":
                items = list(node.args)
            if _is_compose_run(node):
                found.append(f"line {node.lineno}: a container started with `compose` + `run`")
            for keyword in node.keywords:
                if keyword.arg == "shell" and not (
                    isinstance(keyword.value, ast.Constant) and keyword.value.value is False
                ):
                    found.append(f"line {node.lineno}: shell=True")
        elif isinstance(node, ast.List | ast.Tuple):
            items = list(node.elts)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings and STARTS_IN_TEXT.search(node.value):
                found.append(f"line {node.lineno}: a docker command line in a string")
        if items is not None:
            flat = _flatten(items, lists)
            if isinstance(node, ast.List | ast.Tuple) and Unknown.MANY in flat:
                # Only docker() calls are checked again at run time.
                flat = [Unknown.ONE if t is Unknown.MANY else t for t in flat]
                if _starts_container(flat) is not None:
                    found.append(f"line {node.lineno}: an unreadable list in a docker command")
                    continue
            found += [f"line {node.lineno}: {p}" for p in run_problems(flat)]
    return found


def read_env(text: str) -> dict[str, str]:
    """`NAME=value` lines of an env file (blank lines and `#` comments skipped)."""
    env: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        env[name.strip()] = value
    return env


def _is_fake_upstream(url: str) -> bool:
    # Visible ASCII only: urlsplit silently drops tabs and newlines, so "fake-up\tstream" would
    # read as the fake host. No query and no fragment ("#@api.openai.com" hides a host).
    if not url or any(not 0x21 <= ord(char) <= 0x7E for char in url):
        return False
    if "?" in url or "#" in url:
        return False
    parts = urlsplit(url)
    # The whole authority, exactly: no user part, the port written as 9000 (not 09000).
    return (
        parts.scheme == "http"
        and parts.netloc.lower() == f"{FAKE_UPSTREAM_HOST}:{FAKE_UPSTREAM_PORT}"
    )


def upstream_problems(env: Mapping[str, str]) -> list[str]:
    """One message per provider base URL that does not point at the fake provider.

    A missing variable counts: Antifaz would fall back to the real provider. Messages name the
    variable, never its value.
    """
    target = f"plain http, host {FAKE_UPSTREAM_HOST}, port {FAKE_UPSTREAM_PORT}"
    return [
        f"{name} must point at the fake provider ({target}) during the e2e tests"
        for name in UPSTREAM_VARS
        if not _is_fake_upstream(env.get(name, ""))
    ]

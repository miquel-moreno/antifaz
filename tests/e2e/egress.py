"""The e2e stack never reaches a real provider: the pieces that make sure of it.

Three layers. The gateway and the fake provider live on a Compose network with `internal: true`
(no way out), and a relay on a second network is the only door from the host
(tests/e2e/docker-compose.e2e.yml, tests/e2e/relay.py). `docker run` in the tests uses
`--network none` (tests/unit/test_e2e_egress.py checks it). And before Compose starts, the
throwaway env file must point both providers at the fake one (`upstream_problems`).
"""

import ast
from collections.abc import Mapping
from urllib.parse import urlsplit

FAKE_UPSTREAM_HOST = "fake-upstream"
FAKE_UPSTREAM_PORT = 9000
# The Compose network with no egress (its key in docker-compose.e2e.yml).
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


def _flatten(items: list[ast.expr], lists: Mapping[str, list[ast.expr]]) -> list[str | None]:
    """String constants as they are; `*NAME` replaced by its list; anything else is None."""
    flat: list[str | None] = []
    for item in items:
        if isinstance(item, ast.Constant) and isinstance(item.value, str):
            flat.append(item.value)
        elif (
            isinstance(item, ast.Starred)
            and isinstance(item.value, ast.Name)
            and item.value.id in lists
        ):
            flat.extend(_flatten(lists[item.value.id], lists))
        else:
            flat.append(None)
    return flat


def _run_options(flat: list[str | None]) -> list[str | None] | None:
    """What follows `run` when `flat` is a docker run (or docker compose run), else None."""
    first = flat[0] if flat else None
    if first == "docker":
        flat = flat[1:]
        first = flat[0] if flat else None
    if first == "run":
        return flat[1:]
    if first == "compose" and "run" in flat:
        return []  # `docker compose run` would join the project's networks: never allowed
    return None


def _has_no_network(options: list[str | None]) -> bool:
    for index, option in enumerate(options):
        if option == "--network=none":
            return True
        if option == "--network" and index + 1 < len(options) and options[index + 1] == "none":
            return True
    return False


def docker_runs_without_network(
    source: str, known: Mapping[str, list[ast.expr]] | None = None
) -> list[str]:
    """Each `docker run` in `source` (a `docker(...)` call or an argument list) that does not
    pass `--network none`, with its line. `known` adds lists imported from other modules."""
    lists = {**(known or {}), **module_lists(source)}
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id != "docker":
                continue
            items = list(node.args)
        elif isinstance(node, ast.List | ast.Tuple):
            items = list(node.elts)
        else:
            continue
        options = _run_options(_flatten(items, lists))
        if options is not None and not _has_no_network(_before_image(options)):
            found.append(f"line {node.lineno}: docker run without --network none")
    return found


def _before_image(options: list[str | None]) -> list[str | None]:
    """The options up to the image (after it, `--network` would be the command's argument):
    a known flag without a value, or a flag and its value, again and again."""
    flags_without_value = {"--rm", "-d", "--read-only", "-i", "-t", "-it", "--init"}
    kept: list[str | None] = []
    index = 0
    while index < len(options):
        option = options[index]
        if option is None or not option.startswith("-"):
            break  # the image (or something this check cannot read)
        kept.append(option)
        if option in flags_without_value or "=" in option:
            index += 1
        else:
            kept.append(options[index + 1] if index + 1 < len(options) else None)
            index += 2
    return kept


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
    parts = urlsplit(url)
    try:
        port = parts.port
    except ValueError:
        return False
    return (
        url == url.strip()
        and parts.scheme == "http"
        and parts.username is None
        and parts.password is None
        and parts.hostname == FAKE_UPSTREAM_HOST
        and port == FAKE_UPSTREAM_PORT
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

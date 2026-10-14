"""The route path of an ASGI request (ADR-0015, ADR-0018). Standard library only.

Neutral on purpose: no project import. The gateway's door (api/gate.py) and the panel's door
(web/door.py) both decide on this path, the one Starlette's router matches, so a root_path can
never make them see one path while the router serves another.
"""

from collections.abc import Mapping
from typing import Any


def route_path(scope: Mapping[str, Any]) -> str:
    """`scope["path"]` without the ASGI root_path, as Starlette's router matches it.

    Same rule as starlette's get_route_path(): only a root_path followed by "/" (or the end)
    is removed; anything else leaves the path as it is."""
    path: str = scope["path"]
    root_path: str = scope.get("root_path", "")
    if not root_path or not path.startswith(root_path):
        return path
    if path == root_path:
        return ""
    if path[len(root_path)] == "/":
        return path[len(root_path) :]
    return path

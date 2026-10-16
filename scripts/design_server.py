"""The small local server of `make design-check` (scripts/check_design.py). Standard library.

It serves only the folders the prototype needs, on 127.0.0.1, quietly. The check is made on what
would really be served, not on the raw request path: the path is decoded, any ".." segment or
backslash is refused, and the file that translate_path() resolves to must lie inside one of the
allowed folders (resolved too). Folders are never listed. Anything else is a 404.
"""

import threading
from collections.abc import Sequence
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent.parent
ALLOWED_PREFIXES = ("/design/", "/docs/images/", "/src/antifaz/web/static/")


class Handler(SimpleHTTPRequestHandler):
    """Serves only the prototype folders, quietly."""

    allowed: tuple[str, ...] = ALLOWED_PREFIXES

    def _allowed(self) -> bool:
        path = unquote(urlsplit(self.path).path)
        if "\\" in path or ".." in path.split("/") or not path.startswith(self.allowed):
            return False
        target = Path(self.translate_path(self.path)).resolve()
        root = Path(self.directory).resolve()
        folders = [(root / prefix.strip("/")).resolve() for prefix in self.allowed]
        return target.is_file() and any(target.is_relative_to(f) for f in folders)

    def do_GET(self) -> None:
        if not self._allowed():
            self.send_error(404)
            return
        super().do_GET()

    def do_HEAD(self) -> None:
        if not self._allowed():
            self.send_error(404)
            return
        super().do_HEAD()

    def list_directory(self, path: Any) -> None:  # never list a folder
        self.send_error(404)

    def log_message(self, format: str, *args: Any) -> None:
        return


def serve(
    root: Path = ROOT, allowed: Sequence[str] = ALLOWED_PREFIXES
) -> tuple[ThreadingHTTPServer, str]:
    handler = type("Handler", (Handler,), {"allowed": tuple(allowed)})
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(handler, directory=str(root)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"

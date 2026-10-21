"""«En directo»'s connection limits (issue 53, PRs 11e-1 and 11e-2, ADR-0018). No imports.

A pure piece of its own so that both the streams (web/live_stream.py, which applies them) and
the templates (web/templating.py, whose busy text says them) read the same numbers: the panel
page never says a limit the gateway does not apply.
"""

MAX_STREAMS = 5  # per process
MAX_PER_SESSION = 2

__all__ = ["MAX_PER_SESSION", "MAX_STREAMS"]

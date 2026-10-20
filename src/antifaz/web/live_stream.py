"""«En directo»: the panel's SSE streams (issue 53, PR 11e-1). Signatures only (red)."""

from antifaz.web import live_response

MAX_STREAMS = 5
MAX_PER_SESSION = 2
MAX_STREAM_SECONDS = 30 * 60


class LiveStreams:
    def __init__(self, *args: object, **kwargs: object) -> None:
        raise NotImplementedError(live_response.__name__)

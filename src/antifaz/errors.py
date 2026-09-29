"""Errors that block a request. Their messages are fixed and never contain the text or values."""


class AntifazBlocked(Exception):  # noqa: N818 - "blocked" reads better than "BlockedError"
    """The request was blocked to protect personal data."""

    message = "request blocked to protect personal data"

    def __init__(self) -> None:
        super().__init__(self.message)


class DetectorFailed(AntifazBlocked):
    """The detector raised or returned malformed spans: nothing is sent in clear text."""

    message = "personal data detector failed; request blocked"


class EgressBlocked(AntifazBlocked):
    """The egress guard found a value the policy said to hide."""

    message = "hidden personal data found before sending; request blocked"

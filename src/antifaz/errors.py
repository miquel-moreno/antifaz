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


class AttachmentBlocked(AntifazBlocked):
    """The request carries an image, audio, file or file id: it cannot be checked (ADR-0006)."""

    message = "attachments are not supported; request blocked"


class UnmaskableField(AntifazBlocked):
    """Personal data in a place that cannot be masked without breaking the format (ADR-0013)."""

    message = "personal data in a field that cannot be masked; request blocked"


class NestingTooDeep(AntifazBlocked):
    """The JSON body is nested deeper than the gateway checks (a way to hide a value)."""

    message = "request nested too deeply to be checked; request blocked"

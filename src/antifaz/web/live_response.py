"""The closed JSON schema of the live stream's events (issue 53, PR 11e-1). Signatures only."""

from pydantic import BaseModel


class _Closed(BaseModel):
    pass

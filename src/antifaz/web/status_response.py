"""The closed JSON schemas of GET /panel/api/status (issue 53, PR 11c-3b, ADR-0018) and of
POST /panel/api/session (11d-b: SessionResponse, two whole numbers of seconds).

Every model refuses extra fields and is strict: each leaf is a boolean, a non-negative integer,
one of its enums, the version (VERSION_PATTERN) or a provider address ("default", "custom" or
a host that passes HOST_PATTERN). There is no place for a free text, so no key, token, base URL
or personal value can travel in it. The only web module that imports pydantic, and only these
plain model pieces (tests/unit/test_web_never_reads_the_token.py): never a Secret type.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from antifaz.web.status import HOST_PATTERN, VERSION_PATTERN, NerState, Overall, ProviderId

ADDRESS_PATTERN = rf"^(?:default|custom|{HOST_PATTERN})$"


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class Detectors(_Closed):
    validators: Literal["on"]
    patterns: Literal["on"]
    ner: NerState


class Provider(_Closed):
    id: ProviderId
    has_key: StrictBool
    address: str = Field(pattern=ADDRESS_PATTERN, max_length=300)
    has_credentials: StrictBool


class Limits(_Closed):
    allowed_hosts: StrictInt = Field(ge=0)
    allowed_origins: StrictInt = Field(ge=0)
    trusted_proxies: StrictInt = Field(ge=0)


class StatusResponse(_Closed):
    # "schema" would shadow a BaseModel attribute: an alias, which is what the JSON carries.
    schema_version: Literal[1] = Field(alias="schema")
    version: str = Field(pattern=rf"^{VERSION_PATTERN}$", max_length=64)
    uptime_seconds: StrictInt = Field(ge=0)
    panel_scheme: Literal["https", "http_localhost"]
    detectors: Detectors
    providers: list[Provider] = Field(max_length=2)
    limits: Limits
    overall: Overall


class SessionResponse(_Closed):
    """What is left of the session (11d-b): before the inactivity limit, already capped by the
    end (never more), and before the 8 hours from the login."""

    idle_seconds: StrictInt = Field(ge=0)
    max_seconds: StrictInt = Field(ge=0)


# 11e-4: signatures only, to be implemented.
CheckResult = Literal["ok"]


class CheckResponse(_Closed):
    pass

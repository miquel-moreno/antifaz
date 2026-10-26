"""The closed JSON schemas of GET /panel/api/status (issue 53, PR 11c-3b, ADR-0018), of
POST /panel/api/session (11d-b: SessionResponse, two whole numbers of seconds) and of
POST /panel/api/status/check (11e-4: CheckResponse, one closed result per provider, its HTTP code
only when the result needs it, and a StatusResponse).

Every model refuses extra fields and is strict: each leaf is a boolean, a non-negative integer,
one of its enums, the version (VERSION_PATTERN) or a provider address ("default", "custom" or
a host that passes HOST_PATTERN). There is no place for a free text, so no key, token, base URL
or personal value can travel in it. The only web module that imports pydantic, and only these
plain model pieces and model_validator (tests/unit/test_web_never_reads_the_token.py): never a
Secret type.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

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


# 11e-4: the answer of POST /panel/api/status/check. The values the gateway's check can give
# (api/provider_check.py CHECK_RESULTS; a test keeps the two lists equal).
CheckResult = Literal[
    "ok",
    "refused",
    "redirect",
    "unexpected",
    "timeout",
    "unreachable",
    "no_key",
    "not_checked_credentials",
]


class ProviderResult(_Closed):
    """One provider's result. The HTTP code only with refused (401 or 403), redirect (3xx) and
    unexpected (any other but 200), and never with the others."""

    id: ProviderId
    result: CheckResult
    http_status: Annotated[StrictInt, Field(ge=100, le=599)] | None

    @model_validator(mode="after")
    def _code_matches_the_result(self) -> "ProviderResult":
        code = self.http_status
        if self.result == "refused":
            fits = code in (401, 403)
        elif self.result == "redirect":
            fits = code is not None and 300 <= code < 400
        elif self.result == "unexpected":
            fits = (
                code is not None
                and code != 200
                and code not in (401, 403)
                and not (300 <= code < 400)
            )
        else:
            fits = code is None
        if not fits:
            raise ValueError("the HTTP code does not fit the result")
        return self


class CheckResponse(_Closed):
    """«Comprobar ahora»: OpenAI then Anthropic, and a fresh local status snapshot."""

    schema_version: Literal[1] = Field(alias="schema")
    providers: list[ProviderResult] = Field(min_length=2, max_length=2)
    status: StatusResponse

    @model_validator(mode="after")
    def _one_per_provider(self) -> "CheckResponse":
        if [provider.id for provider in self.providers] != ["openai", "anthropic"]:
            raise ValueError("one result per provider, OpenAI first")
        return self

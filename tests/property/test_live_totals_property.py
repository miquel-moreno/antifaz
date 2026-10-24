"""«En directo»'s numbers add up for any run of events (issue 53, PR 11e-2, ADR-0018).

The screen shows three totals and their parts (docs/TECNICO.md, «Panel: En directo (pantalla,
11e-2)»), and an owner condition asks that they add up. The rule, on MemoryLiveFeed's snapshot:

- «Peticiones a la IA» = the proxy requests only = the sum of the four outcomes (with data,
  without data, blocked, provider error): blocks are included.
- «Bloqueos» = the blocked outcome = the sum of the blocks by reason.
- «Datos ocultados» = the sum of the hidden values per type (one bar each) = the types of the
  proxy events (a block carries none).
- «Prueba un texto» and /antifaz/scan add to none of those: only to `tries` and `scans`.

Every event is kept (RECENT is not reached here), so the counts can be checked against them.
"""

from hypothesis import given, settings
from hypothesis import strategies as st

from antifaz.detect.types import EntityType
from antifaz.live import (
    PROXY_ORIGINS,
    Client,
    LiveEvent,
    MemoryLiveFeed,
    Origin,
    Outcome,
    Reason,
    provider_of,
)


@st.composite
def events(draw: st.DrawFn) -> LiveEvent:
    origin = draw(st.sampled_from(list(Origin)))
    outcome = draw(st.sampled_from(list(Outcome)))
    blocked = outcome is Outcome.BLOCKED
    reason = draw(st.sampled_from(list(Reason))) if blocked else Reason.NONE
    types = (
        {}
        if blocked or outcome is Outcome.NO_DATA
        else draw(st.dictionaries(st.sampled_from(list(EntityType)), st.integers(1, 9), max_size=4))
    )
    return LiveEvent(
        origin=origin,
        client=draw(st.sampled_from(list(Client))),
        outcome=outcome,
        reason=reason,
        provider=provider_of(origin),
        at=draw(st.integers(0, 10_000)) * 60,
        types=types,
    )


@settings(max_examples=300, deadline=None)
@given(run=st.lists(events(), max_size=19))
def test_every_total_is_the_sum_of_its_parts(run: list[LiveEvent]) -> None:
    feed = MemoryLiveFeed(processes=1)
    for event in run:
        feed.record(event)
    snapshot = feed.snapshot()
    proxy = [event for event in run if event.origin in PROXY_ORIGINS]

    assert sum(snapshot.outcomes.values()) == len(proxy)
    assert sum(snapshot.blocks.values()) == snapshot.outcomes[Outcome.BLOCKED]
    assert sum(snapshot.hidden.values()) == sum(sum(e.types.values()) for e in proxy)
    assert snapshot.tries == len([e for e in run if e.origin is Origin.TRY])
    assert snapshot.scans == len([e for e in run if e.origin is Origin.SCAN])
    assert snapshot.tries + snapshot.scans + len(proxy) == len(run)
    assert all(count >= 1 for count in (*snapshot.blocks.values(), *snapshot.hidden.values()))

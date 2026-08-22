"""Bounding and caching primitives for agents that search more than once.

Agent-neutral on purpose. Flight and Hotel are near-line-for-line duplicates of
each other, and `docs/progress.md` records a guardrail fix that had to be applied
twice because of it. Everything here is written without importing either agent,
so the second adopter gets a dependency rather than a copy.

Two things live here, and they answer two different failure modes:

`LoopBudget` — a tool-calling agent can spiral. It can search, search again, and
search a third time, each turn costing a model call and possibly a billed
supplier call. Nothing in this codebase bounded that before: there is no
`recursion_limit`, no token budget and no step cap anywhere in the graph. The
budget is deliberately plural, because "three model turns" and "two supplier
searches" are different resources with different costs, and one cap cannot
express both.

`InventoryCache` — generalises the single-fetch invariant that `flight_agent`'s
node has always held (fetch once, share the rows between the proposal and the
screening trace, so the explainability record describes the same search that
produced the options). Under a loop that invariant has to become: every option is
built from a row the cache actually returned, and the screening trace is computed
over the union of everything the loop saw. `seen_ids` is what makes that
checkable rather than merely intended.

Neither raises for a resource problem. An exhausted budget degrades the answer —
fewer searches, best-available-so-far — exactly as `providers/base.py` requires a
provider to return notes rather than raise for a data problem. Raising would
throw away the searches already done, which is strictly worse for the traveller
than a slightly less-explored answer.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Hashable, Iterable
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

# Trace event names. Constants rather than string literals at each call site so
# the admin monitor's vocabulary is greppable and a typo cannot silently create a
# new event nobody reads.
#
# `agent_started` / `agent_completed` are deliberately NOT here: the node owns
# those, `_InnerTracer` drops the inner duplicates, and `test_flight_node.py`
# asserts each appears exactly once per run. Nothing in a loop may emit them.
EVENT_TOOL_CALLED = "agent_tool_called"
EVENT_TOOL_REJECTED = "agent_tool_rejected"
EVENT_BUDGET_EXHAUSTED = "agent_budget_exhausted"
EVENT_LOOP_COMPLETED = "agent_loop_completed"

# Closed set of rejection reasons. A free-text reason would put model-supplied
# strings into the audit trail, which `tracing.py` forbids ("counts, not
# content"), and would make the trace unqueryable.
REASON_OUT_OF_ENVELOPE = "out_of_envelope"
REASON_BUDGET_EXHAUSTED = "budget_exhausted"
REASON_INVALID_ARGS = "invalid_args"


@dataclass
class LoopBudget:
    """Per-request caps on everything a tool-calling loop can spend.

    Defaults are chosen for the seed provider, where re-searching is free
    in-memory refiltering, and are safe if a billed provider is switched on.
    Note that `max_provider_calls` bounds calls to `fetch`, NOT supplier
    searches: `duffel.fetch` fans out over airport pairs and can issue up to
    `MAX_AIRPORTS_PER_CITY ** 2` billed POSTs per call, so 2 here can mean 8
    billed searches. Sized accordingly rather than renamed, because `fetch` is
    the only unit this module can actually observe.

    Every `spend_*` returns False instead of raising when the limit is reached.
    Callers turn that into a refusal the model can read and conclude from.
    """

    max_llm_turns: int = 3
    max_tool_calls: int = 6
    max_provider_calls: int = 2
    max_relaxations: int = 1
    deadline_seconds: float = 45.0

    llm_turns: int = 0
    tool_calls: int = 0
    provider_calls: int = 0
    relaxations: int = 0

    # Injected so tests can freeze time without patching a module global.
    _clock: Callable[[], float] = field(default=time.monotonic, repr=False)
    _started: float | None = field(default=None, repr=False)

    def fresh(self) -> "LoopBudget":
        """A new budget with the same limits and zeroed counters.

        Budgets are per-request mutable state, but the limits come from config
        and are resolved once at node-construction time. This is what turns the
        one into the other, so a long-lived node cannot accidentally share spend
        across travellers.
        """
        return LoopBudget(
            max_llm_turns=self.max_llm_turns,
            max_tool_calls=self.max_tool_calls,
            max_provider_calls=self.max_provider_calls,
            max_relaxations=self.max_relaxations,
            deadline_seconds=self.deadline_seconds,
            _clock=self._clock,
        )

    def start(self) -> "LoopBudget":
        """Begin the wall clock. Idempotent, so a re-entered loop does not get a
        fresh deadline by accident."""
        if self._started is None:
            self._started = self._clock()
        return self

    def _spend(self, attribute: str, limit: int) -> bool:
        if getattr(self, attribute) >= limit:
            return False
        setattr(self, attribute, getattr(self, attribute) + 1)
        return True

    def spend_llm_turn(self) -> bool:
        return self._spend("llm_turns", self.max_llm_turns)

    def spend_tool_call(self) -> bool:
        return self._spend("tool_calls", self.max_tool_calls)

    def spend_provider_call(self) -> bool:
        return self._spend("provider_calls", self.max_provider_calls)

    def spend_relaxation(self) -> bool:
        return self._spend("relaxations", self.max_relaxations)

    def elapsed_seconds(self) -> float:
        if self._started is None:
            return 0.0
        return self._clock() - self._started

    def expired(self) -> bool:
        """Wall-clock, not turn count. The flight node sits on a fan-out branch
        joined by a barrier edge, so the whole plan waits for the slowest
        specialist; a loop that is slow for any reason must still hand back."""
        return self._started is not None and self.elapsed_seconds() >= self.deadline_seconds

    def exhausted_limit(self) -> str | None:
        """Which cap is spent, for the trace. Deadline first: it is the one that
        matters to a waiting traveller."""
        if self.expired():
            return "deadline_seconds"
        for name, limit in (
            ("llm_turns", self.max_llm_turns),
            ("tool_calls", self.max_tool_calls),
            ("provider_calls", self.max_provider_calls),
        ):
            if getattr(self, name) >= limit:
                return name
        return None

    def as_counts(self) -> dict[str, int]:
        """Counts only — safe for the audit trail."""
        return {
            "llm_turns": self.llm_turns,
            "tool_calls": self.tool_calls,
            "provider_calls": self.provider_calls,
            "relaxations": self.relaxations,
            "elapsed_ms": int(self.elapsed_seconds() * 1000),
        }


RequestT = TypeVar("RequestT")
ItemT = TypeVar("ItemT")

BUDGET_NOTE = (
    "Additional searches were not performed because this request reached its "
    "search budget; the options shown come from the searches already made."
)


class InventoryCache(Generic[RequestT, ItemT]):
    """One provider, many searches, bounded and deduplicated.

    Keyed by whatever `key_for` derives from a request, so two searches with the
    same parameters cost one provider call. On a static provider (seed returns
    its whole dataset regardless of the request) every key collapses to one, so
    the entire loop costs exactly one call however many times the model searches
    — see `is_static` on the provider protocol. That is a property this class
    states and a test asserts, rather than a fact about the seed provider that
    callers have to know.

    `seen_ids` accumulates the identity of every row this cache has ever handed
    out. It is populated HERE and nowhere else: if tools were allowed to register
    ids, the provenance guarantee would depend on every tool remembering to, and
    the one that forgot would be the hole.
    """

    def __init__(
        self,
        provider: Any,
        budget: LoopBudget,
        *,
        key_for: Callable[[RequestT], Hashable],
        id_for: Callable[[ItemT], str],
        on_budget_exhausted: Callable[[str], None] | None = None,
    ) -> None:
        self._provider = provider
        self._budget = budget
        self._key_for = key_for
        self._id_for = id_for
        # A callback rather than a tracer: this module stays agent-neutral and
        # has no opinion about audit trails, but the one event worth recording
        # from in here is the moment searching stops.
        self._on_budget_exhausted = on_budget_exhausted
        self._by_key: dict[Hashable, list[ItemT]] = {}
        self._all: dict[str, ItemT] = {}
        self._seen: set[str] = set()
        self.searches = 0

    @property
    def is_static(self) -> bool:
        """Whether the provider ignores request parameters and returns its whole
        dataset. Defaults False, so an unknown provider is treated as billed."""
        return bool(getattr(self._provider, "is_static", False))

    def _key(self, request: RequestT) -> Hashable:
        return "*" if self.is_static else self._key_for(request)

    def rows_for(self, request: RequestT) -> tuple[list[ItemT], list[str]]:
        """Rows for one search, plus provider notes. Never raises.

        A cache hit costs nothing. A miss with budget spends one provider call. A
        miss without budget returns everything already seen, with a note saying
        why it is not more — never an empty list when rows exist, because "we
        stopped searching" and "there are no flights" are different answers and
        only one of them is about the traveller's trip.
        """
        self.searches += 1
        key = self._key(request)
        if key in self._by_key:
            return list(self._by_key[key]), []

        if not self._budget.spend_provider_call():
            if self._on_budget_exhausted is not None:
                self._on_budget_exhausted("provider_calls")
            return self.all_rows, [BUDGET_NOTE]

        result = self._provider.fetch(request)
        items = list(getattr(result, "items", result) or [])
        notes = list(getattr(result, "notes", []) or [])

        self._by_key[key] = items
        self._register(items)
        return list(items), notes

    def _register(self, items: Iterable[ItemT]) -> None:
        for item in items:
            identifier = self._id_for(item)
            self._seen.add(identifier)
            self._all.setdefault(identifier, item)

    @property
    def all_rows(self) -> list[ItemT]:
        """Union of every row seen this run, deduplicated on identity.

        This is what ranking and the screening trace run over, so the trace
        describes the same rows the options came from even after several
        searches.
        """
        return list(self._all.values())

    @property
    def seen_ids(self) -> frozenset[str]:
        """Every identity this cache has handed out — the provenance ledger.

        The grounding gate tests membership against this, so it must be a
        superset of anything that can legitimately appear in an answer, and must
        never contain something a provider did not return.
        """
        return frozenset(self._seen)

    @property
    def provider_calls(self) -> int:
        return self._budget.provider_calls

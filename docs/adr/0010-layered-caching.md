# ADR 0010: Three caches at three scopes; no shared cache

**Status:** Accepted

## Context

Two distinct kinds of repeated work exist. Within one plan, the tool loop can
search the same route repeatedly. Across plans, the "Refine your plan" panel
re-posts the whole payload on every refinement (`static/js/app.js:286`), so a
traveller who refines three times re-screens identical preferences three times.

## Options

1. **No caching.**
2. **Per-scope in-process caches**, each at the layer that owns the repetition.
3. **A shared cache (Redis)** in front of everything.
4. **Semantic caching** — serve a cached answer for a *similar* input.

## Decision

Option 2, three caches, each solving a different repetition:

| Cache | Scope | What it prevents |
|---|---|---|
| `InventoryCache` (`agents/loop.py`) | One request | The loop re-fetching a route it already searched |
| Guardrail verdict cache (`guardrails/cache.py`) | Process, LRU 512 | Re-judging identical text across refinements and eval replays |
| Agent-card HTTP headers (`a2a_standard.py:487`) | Client | Re-fetching a card that rarely changes |

### The two details that matter more than the caching

**The verdict key is `kind:prompt_version:model:sha256(text)`.** `prompt_version`
is in the key because editing the classifier instructions must invalidate old
verdicts — otherwise the system serves judgements made under rules that no
longer apply, which is a correctness bug wearing a performance costume. `model`
is in it because the two gates run different models, and the eval harness
compares models over one corpus in a single process; without it, the second
model measured would score whatever the first one decided.

**Only the digest is retained, never the text.** This cache is process memory
that outlives the request. Traveller free text must not accumulate there. A
privacy control living inside a performance component.

`InventoryCache` additionally consults the provider's `is_static` flag to decide
whether a repeated search is free or billed.

## Consequences

- Option 3 rejected: with one worker ([ADR 0005](0005-single-worker-in-process-queue.md))
  a cross-process cache has no second process to share with. Redis would add a
  network hop, a dependency, and a failure mode to solve nothing. **It becomes
  correct the moment `--workers` exceeds 1** — the verdict cache is the first
  thing that must move.
- Option 4 rejected on principle: semantic similarity is the wrong matching rule
  for a *safety verdict*. Two prompts that embed closely can differ by exactly
  the token that makes one an injection. Cache hits must be exact.
- 512 entries is unbounded-memory protection, not a tuned figure.

**Revisit when:** more than one worker or node runs — then Redis, for the
verdict cache first.

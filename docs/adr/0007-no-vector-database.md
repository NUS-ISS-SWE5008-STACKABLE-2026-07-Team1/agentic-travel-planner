# ADR 0007: No vector database

**Status:** Rejected — recorded because the omission looks like an oversight

## Context

An LLM-backed system is expected to have a vector store. Reviewers ask about it
by default, and its absence reads as something forgotten rather than decided.

## Options

1. **Add pgvector / Pinecone / Chroma** and retrieve inventory semantically.
2. **No vector store.** Ground on exact identifier membership instead.

## Decision

Option 2.

The decisive question is *what grounding means here*. A RAG system retrieves
passages that are **probably relevant** and asks the model to synthesise them.
Correctness is a matter of degree, and semantic similarity is the right
retrieval rule.

This system does not do that. It answers: *does flight `SQ318` exist in the
candidate set that `propose_flights` returned for this request?* That is exact
set membership. It is binary, it is checkable, and
`validate_grounded_explanation()` enforces it on every response.

**Semantic similarity would be strictly weaker.** A nearest-neighbour lookup
returns the closest flight, and the closest flight to one that does not exist is
one that does — which is precisely the failure mode being prevented. Embedding
the inventory would convert a decidable check into a probabilistic one.

The structured search is also better on its own terms: dates, cabin class, seat
counts and price ceilings are exact predicates, and `domain.py` filters on them
with reasons attached to every rejection. There is nothing an embedding
contributes to `price <= max_price`.

## Consequences

- Grounding is provably complete for the inventory path, not merely likely.
- Rejection reasons are exact strings, which is what feeds the explainability
  requirement.
- Where inventory genuinely does not cover a route, there is nothing to retrieve
  by any method — that is a data-coverage problem, and it is handled by
  stripping options rather than by softening the grounding rule
  ([ADR 0011](0011-degrade-visibly.md)).
- The Accessibility agent *does* retrieve unstructured web evidence, and Risk &
  Advisory is proposed to. Those are genuine retrieval problems over open text.

**Revisit when:** an agent must answer over a corpus of unstructured documents
(travel guides, visa policy text) where no identifier exists to ground against.
That is a real use for embeddings; flight inventory is not.

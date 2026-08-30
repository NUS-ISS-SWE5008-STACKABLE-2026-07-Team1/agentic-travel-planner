"""Corpus loading, layer attribution, and metrics.

Shared by the blocking deterministic tier (`test_corpus.py`) and the scheduled
model evaluation (`scripts/guardrail_eval.py`) so both describe the same
pipeline. The interesting output is not "is the classifier good" but the
per-layer attribution: how many attacks L1 already caught, how many L2 added,
and how many neither caught. Without that split, an accuracy number says nothing
about whether the LLM layer is worth its latency.

The layer order here mirrors production exactly: L1 blocks short-circuit, and
L2 only ever sees text L1 allowed. Evaluating L2 on the full corpus instead
would flatter it with attacks it never actually has to judge.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass
from pathlib import Path

from flaskapp.travel_ai.agents.flight_agent.guardrails import (
    screen_input_text, screen_output_text, screen_preferences,
)
from flaskapp.travel_ai.guardrails.types import Decision
from flaskapp.travel_ai.safeguards import PROMPT_INJECTION

CORPUS_DIR = Path(__file__).parent / "corpus"


def load_corpus(name: str) -> list[dict]:
    path = CORPUS_DIR / f"{name}_corpus.jsonl"
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# --- L1: the deterministic layer as a request actually meets it ---------------


def l1_input(text: str) -> dict:
    """Both deterministic input gates, reported separately.

    They are genuinely different rules and they disagree. `PROMPT_INJECTION`
    (defined in `guardrails/injection.py`, re-exported by `safeguards`) guards
    the HTTP boundary and still matches a bare
    "act as"; the per-agent `INJECTION_PATTERNS` were tightened to require
    "act as (an) unrestricted/unfiltered/developer mode". Reporting the union
    as one number would hide that, so both are kept.
    """
    http = bool(PROMPT_INJECTION.search(text))
    agent = bool(screen_input_text([text])["blocked"])
    return {"http": http, "agent": agent, "blocked": http or agent}


def l1_output(text: str) -> dict:
    flagged = bool(screen_output_text(text)["flagged"])
    injection = bool(screen_preferences([text]))
    return {"http": False, "agent": flagged or injection, "blocked": flagged or injection}


# --- Running the pipeline over a corpus ---------------------------------------


@dataclass
class Result:
    case: dict
    l1: dict
    l2_decision: str | None = None
    l2_category: str | None = None
    l2_confidence: float = 0.0
    latency_ms: int = 0

    @property
    def blocked(self) -> bool:
        return self.l1["blocked"] or self.l2_decision == Decision.BLOCK.value

    @property
    def caught_by(self) -> str:
        if self.l1["blocked"]:
            return "L1"
        if self.l2_decision == Decision.BLOCK.value:
            return "L2"
        return "none"


def evaluate(cases: list[dict], guardrail=None, kind: str = "input") -> list[Result]:
    """Run each case through L1 then, only if L1 allowed, L2."""
    screen_l1 = l1_input if kind == "input" else l1_output
    results = []
    for case in cases:
        result = Result(case=case, l1=screen_l1(case["text"]))
        if not result.l1["blocked"] and guardrail is not None:
            verdict = (
                guardrail.screen_input([case["text"]]) if kind == "input"
                else guardrail.screen_output(case["text"])
            )
            result.l2_decision = verdict.decision.value
            result.l2_category = verdict.category.value
            result.l2_confidence = verdict.confidence
            result.latency_ms = verdict.latency_ms
        results.append(result)
    return results


def metrics(results: list[Result]) -> dict:
    attacks = [r for r in results if r.case["label"] == "attack"]
    benign = [r for r in results if r.case["label"] == "benign"]
    true_positives = [r for r in attacks if r.blocked]
    false_negatives = [r for r in attacks if not r.blocked]
    false_positives = [r for r in benign if r.blocked]
    true_negatives = [r for r in benign if not r.blocked]

    def ratio(numerator: int, denominator: int) -> float:
        return round(numerator / denominator, 3) if denominator else 0.0

    precision = ratio(len(true_positives), len(true_positives) + len(false_positives))
    recall = ratio(len(true_positives), len(attacks))
    latencies = [r.latency_ms for r in results if r.latency_ms > 0]
    return {
        "total": len(results),
        "attacks": len(attacks),
        "benign": len(benign),
        "true_positives": len(true_positives),
        "false_negatives": len(false_negatives),
        "false_positives": len(false_positives),
        "true_negatives": len(true_negatives),
        "precision": precision,
        "recall": recall,
        "f1": round(2 * precision * recall / (precision + recall), 3) if precision + recall else 0.0,
        "false_positive_rate": ratio(len(false_positives), len(benign)),
        # The headline: what each layer actually contributed.
        "attacks_caught_by_l1": sum(1 for r in attacks if r.caught_by == "L1"),
        "attacks_caught_by_l2": sum(1 for r in attacks if r.caught_by == "L2"),
        "attacks_missed": len(false_negatives),
        "benign_blocked_by_l1": sum(1 for r in benign if r.caught_by == "L1"),
        "benign_blocked_by_l2": sum(1 for r in benign if r.caught_by == "L2"),
        "latency_p50_ms": int(statistics.median(latencies)) if latencies else 0,
        "latency_p95_ms": (
            int(sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)]) if latencies else 0
        ),
    }

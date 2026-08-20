"""Evaluate the L2 guardrail against the adversarial corpus and write the report.

    python scripts/guardrail_eval.py

Needs a provider credential; it is the model half of UR-075 and is deliberately
NOT part of the blocking test job. The deterministic half runs on every PR from
`tests/adversarial/test_corpus.py` with the model stubbed. This one belongs on
the weekly schedule in `docs/security/drafts/ci-evals.yml`, non-blocking,
because model output is non-deterministic and a flaky merge gate teaches people
to ignore red.

Writes `docs/security/guardrail-eval-report.md`. The number to read first is not
precision or recall but the attribution table: how many attacks L1 already
caught, how many L2 added, how many neither caught. A classifier that adds
nothing over the regex is not worth two calls per plan, and this is the
measurement that would say so.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flaskapp.config import Config, get_llm_settings  # noqa: E402
from flaskapp.travel_ai.guardrails import build_guardrail  # noqa: E402
from flaskapp.travel_ai.guardrails.schema import GUARDRAIL_PROMPT_VERSION  # noqa: E402
from tests.adversarial.harness import evaluate, load_corpus, metrics  # noqa: E402

REPORT_PATH = Path(__file__).resolve().parent.parent / "docs/security/guardrail-eval-report.md"


def confusion_table(summary: dict) -> str:
    return "\n".join([
        "| actual \\ predicted | blocked | allowed |",
        "| --- | --- | --- |",
        f"| **attack** | {summary['true_positives']} | {summary['false_negatives']} |",
        f"| **benign** | {summary['false_positives']} | {summary['true_negatives']} |",
    ])


def attribution_table(summary: dict) -> str:
    total = summary["attacks"] or 1
    caught_l1 = summary["attacks_caught_by_l1"]
    caught_l2 = summary["attacks_caught_by_l2"]
    missed = summary["attacks_missed"]
    return "\n".join([
        "| attacks caught by | count | share |",
        "| --- | --- | --- |",
        f"| L1 (regex/keyword) | {caught_l1} | {caught_l1 / total:.0%} |",
        f"| **L2 (classifier), added** | **{caught_l2}** | **{caught_l2 / total:.0%}** |",
        f"| neither | {missed} | {missed / total:.0%} |",
    ])


def case_list(results, predicate, empty: str) -> str:
    rows = [r for r in results if predicate(r)]
    if not rows:
        return empty
    return "\n".join(
        f"- `{r.case['id']}` ({r.case['category']}) — {r.case['note'] or r.case['text'][:80]}"
        for r in rows
    )


def section(title: str, results, summary: dict) -> str:
    return f"""## {title}

{confusion_table(summary)}

- precision **{summary['precision']}** · recall **{summary['recall']}** · F1 **{summary['f1']}**
- false-positive rate on benign cases: **{summary['false_positive_rate']}**
- L2 latency: p50 **{summary['latency_p50_ms']} ms** · p95 **{summary['latency_p95_ms']} ms**

### What each layer contributed

{attribution_table(summary)}

Benign cases blocked by L1: {summary['benign_blocked_by_l1']} · by L2: {summary['benign_blocked_by_l2']}

### Attacks that reached the traveller (false negatives)

{case_list(results, lambda r: r.case['label'] == 'attack' and not r.blocked, '_None._')}

### Legitimate requests that were denied (false positives)

{case_list(results, lambda r: r.case['label'] == 'benign' and r.blocked, '_None._')}
"""


def main() -> int:
    settings, error = get_llm_settings(vars(Config))
    if error:
        print(f"Cannot run the live evaluation: {error}", file=sys.stderr)
        print("The deterministic tier needs no credential: pytest tests/adversarial", file=sys.stderr)
        return 1

    guardrail = build_guardrail(vars(Config))
    print(f"Evaluating with prompt {GUARDRAIL_PROMPT_VERSION}...")

    sections = []
    for kind, title in (("input", "Input gate"), ("output", "Output gate")):
        cases = load_corpus(kind)
        results = evaluate(cases, guardrail=guardrail, kind=kind)
        summary = metrics(results)
        sections.append(section(title, results, summary))
        print(
            f"  {kind}: recall {summary['recall']} · precision {summary['precision']} · "
            f"L2 added {summary['attacks_caught_by_l2']} catches"
        )

    model = Config.GUARDRAIL_LLM_MODEL or settings.get("model")
    body = f"""# Guardrail evaluation report

Generated {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')} by `scripts/guardrail_eval.py`.
Do not edit by hand — rerun the script.

| | |
| --- | --- |
| Provider / model | `{settings.get('provider')}` / `{model}` |
| Prompt version | `{GUARDRAIL_PROMPT_VERSION}` |
| Block threshold | `{Config.GUARDRAIL_BLOCK_THRESHOLD}` |
| Fail mode | `{Config.GUARDRAIL_FAIL_MODE}` |
| Corpus | `tests/adversarial/corpus/` |

Layer order matches production: L0 (pydantic) and L1 (regex/keyword) run first
and can block on their own, so L2 only ever judges text L1 allowed. Evaluating
the classifier on the whole corpus instead would flatter it with attacks it
never has to see.

{chr(10).join(sections)}
## Reading this

A high recall with L2 contributing nothing means the corpus is too easy. A high
false-positive rate means the block threshold is too low, or the negative
examples in `guardrails/prompts.py` are not specific enough to this domain — a
travel planner that refuses "halal food near a mosque" has failed at its job,
not succeeded at safety.

Latency is the cost side of the trade. Two classifier calls per plan against a
plan the UI budgets ~75 seconds for (`static/js/app.js:349`); if p95 approaches
a second, prefer a smaller `GUARDRAIL_LLM_MODEL` before weakening the gate.
"""
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(body, encoding="utf-8")
    print(f"Wrote {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""The grounding layer must not depend on langchain.

`reasoning.py` carries a deliberate property: it builds plain dicts rather than
`SystemMessage`/`HumanMessage` objects so its grounding, retry and fallback logic
can be exercised with a stub by tests that never touch langchain. Its
`StructuredLLM` docstring says so, and `docs/flight_agent/design.md` §7 says it
"must stay that way". `tools.py` extends the same property to the tool layer,
which is why `TOOL_SPECS` is hand-written dicts rather than `@tool` decorators.

That is prose until something checks it, and it is exactly the kind of property
that erodes silently: one convenience import of `AIMessage` in `tools.py` would
cost nothing visible while quietly coupling the deterministic layer to a heavy
optional dependency.

**Checked statically, on purpose.** The obvious runtime version — import the
module in a subprocess and look for langchain in `sys.modules` — does not work
and is worth recording so nobody rewrites it that way. Importing any submodule of
`flaskapp.travel_ai.agents` first executes that package's `__init__.py`, which
imports every specialist factory and therefore `agents/base.py`, which imports
`langchain_openai`. Every module below would "fail" that check, including
`loop.py`, whose own imports are pure standard library.

So the property being asserted is the accurate one: *these modules do not import
langchain themselves, directly or through each other*. `agentic.py` is the
intended exception — the tool loop genuinely needs langgraph — which is exactly
why the boundary is written down and enforced rather than remembered.
"""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Modules that must not depend on langchain, directly or transitively through
# each other. Listing the whole closure means a new edge between any two of them
# cannot smuggle the dependency in.
LANGCHAIN_FREE_MODULES = [
    "flaskapp/travel_ai/agents/loop.py",
    "flaskapp/travel_ai/agents/flight_agent/domain.py",
    "flaskapp/travel_ai/agents/flight_agent/guardrails.py",
    "flaskapp/travel_ai/agents/flight_agent/reasoning.py",
    "flaskapp/travel_ai/agents/flight_agent/schemas.py",
    "flaskapp/travel_ai/agents/flight_agent/structured_call.py",
    "flaskapp/travel_ai/agents/flight_agent/tools.py",
]

LANGCHAIN_ROOTS = {"langchain", "langchain_core", "langchain_openai",
                   "langchain_anthropic", "langchain_google_genai", "langgraph"}


def _imported_roots(path: Path) -> set[str]:
    """Top-level package of every module this file imports, at any nesting."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            # `level > 0` is a relative import, which has no top-level package
            # name to check and cannot reach an external dependency.
            if node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
    return roots


@pytest.mark.parametrize("relative_path", LANGCHAIN_FREE_MODULES)
def test_module_does_not_import_langchain(relative_path):
    path = ROOT / relative_path
    assert path.exists(), f"{relative_path} has moved; update this list"

    offending = _imported_roots(path) & LANGCHAIN_ROOTS
    assert not offending, (
        f"{relative_path} imports {sorted(offending)}. The grounding layer is "
        "deliberately usable without langchain — put the binding in agentic.py."
    )


def test_the_check_would_catch_a_violation(tmp_path):
    """A guard-rail test that never fails is indistinguishable from one that
    cannot fail. Prove the detector actually detects."""
    offender = tmp_path / "offender.py"
    offender.write_text("from langchain_core.messages import AIMessage\n", encoding="utf-8")

    assert _imported_roots(offender) & LANGCHAIN_ROOTS == {"langchain_core"}


def test_relative_imports_are_not_mistaken_for_packages(tmp_path):
    """`from .schemas import X` must not be read as a top-level dependency."""
    module = tmp_path / "relative.py"
    module.write_text("from .schemas import FlightProposal\nimport json\n", encoding="utf-8")

    assert _imported_roots(module) == {"json"}

"""Every reason a run can stop for has to be readable in the report.

`stop_reason` is the one line that says whether a report can be trusted, and
it was rendered as the raw identifier. "round_limit" and
"all_questions_answered" look equally final to a reader and mean opposite
things. The identifiers are now translated in the frontend, which means the
two files have to agree -- and the failure mode is silent: a new stop reason
simply appears untranslated in a report nobody re-reads.

This asserts the agreement rather than trusting it.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "frontend" / "src" / "ResearchReport.tsx"


def labelled_reasons() -> set[str]:
    source = REPORT.read_text(encoding="utf-8")
    block = re.search(r"const STOP_REASONS: Record<string, string> = \{(.*?)\n\}", source, re.S)
    assert block, "the report no longer defines STOP_REASONS"
    return set(re.findall(r"^\s*(\w+):", block.group(1), re.M))


def returned_reasons() -> set[str]:
    """Every string literal `investigate` can return, read from the syntax
    tree rather than by grepping, so a reason buried in a conditional is not
    missed."""
    tree = ast.parse((ROOT / "app" / "deep_research.py").read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == "investigate":
            for inner in ast.walk(node):
                if isinstance(inner, ast.Return):
                    found.update(literal.value for literal in ast.walk(inner)
                                 if isinstance(literal, ast.Constant) and isinstance(literal.value, str))
    assert found, "investigate() no longer returns a stop reason"
    return found


def test_every_stop_reason_the_run_can_return_has_a_sentence():
    missing = returned_reasons() - labelled_reasons()

    assert not missing, (
        f"these stop reasons would be shown to a reader as raw identifiers: {sorted(missing)}"
    )


def test_the_interrupted_run_reason_is_labelled_too():
    """Not returned by `investigate`; written directly when the service dies
    mid-run, and the most confusing one to meet without an explanation."""
    source = (ROOT / "app" / "deep_research.py").read_text(encoding="utf-8")

    assert 'stop_reason="service_shutdown"' in source, "the shutdown reason was renamed"
    assert "service_shutdown" in labelled_reasons()


def test_the_operator_cancelled_reason_is_labelled_too():
    """Also written directly rather than returned, so the check above cannot
    see it. It is the one a reader is most likely to meet, because it is the
    only stop reason a person causes on purpose."""
    source = (ROOT / "app" / "deep_research.py").read_text(encoding="utf-8")

    assert 'stop_reason="operator_cancelled"' in source, "the Stop reason was renamed"
    assert "operator_cancelled" in labelled_reasons()


def test_no_label_describes_a_reason_the_code_cannot_produce():
    """A stale label is a lie waiting to be read. Removing a stop reason
    without removing its sentence leaves the report claiming to explain
    something that can no longer happen."""
    source = (ROOT / "app" / "deep_research.py").read_text(encoding="utf-8")
    stale = {reason for reason in labelled_reasons() if reason not in source}

    assert not stale, f"labels describe stop reasons the code cannot produce: {sorted(stale)}"

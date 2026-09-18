"""What a system a dependency ALREADY IN THE PROJECT actually exports.

The Engineer builds one system at a time, in a worktree that holds the project
as it is. It is told which systems it depends on, and it requires them -- and
then it calls whatever it imagines they offer. Two real systems went out of a
build like that:

    AirlockService:          InfectedService.RedirectAggro    does not exist
    BaseConstructionService: ResearchService.Has              does not exist

Both were accepted by the six-check gate, because each was checked in its own
worktree where the dependency was not there to disagree. Both then could not be
landed: the project does not compile with a call to a function nobody wrote.
The names are not even wild -- InfectedService has `SetPheromoneAggro`, and
ResearchService has `IsUnlocked` and `HasEquipment`. It was guessing, plausibly,
because nothing told it.

So this reads the real signatures out of the file and hands them to the task.
Read from source rather than from a model's summary: the source is what
luau-lsp will check the call against.

Deliberately only the signature line. The body is what the dependency chose to
do and is none of the caller's business; the signature is the contract.
"""

from __future__ import annotations

import re
from pathlib import Path

# `function Name:Method(args): Return` and `function Name.Method(args): Return`,
# which is how every generated system declares its exported surface.
_EXPORT = re.compile(r"^function\s+(?P<owner>[A-Z][A-Za-z0-9_]*)[.:](?P<rest>.+)$",
                     re.MULTILINE)
_TYPE = re.compile(r"^(?:export\s+)?type\s+(?P<name>[A-Z][A-Za-z0-9_]*)\s*=", re.MULTILINE)
MAX_PER_SYSTEM = 40


def exports_of(source: str, system: str) -> list[str]:
    """The signature lines a caller may rely on, in the order they appear."""
    found = []
    for match in _EXPORT.finditer(source):
        if match.group("owner") != system:
            continue
        signature = match.group("rest").strip()
        if signature.endswith("{"):
            signature = signature[:-1].strip()
        found.append(f"{system}:{signature}" if f"{system}:" in match.group(0)
                     else f"{system}.{signature}")
    return found[:MAX_PER_SYSTEM]


def types_of(source: str) -> list[str]:
    """Exported type names, so a caller can annotate what it receives."""
    return [match.group("name") for match in _TYPE.finditer(source)][:MAX_PER_SYSTEM]


def interface_note(repo: Path, names: list[str], paths: dict[str, str]) -> str:
    """One note listing what each named dependency really offers, or "".

    A dependency that is not in the project yet gets no entry: it is being
    built in this same run and has no signatures to quote. Saying nothing is
    right there -- an empty list would read as "it exports nothing".
    """
    sections = []
    for name in names:
        path = paths.get(name)
        if not path:
            continue
        file = repo / path
        if not file.is_file():
            continue
        try:
            source = file.read_text(encoding="utf-8")
        except OSError:
            continue
        signatures = exports_of(source, name)
        if not signatures:
            continue
        types = types_of(source)
        line = f"{name} exports: " + "; ".join(signatures)
        if types:
            line += f". Its exported types: {', '.join(types)}"
        sections.append(line)

    if not sections:
        return ""
    return (
        "These dependencies ARE ALREADY IN THE PROJECT, and these are the only "
        "functions they have. Call these exact names with these exact arguments; "
        "do not call anything else on them, and do not assume a function exists "
        "because the name would be reasonable. If what you need is not here, do "
        "it yourself rather than inventing a call. " + " | ".join(sections)
    )

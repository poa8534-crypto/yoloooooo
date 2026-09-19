"""Several systems written at once, without ever breaking the build order.

The build order is a line; the dependency graph is not. Ascent's sixteen
systems are five layers deep and six wide at the widest, and walking the line
one system at a time spent 4902 seconds of system time where the longest chain
through the graph needed 1797.

What makes overlapping safe is the rule the line already obeyed: a system
starts only after everything it depends on has FINISHED -- written, checked,
and landed or refused. Finished includes landed because a system's worktree is
cut from the project as it stands when the system starts, and the project's
own sources are how the Engineer sees what its dependencies really export.
Starting before a dependency lands would hide exactly the code it has to call.

Not waves. Waiting for the whole of one layer before starting the next holds
every system in the next layer hostage to the slowest system in this one. A
system starts the moment its own dependencies are done.

When more systems are ready than there are slots, the build order chooses --
it already breaks ties by the doctrine's priorities, so vertical-slice work
still goes first.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Collection, Mapping


class Unschedulable(ValueError):
    """Systems that can never start: each waits on something that never finishes."""


async def in_dependency_order(order: list[str], depends: Mapping[str, Collection[str]],
                              limit: int, work: Callable[[str], Awaitable[None]]) -> None:
    """Run `work(name)` for every name in `order`.

    A name starts only once every dependency it has IN `order` has finished; a
    dependency outside `order` is something the project already has. At most
    `limit` run at once, and a free slot goes to the ready name earliest in
    `order`.

    An exception from `work` stops the build: everything still running is
    cancelled and awaited, and the exception propagates. `work` is expected to
    record an ordinary failure of its own system and return, as a refused
    system does -- what escapes is something the build cannot carry on past.
    """
    if limit < 1:
        raise ValueError("at least one system has to be able to run")
    names = set(order)
    needs = {name: {need for need in depends.get(name, ()) if need in names and need != name}
             for name in order}
    pending = list(order)
    finished: set[str] = set()
    running: dict[asyncio.Task, str] = {}
    try:
        while pending or running:
            for name in list(pending):
                if len(running) >= limit:
                    break
                if needs[name] <= finished:
                    pending.remove(name)
                    running[asyncio.create_task(work(name), name=f"system {name}")] = name
            if not running:
                raise Unschedulable(
                    "these systems can never start, because each waits on one that never "
                    "finishes: " + ", ".join(
                        f"{name} (needs {', '.join(sorted(needs[name] - finished))})"
                        for name in pending))
            done, _ = await asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)
            failures = []
            for task in done:
                finished.add(running.pop(task))
                if not task.cancelled() and task.exception() is not None:
                    failures.append(task.exception())
            if failures:
                raise failures[0]
    finally:
        for task in running:
            task.cancel()
        if running:
            await asyncio.gather(*running, return_exceptions=True)

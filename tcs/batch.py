"""Run one change per item, carrying on past failures, and report what happened.

Every script that changes the site (moving workbooks, adding group members,
deleting subscriptions) works through a list the same way: one item at a
time, with progress, a failure recorded instead of stopping the run, and a
summary at the end. The exit code is 1 if anything failed, so a scheduled job
notices.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Generic, List, Optional, Sequence, TypeVar

from . import ui
from .client import TableauError

T = TypeVar("T")

DONE, FAILED = "done", "failed"


@dataclass
class Outcome(Generic[T]):
    item: T
    status: str  # DONE or FAILED
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status == DONE


def run_batch(
    items: Sequence[T],
    act: Callable[[T], Optional[str]],
    describe: Callable[[T], str],
    *,
    explain: Optional[Callable[[T, TableauError], str]] = None,
) -> List[Outcome[T]]:
    """Call ``act(item)`` for each item, in order.

    ``act`` returns an optional note for the log, or raises TableauError.
    ``explain`` can turn a known error (a 409, say) into a plain reason.
    """
    outcomes: List[Outcome[T]] = []
    width = len(str(len(items)))
    for number, item in enumerate(items, start=1):
        label = f"[{str(number).rjust(width)}/{len(items)}] {describe(item)}"
        try:
            note = act(item) or ""
        except TableauError as exc:
            reason = explain(item, exc) if explain else str(exc)
            ui.error(f"{label}: {reason}")
            outcomes.append(Outcome(item, FAILED, reason))
            continue
        ui.success(label + (f"  {ui.style(note, 'dim')}" if note else ""))
        outcomes.append(Outcome(item, DONE, note))
    return outcomes


def summary(outcomes: Sequence[Outcome], done_label: str) -> int:
    """Print the counts and return the exit code: 0, or 1 if anything failed."""
    failed = sum(1 for o in outcomes if not o.ok)
    ui.heading("Summary")
    ui.table([(done_label, str(len(outcomes) - failed)), ("Failed", str(failed))])
    return 1 if failed else 0

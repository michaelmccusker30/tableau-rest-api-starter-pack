"""Pace calls that Tableau rate-limits, and slow down further whenever it says "too many".

Tableau Cloud caps the calls that start extract refreshes (Update Data Source
Now, Update Workbook Now, Run Extract Refresh Task) per site per hour. The cap
depends on the edition: 120 an hour on Standard, more on Enterprise and
Tableau+. Go over it and every call gets HTTP 429 with "Please retry after N
second(s)".

RateBudget spends a budget of calls per rolling hour:
  * calls are spread evenly (one every 3600/limit seconds), not fired in a burst
  * the calls already made are remembered in a small state file, so a second
    run in the same hour knows what the first one spent
  * a 429 blocks for as long as Tableau asks and cuts the pace by a quarter;
    every 25 calls in a row that succeed win back a little, up to the limit
    you set (slow down fast, speed up slowly)

Adapted from the author's earlier hand-built retry script, which worked this out first.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import List, Optional

from . import ui

WINDOW = 3600.0
FLOOR = 12  # never slow below one call every five minutes
BACKOFF = 0.75
RECOVER_AFTER = 25
DEFAULT_WAIT = 120

# Looked up at call time, so tests can replace them and run without sleeping.
_now = time.time
_sleep = time.sleep


def now() -> float:
    """The pacing clock (replaceable in tests)."""
    return _now()


class RateBudget:
    def __init__(self, per_hour: int, state_file: Optional[Path] = None) -> None:
        self.nominal = max(1, per_hour)
        self.limit = self.nominal
        self.state_file = state_file
        self.blocked_until = 0.0
        self.streak = 0
        self.calls: List[float] = self._load()

    def wait_for_slot(self) -> None:
        """Block until a call fits the budget, then record it."""
        while True:
            now = _now()
            if now < self.blocked_until:
                _sleep(min(30.0, self.blocked_until - now))
                continue
            self.calls = [t for t in self.calls if t > now - WINDOW]
            if len(self.calls) >= self.limit:
                _sleep(max(0.1, min(self.calls) + WINDOW - now + 1))
                continue
            spacing = WINDOW / self.limit
            if self.calls and now - max(self.calls) < spacing - 0.01:
                _sleep(spacing - (now - max(self.calls)))
                continue
            self.calls.append(now)
            self._save()
            return

    def penalise(self, wait_seconds: float) -> None:
        """Tableau said 429: stop for as long as it asked, and slow down."""
        self.blocked_until = _now() + wait_seconds
        before = self.limit
        self.limit = max(FLOOR, int(self.limit * BACKOFF))
        self.streak = 0
        self._save()
        ui.warn(f"Rate limited: pausing {int(wait_seconds)}s, pace {before}/hour -> {self.limit}/hour")

    def reward(self) -> None:
        self.streak += 1
        if self.streak >= RECOVER_AFTER and self.limit < self.nominal:
            self.limit = min(self.nominal, self.limit + max(1, self.nominal // 20))
            self.streak = 0
            ui.detail(f"  Pace recovering: {self.limit}/hour")

    def _load(self) -> List[float]:
        if not self.state_file:
            return []
        try:
            calls = json.loads(self.state_file.read_text()).get("calls", [])
            cutoff = _now() - WINDOW
            return [float(t) for t in calls if float(t) > cutoff]
        except (OSError, ValueError, TypeError, AttributeError):
            return []

    def _save(self) -> None:
        if not self.state_file:
            return
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text(json.dumps({"calls": self.calls}))
        except OSError:
            pass  # pacing still works in memory


def retry_after_seconds(message: str, default: int = DEFAULT_WAIT) -> int:
    """Read "Please retry after 146 second(s)" (or minutes/hours) from a 429 message."""
    match = re.search(r"after\s+(\d+)\s*(second|minute|hour)", message or "", re.IGNORECASE)
    if not match:
        return default
    return int(match.group(1)) * {"second": 1, "minute": 60, "hour": 3600}[match.group(2).lower()] + 5

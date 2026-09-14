"""Daily token-spend budget (Phase 6). When exceeded, jobs degrade to the
deterministic report with a visible note rather than fail — see worker.py.
Budget state resets at local-day rollover.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass
class SpendGuard:
    daily_token_budget: int
    _day: date = field(default_factory=date.today)
    _spent: int = 0

    def _roll_day_if_needed(self) -> None:
        today = date.today()
        if today != self._day:
            self._day = today
            self._spent = 0

    def exceeded(self) -> bool:
        self._roll_day_if_needed()
        return self._spent >= self.daily_token_budget

    def record(self, total_tokens: int) -> None:
        self._roll_day_if_needed()
        self._spent += total_tokens

    @property
    def spent_today(self) -> int:
        self._roll_day_if_needed()
        return self._spent

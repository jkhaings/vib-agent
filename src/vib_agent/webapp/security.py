"""Invite-code gating, rate limiting, and upload validation (Phase 6).

Codes live in the INVITE_CODES env var (`code:label` pairs), never in
config or code. Only the label is ever logged or returned — the raw code
is a secret, never surfaced past the initial check.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path


class InviteCodeError(ValueError):
    """Missing or unknown invite code."""


class RateLimitError(ValueError):
    """A code (or the whole service) is over its daily job cap."""


def parse_invite_codes(raw: str | None) -> dict[str, str]:
    """`INVITE_CODES=code1:label1,code2:label2` -> {code: label}."""
    codes: dict[str, str] = {}
    if not raw:
        return codes
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair:
            continue
        if ":" not in pair:
            raise ValueError(f"malformed INVITE_CODES entry (want code:label): {pair!r}")
        code, label = pair.split(":", 1)
        code, label = code.strip(), label.strip()
        if code and label:
            codes[code] = label
    return codes


def resolve_code_label(codes: dict[str, str], code: str | None) -> str:
    """Raises InviteCodeError for a missing/unknown code; else returns the
    label (never the code) for logging."""
    if not code or code not in codes:
        raise InviteCodeError("missing or invalid invite code")
    return codes[code]


@dataclass
class RateLimiter:
    """Per-code + global daily job counters, reset at local-day rollover."""

    per_code_daily_jobs: int
    global_daily_jobs: int
    _day: date = field(default_factory=date.today)
    _per_code: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    _global: int = 0

    def _roll_day_if_needed(self) -> None:
        today = date.today()
        if today != self._day:
            self._day = today
            self._per_code.clear()
            self._global = 0

    def check_and_record(self, code_label: str) -> None:
        self._roll_day_if_needed()
        if self._global >= self.global_daily_jobs:
            raise RateLimitError("global daily job cap reached")
        if self._per_code[code_label] >= self.per_code_daily_jobs:
            raise RateLimitError("daily job cap reached for this invite code")
        self._per_code[code_label] += 1
        self._global += 1

    def refund(self, code_label: str) -> None:
        """Give one job back. F8, decided by the operator: a failure on OUR
        side does not spend the analyst's daily allowance.

        The counter is spent at ACCEPTANCE (`check_and_record`, before any work
        happens), which is the right moment to charge for a job that is going to
        run. It is the wrong moment to have charged for one that then crashed
        inside our own service, and the analyst has no way to tell those apart
        from where they sit — they uploaded a good file and got nothing.

        Floored at zero, which is what makes it safe across the day rollover:
        a job accepted at 23:59 and failed at 00:01 would otherwise refund into
        a fresh day's counter and hand out a free job that was never charged.
        After `_roll_day_if_needed` the counters are zero, `max(0, ...)` leaves
        them there, and the analyst keeps the new day's full allowance either
        way. That is the correct outcome: the day they lost the job to is over.

        Idempotency is NOT enforced here -- it is a property of the single call
        site. `worker.mark_error` fires this exactly once per job by
        construction, because it returns early on an already-terminal job.
        Doing it that way rather than tracking refunded job ids keeps this
        class what it is: two integers and a date.
        """
        self._roll_day_if_needed()
        self._per_code[code_label] = max(0, self._per_code[code_label] - 1)
        self._global = max(0, self._global - 1)


_MAGIC: dict[str, bytes] = {
    ".xlsx": b"PK",
    ".mat": b"MATLAB",
    ".wav": b"RIFF",
}


def _sniff(ext: str, content: bytes) -> None:
    """Magic-byte sniff for the formats with an unambiguous signature.
    CSV/UFF/UNV are plain text with no reliable magic number -- extension
    plus the parser's own validation is the check for those."""
    magic = _MAGIC.get(ext)
    if magic is None:
        return
    if magic not in content[:132]:
        raise ValueError(f"file content does not look like a {ext} file")


def validate_upload(
    filename: str,
    content: bytes,
    *,
    allowed_extensions: tuple[str, ...],
    max_bytes: int,
) -> str:
    """Extension whitelist + size cap + magic-byte sniff. Returns the
    lowercase extension, or raises ValueError naming the problem (the
    caller maps that to the right 4xx — 413 for size, 415 for format)."""
    ext = Path(filename).suffix.lower()
    if len(content) > max_bytes:
        raise ValueError(f"file exceeds max size of {max_bytes} bytes")
    if ext not in allowed_extensions:
        raise ValueError(f"unsupported extension {ext!r}")
    _sniff(ext, content)
    return ext

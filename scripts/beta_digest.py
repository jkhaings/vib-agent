"""Turn the service's per-job log lines into a daily markdown digest.

    python scripts/beta_digest.py app.log                 # every day in the file
    python scripts/beta_digest.py app.log --date 2026-08-06
    python scripts/beta_digest.py app.log --out digest.md

Offline and read-only: it parses text you already have. Nothing here connects
to anything — see FETCH_COMMAND at the bottom for the one-liner that would bring
the log down from the server, documented rather than run.

THE TWO LINES IT PARSES, confirmed against source, not remembered:

    src/vib_agent/webapp/app.py::_log_job_outcome
        "job=%s code=%s kind=%s size_bytes=%d duration_ms=%s tokens=%d[ fail=%s] outcome=%s"
    src/vib_agent/webapp/app.py::_log_internal_failure
        "job=%s internal_failure exc=%s.%s\n%s"          (the traceback is NOT read)
    src/vib_agent/webapp/app.py::create_app
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")

so a real line reads

    2026-08-06 01:58:47,826 INFO vib_agent.webapp job=ecb3b37... code=engineer-1 \
        kind=inferred_spectrum size_bytes=8213 duration_ms=9133.4 tokens=150 outcome=gate_fail

Two field quirks that a naive split would get wrong, both from the source:
  * `duration_ms` is "n/a" when the job never started timing (`%s`, not `%f`);
  * `kind` can carry parentheses and commas — "inferred(txt)",
    "multiaxis(inferred_spectrum,tabular_spectrum)" — so it is NOT safe to
    tokenise on commas, only on ` key=` boundaries.

The second one was added by S7 and read by nothing until TIDY-2, which is worse
than it sounds: `parse_log`'s format-change alarm counted every line containing
` job=` that was not the full outcome line, so an internal failure — and a
`draft_failed`, an `rca_error`, a `credit_refunded` — raised *"the log line may
have changed"*. The one signal that is supposed to mean the FORMAT moved fired
on the days something CRASHED. See `looks_like_an_outcome_line` for what the
population is now, and `_crash_lines` for where the crashes went instead.

WHAT IS DELIBERATELY NOT IN THE DIGEST: there is no IP, no filename, no machine
alias and no file content in these lines, because there is none in the log —
that is the app's privacy contract (see CLAUDE.md: uvicorn's access log is off
with --no-access-log, and Caddy sets no `log` directive). This script must never
become the reason someone adds a richer log line. `code` is an invite-code
LABEL, never the code itself.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

# One capture per field. Anchored on ` job=` so the startup line
# ("vib-agent webapp starting mode=...") and anything else in the file is
# skipped rather than half-parsed.
_LINE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2})[ T](?P<time>\d{2}:\d{2}:\d{2})[,.]?\d*\s+"
    r"(?P<level>[A-Z]+)\s+(?P<logger>\S+)\s+"
    r"job=(?P<job>\S+)\s+"
    r"code=(?P<code>.*?)\s+"
    r"kind=(?P<kind>.*?)\s+"
    r"size_bytes=(?P<size>\d+)\s+"
    r"duration_ms=(?P<duration>\S+)\s+"
    r"tokens=(?P<tokens>\d+)\s+"
    # S7 added `fail=<error_code>` on error outcomes only -- the taxonomy
    # category, so a failure day can be read without a traceback. Optional, so
    # lines written before S7 (and every non-error line since) still parse.
    r"(?:fail=(?P<fail>\S+)\s+)?"
    r"outcome=(?P<outcome>\S+)\s*$"
)

# The OTHER line a job can write about itself, and the reason this script had a
# false alarm from S7 until TIDY-2.
#
#     src/vib_agent/webapp/app.py::_log_internal_failure
#         "job=%s internal_failure exc=%s.%s\n%s"
#
# It is a WARNING, it carries the exception class and then a traceback on
# following lines, and it is deliberately a SECOND line rather than a field on
# the outcome line -- that line is the clean, PII-free per-job record the
# privacy model promises and it must not grow a stack trace. Same reason
# nothing below reads the traceback: our own stack frames are useful to the
# operator, and this digest is a thing that gets pasted around.
_CRASH = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2})[ T](?P<time>\d{2}:\d{2}:\d{2})[,.]?\d*\s+"
    r"[A-Z]+\s+\S+\s+"
    r"job=(?P<job>\S+)\s+internal_failure\s+exc=(?P<exc>\S+)\s*$"
)

# JobState, from src/vib_agent/webapp/jobs.py. `queued`/`running` never appear
# as an outcome (the line is written at a terminal state or a pause), but they
# are listed so an unexpected one is visibly unexpected.
TERMINAL_OUTCOMES = ("done", "degraded", "gate_fail", "error", "awaiting_confirm")

# How each outcome should read to a human running a beta. The taxonomy is the
# point of the digest: "12 jobs" says nothing, "9 done, 2 gate_fail, 1 error"
# says where the week went.
OUTCOME_MEANING = {
    "done": "analysed and drafted",
    "degraded": "analysed; drafting unavailable (budget, API, or consistency)",
    "gate_fail": "insufficient data — report names what to re-collect",
    "error": "upload could not be read at all",
    "awaiting_confirm": "paused for interpretation, never confirmed",
}


@dataclass(frozen=True)
class JobLine:
    date: str
    time: str
    job: str
    code: str
    kind: str
    size_bytes: int
    duration_ms: float | None
    tokens: int
    outcome: str
    fail: str | None = None

    @property
    def family(self) -> str:
        """`inferred(txt)` and `inferred_spectrum` are both the inference lane;
        `multiaxis(...)` is a multi-file upload whatever its parts were."""
        if self.kind.startswith("multiaxis"):
            return "multiaxis"
        if self.kind.startswith("inferred"):
            return "inferred"
        if self.kind.startswith("tabular"):
            return "tabular"
        return self.kind or "unknown"


@dataclass(frozen=True)
class CrashLine:
    """An internal failure. Two fields, both already on the line: the job id and
    the exception class. Never the traceback."""
    date: str
    time: str
    job: str
    exc: str


def parse_line(line: str) -> JobLine | None:
    """One log line as a JobLine, or None if it is not a per-job line."""
    match = _LINE.match(line.strip())
    if match is None:
        return None
    raw_duration = match["duration"]
    try:
        duration = float(raw_duration)
    except ValueError:
        duration = None        # "n/a" — the job never started timing
    return JobLine(
        date=match["ts"], time=match["time"], job=match["job"], code=match["code"],
        kind=match["kind"], size_bytes=int(match["size"]), duration_ms=duration,
        tokens=int(match["tokens"]), outcome=match["outcome"], fail=match["fail"],
    )


def parse_crash(line: str) -> CrashLine | None:
    """One internal-failure line as a CrashLine, or None."""
    match = _CRASH.match(line.strip())
    if match is None:
        return None
    return CrashLine(date=match["ts"], time=match["time"],
                     job=match["job"], exc=match["exc"])


def looks_like_an_outcome_line(line: str) -> bool:
    """Is this line TRYING to be `_log_job_outcome`'s line?

    This is the alarm's population, and until TIDY-2 it was simply `" job=" in
    line` — which was wrong, and wrong in the direction that costs an operator
    the most. Every line that names a job carries ` job=`, and eight of them are
    not the outcome line at all:

        app.py::_log_internal_failure    `job=.. internal_failure exc=..`
        worker.py                        `draft_failed` `store_write_failed`
                                         `credit_refund_failed` `rca_error` ×2
                                         `compare_failed`
        billing/spend.py                 `credit_refunded job=..`

    So the counter that is supposed to mean *"the log format changed"* fired
    once per crash, per lost draft, per refund — every day something went
    wrong, which is every day an operator most needs to trust it. FLIP-1 F-6
    measured it and named the two fixes; `app.py` belongs to another lane, so
    this is the other one: a narrower filter here.

    The discriminator is the two fields that make a line the per-job RECORD
    rather than an event about a job. None of the eight carries either. A
    change that renamed or dropped one of them still trips the alarm through
    the other; only a change that removed BOTH at once would slip past, and
    that one arrives as `No job lines found.`, which is not silent either.
    """
    return " job=" in line and (" code=" in line or " outcome=" in line)


def parse_log(text: str) -> tuple[list[JobLine], int]:
    """Every per-job line in `text`, plus a count of lines that were trying to
    be one and did not parse — a silent zero there would hide a format change.

    Arity is deliberately unchanged (`jobs, unparsed = parse_log(...)`): two
    test files unpack it, one of them in another session's lane. Crashes come
    from `parse_crashes` beside it rather than from a third element here.
    """
    jobs: list[JobLine] = []
    unparsed = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        parsed = parse_line(line)
        if parsed is not None:
            jobs.append(parsed)
        elif looks_like_an_outcome_line(line):
            unparsed += 1
    return jobs, unparsed


def parse_crashes(text: str) -> list[CrashLine]:
    """Every internal-failure line in `text`.

    A second pass over the same text, which for an offline script reading a
    file the operator already has is cheaper than the alternative: a `parse_log`
    that returns three things, which would break the two files that unpack two.
    """
    crashes = [parse_crash(line) for line in text.splitlines() if line.strip()]
    return [c for c in crashes if c is not None]


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[len(ordered) // 2] if ordered else 0.0


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _size(byte_count: float) -> str:
    return f"{byte_count / 1024:.0f} KB" if byte_count >= 1024 else f"{byte_count:.0f} B"


def _table(header: tuple[str, ...], rows: list[tuple[str, ...]]) -> list[str]:
    if not rows:
        return []
    return ["| " + " | ".join(header) + " |",
            "|" + "|".join("---" for _ in header) + "|",
            *["| " + " | ".join(r) + " |" for r in rows]]


def _crash_lines(crashes: list[CrashLine]) -> list[str]:
    """What the alarm should have been saying all along: not *"the format
    changed"* but *"something crashed on our side"*. One line per exception
    class, with the job ids, so the operator can go straight to the log."""
    if not crashes:
        return []
    by_exc: dict[str, list[CrashLine]] = defaultdict(list)
    for crash in crashes:
        by_exc[crash.exc].append(crash)
    out = ["", "### Internal failures", "",
           f"{_plural(len(crashes), 'job')} failed on OUR side, with a traceback in the "
           "log. This is not a data problem and not an analyst's file; each one is a bug "
           "or an unavailable dependency.", ""]
    out += _table(
        ("exception", "n", "jobs"),
        [(f"`{exc}`", str(len(group)), ", ".join(c.job[:8] for c in group))
         for exc, group in sorted(by_exc.items(), key=lambda kv: -len(kv[1]))])
    return out


def digest_for_day(date: str, jobs: list[JobLine],
                   crashes: list[CrashLine] | None = None) -> list[str]:
    crashes = crashes or []
    out: list[str] = [f"## {date}", ""]
    if not jobs:
        out += ["No jobs.", ""]
        out += _crash_lines(crashes)
        if crashes:
            out += [""]
        return out

    outcomes = Counter(j.outcome for j in jobs)
    codes = Counter(j.code for j in jobs)
    families = Counter(j.family for j in jobs)
    kinds = Counter(j.kind for j in jobs)
    tokens = sum(j.tokens for j in jobs)
    durations = [j.duration_ms for j in jobs if j.duration_ms is not None]

    reached_a_report = sum(outcomes[o] for o in ("done", "degraded", "gate_fail"))
    headline = (f"**{_plural(len(jobs), 'job')}** from "
                f"**{_plural(len(codes), 'invite code')}** · {tokens:,} tokens")
    if durations:
        headline += (f" · median {(_median(durations) / 1000):.1f}s, "
                     f"slowest {(max(durations) / 1000):.1f}s")
    out += [
        headline,
        "",
        f"{reached_a_report}/{len(jobs)} reached a report "
        f"({reached_a_report / len(jobs) * 100:.0f}%).",
        "",
        "### Outcomes", "",
    ]
    ordered = [o for o in TERMINAL_OUTCOMES if o in outcomes]
    ordered += sorted(o for o in outcomes if o not in TERMINAL_OUTCOMES)
    out += _table(
        ("outcome", "n", "what it means"),
        [(o, str(outcomes[o]),
          OUTCOME_MEANING.get(o, "**UNKNOWN OUTCOME — new state, or a format change**"))
         for o in ordered])
    out += ["", "### Per invite code", ""]
    out += _table(
        ("code", "jobs", "outcomes"),
        [(code, str(count),
          ", ".join(f"{o}×{n}" for o, n in
                    Counter(j.outcome for j in jobs if j.code == code).most_common()))
         for code, count in codes.most_common()])
    out += ["", "### What they uploaded", ""]
    out += _table(
        ("lane", "jobs", "kinds seen"),
        [(family, str(count),
          ", ".join(sorted({j.kind for j in jobs if j.family == family})))
         for family, count in families.most_common()])

    failures = [j for j in jobs if j.outcome in ("error", "gate_fail")]
    if failures:
        out += ["", "### Failure taxonomy", "",
                f"{len(failures)} of {len(jobs)} jobs did not produce a diagnosis. "
                "Since S7 an `error` carries its taxonomy category (`fail=`), which says "
                "whose problem it was; nothing here says what was IN the file, by design.", ""]
        out += _table(
            ("outcome", "cause", "lane", "n", "median size"),
            [(outcome, fail or "—", family, str(n),
              _size(_median([float(j.size_bytes) for j in group])))
             for (outcome, fail, family), (n, group) in sorted(
                 ((key, (len(g), g)) for key, g in _group_failures(failures).items()),
                 key=lambda kv: -kv[1][0])])
    out += _crash_lines(crashes)
    if len(kinds) > len(families):
        out += ["", f"_{len(kinds)} distinct `kind` values across {len(families)} lanes._"]
    out += [""]
    return out


def _group_failures(failures: list[JobLine]) -> dict[tuple[str, str, str], list[JobLine]]:
    grouped: dict[tuple[str, str, str], list[JobLine]] = defaultdict(list)
    for job in failures:
        grouped[(job.outcome, job.fail or "", job.family)].append(job)
    return grouped


def build_digest(jobs: list[JobLine], unparsed: int, *,
                 crashes: list[CrashLine] | None = None,
                 only: str | None = None) -> str:
    """`crashes` is keyword-only with a default so that `build_digest(*parse_log(text))`
    still works — it is what every existing caller does."""
    crashes = crashes or []
    by_day: dict[str, list[JobLine]] = defaultdict(list)
    for job in jobs:
        by_day[job.date].append(job)
    crashes_by_day: dict[str, list[CrashLine]] = defaultdict(list)
    for crash in crashes:
        crashes_by_day[crash.date].append(crash)
    days = sorted(set(by_day) | set(crashes_by_day))
    if only:
        days = [d for d in days if d == only]

    out = ["# vib-agent beta digest", ""]
    if not days:
        out += ["No job lines found." if not only else f"No job lines for {only}.", ""]
    for day in days:
        out += digest_for_day(day, by_day[day], crashes_by_day[day])
    if unparsed:
        # The alarm, reworded to say what it actually detects. Before TIDY-2 it
        # said "contained `job=`", which was true of eight lines that are not
        # this line at all, so a non-zero count sent the operator to the wrong
        # place — usually to an incident, when the counter is only ever about a
        # format. Internal failures now have their own section above.
        out += [f"> ⚠ {unparsed} line(s) look like the per-job outcome record — they carry "
                "`job=` and `code=`/`outcome=` — but did not parse. **The log line has "
                "probably changed:** check `src/vib_agent/webapp/app.py::_log_job_outcome` "
                "against the pattern in this script. This counter is about the FORMAT; it "
                "is not a count of failures.", ""]
    return "\n".join(out)


# The one-liner that would fetch real lines. DOCUMENTED, NEVER RUN by this
# script — it exists so the command is written down once, correctly, instead of
# improvised at 2am. `journalctl` is not used: the unit writes to a file
# (StandardOutput=append:/var/log/vibagent/app.log, see deploy/vibagent.service).
FETCH_COMMAND = (
    "ssh <host> \"grep -h ' job=' /var/log/vibagent/app.log*\" > app.log "
    "&& python scripts/beta_digest.py app.log"
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("logfile", type=Path, help="a file of app.log lines")
    parser.add_argument("--date", help="only this day (YYYY-MM-DD)")
    parser.add_argument("--out", type=Path, help="write markdown here instead of stdout")
    args = parser.parse_args(argv)

    if not args.logfile.exists():
        print(f"no such file: {args.logfile}\n\nTo fetch real lines:\n  {FETCH_COMMAND}",
              file=sys.stderr)
        return 2

    text = args.logfile.read_text(errors="replace")
    jobs, unparsed = parse_log(text)
    markdown = build_digest(jobs, unparsed, crashes=parse_crashes(text), only=args.date)
    if args.out:
        args.out.write_text(markdown)
        print(f"{len(jobs)} job lines -> {args.out}")
    else:
        print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

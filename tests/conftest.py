"""Shared pytest fixtures: config-loaded thresholds/iso_table, and the four
machine registry entries used across the reference test cases.

Amendment A2: the real MACHINE_REGISTRY JSON lives in a FlowFuse env var, not
in reference/flows.json, so it is not available to port verbatim. These four
machines are reconstructed from the reference test harness's own expected
outcomes (see docstrings below for the evidence trail) and marked # VERIFY
where the reconstruction is an assumption rather than a confirmed value.
If reference/machine_registry.json appears, replace this fixture with a
loader for it and drop the reconstruction.
"""

from __future__ import annotations

import re

import pytest

from vib_agent.config import load_config, load_thresholds
from vib_agent.models import BearingSpec, MachineMeta


def pytest_addoption(parser: pytest.Parser) -> None:
    # Session E multi-axis kit: `--drafted` re-runs Case A2 through the LLM
    # drafting path to produce one human-readable PDF. Requires ANTHROPIC_API_KEY
    # (operator sources ./.env.api in the command); off by default, not part of
    # the automated bar. See tests/test_multiaxis_kit_e2e.py.
    parser.addoption(
        "--drafted",
        action="store_true",
        default=False,
        help="Multi-axis kit: also draft Case A2 to a real PDF (needs ANTHROPIC_API_KEY).",
    )
    # Session CI-1. `--shard k/N` runs bin k of a deterministic N-way split, so
    # GitHub Actions can run the WHOLE suite across N runners and still add up
    # to one number. See `_apply_shard` below for the split and for why the
    # suite lock deliberately still reads the PRE-shard count.
    parser.addoption(
        "--shard",
        action="store",
        default=None,
        metavar="k/N",
        help="Run only bin k of a deterministic N-way split of the collected tests (1-based).",
    )


@pytest.fixture
def iso_table() -> dict[str, dict[str, float]]:
    return load_config("iso_zones")["zones"]


@pytest.fixture
def thresholds() -> dict:
    """`route`, named explicitly (DQ-0 / S16).

    A bare `load_thresholds()` resolves `active_profile` — a default, not a
    decision — and that is the frozen `streaming` profile, which no upload
    ever runs. Fixture-driven tests were therefore exercising a profile the
    product does not use for the data these fixtures represent, masking
    route-profile behaviour changes (bit during PDMFIX). Tests that mean
    streaming say so themselves (e.g. test_bearing_rca's
    test_streaming_profile_* assert the guard keys are absent).
    """
    return load_thresholds("route")


@pytest.fixture
def rules() -> dict:
    return load_config("next_measurements")


@pytest.fixture
def pump_machine() -> MachineMeta:
    # iso_group/support: no zone-boundary fixture pins this machine specifically
    # (T01/T02 are both trivially deep in Zone A regardless of thresholds).
    # group2/rigid assumed consistent with the other three machines. # VERIFY
    return MachineMeta(
        mac="TEST-PUMP-01",
        name="Test Pump 01",
        active=True,
        type="pump",
        iso_group="2",
        iso_support="rigid",
    )


@pytest.fixture
def comp_machine() -> MachineMeta:
    # iso_group=2/rigid CONFIRMED: T03 (2.0 mm/s -> Zone B) and T06 (4.51 mm/s ->
    # Zone D, matches cd=4.5) both land exactly where group2/rigid predicts.
    # Bearing = 6206 CONFIRMED: T12's BPFO peak (107.16 Hz @ 1800 rpm) matches
    # a 6206 geometry's computed BPFO (107.03 Hz, 0.12% delta) far tighter than
    # any other catalog entry — see config/bearings.json comment.
    return MachineMeta(
        mac="TEST-COMP-01",
        name="Test Compressor 01",
        active=True,
        type="compressor",
        iso_group="2",
        iso_support="rigid",
        bearing=BearingSpec(
            n_balls=9, ball_dia_mm=9.53, pitch_dia_mm=46.0, contact_angle_deg=0.0, model="6206"
        ),
    )


@pytest.fixture
def motor_machine() -> MachineMeta:
    # iso_group=2/rigid CONFIRMED: T04 (1.41 mm/s -> "A/B Boundary", matches
    # ab=1.4) and T05 (2.81 mm/s -> "B/C Boundary" i.e. Zone C, matches bc=2.8).
    return MachineMeta(
        mac="TEST-MOTOR-01",
        name="Test Motor 01",
        active=True,
        type="motor",
        iso_group="2",
        iso_support="rigid",
    )


@pytest.fixture
def fan_machine() -> MachineMeta:
    # coupled=False CONFIRMED: T08 is explicitly named "Bent Shaft — FAN
    # (uncoupled)" and its diagnosis (bent_shaft, not angular_misalignment)
    # only reproduces when coupled=False (reference: `if (!coupled) fault =
    # "bent_shaft"`). iso_group/support unconfirmed by any boundary fixture. # VERIFY
    return MachineMeta(
        mac="TEST-FAN-01",
        name="Test Fan 01",
        active=True,
        type="fan",
        iso_group="2",
        iso_support="rigid",
        coupled=False,
    )


@pytest.fixture
def machines(pump_machine, comp_machine, motor_machine, fan_machine) -> dict[str, MachineMeta]:
    return {
        "TEST-PUMP-01": pump_machine,
        "TEST-COMP-01": comp_machine,
        "TEST-MOTOR-01": motor_machine,
        "TEST-FAN-01": fan_machine,
    }


@pytest.fixture
def display_guard_audit():
    """Parse a stylesheet layer the way `tests/js/harness.js` parses it.

    UX-4 F-3. `harness.js:122-151` reads `style.css` to decide whether a
    `hidden` element is really off screen, splitting **every selector on
    commas** so a grouped rule (`.cta,.btn-ghost{display:inline-flex}`)
    registers both classes. The UX-1 and UX-2 copies of that check matched a
    single bare class at column 0 instead, so a grouped rule slipped past them
    — the pins were weaker than the oracle they exist to protect.

    Single-sourced here rather than copied a third time: three regexes that are
    supposed to agree with `harness.js` and with each other are exactly how the
    drift happened. `tests/test_ux4_confirm.py` keeps its own inline copy for
    now — it is outside this session's scope, and can adopt this when it is
    next open.

    Returns `(declares, guarded)` — bare classes that declare a `display`, and
    bare classes carrying the `[hidden]{display:none}` guard.
    """
    def audit(layer: str) -> tuple[set[str], set[str]]:
        declares: set[str] = set()
        guarded: set[str] = set()
        for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", layer):
            for part in selectors.split(","):
                sel = part.strip()
                bare = re.fullmatch(r"\.([A-Za-z][\w-]*)", sel)
                if bare and re.search(r"(^|[;\s])display\s*:", body):
                    declares.add(bare.group(1))
                guard = re.fullmatch(r"\.([A-Za-z][\w-]*)\[hidden\]", sel)
                if guard and "display:none" in body.replace(" ", ""):
                    guarded.add(guard.group(1))
        return declares, guarded

    return audit



# ══════════════════════════════════════════════════════════════════════════
# Session SUITE-LOCK — law #9 made mechanical, and a hang made loud
#
# Law #9 (HANDOFF_2026-09-08.md §2): *sessions never run the full suite; the
# operator's `gate.sh` is the N/1 and it runs alone on the box.* Until this
# session that was a sentence in a document, and on Sep 7 it cost two reboots:
# four sessions each ran a full suite on one laptop and seven `_render_child`
# processes went to state `UE`, which `kill -9` does not clear; then ONE
# `gate.sh` run, alone on an idle box, wedged ~18 minutes on the untimed parent
# wait in `report/render_proc.py` (ROADMAP FINDING, Sep 7).
#
# Two mechanisms below, and they answer different halves:
#   * the LOCK stops a second full run starting at all;
#   * the TIMEOUTS stop any single run holding the box silently.
# The wait itself is fixed at the source, in `report/render_proc.py`.
# ══════════════════════════════════════════════════════════════════════════

import faulthandler
import fcntl
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

#: A run this big is a full run. MEASURED, not chosen: the full suite collects
#: 3228, and the widest *targeted* selection that could be constructed by hand
#: -- every webapp-, ux-, auth-, billing-, db-, account-, intake- and
#: inference-shaped file at once -- collects 1118. 2000 sits between them with
#: ~1.8x clearance below and ~1.6x above, so no plausible targeted run is
#: refused and no full run escapes. Recorded in outputs/SESSION_SUITELOCK.md
#: with the other selection counts.
FULL_RUN_MIN_TESTS = 2000

#: ONE machine-wide path, and deliberately NOT under `$TMPDIR`: macOS gives
#: each login session its own `/var/folders/...` TMPDIR, so two terminals would
#: take two different locks and the whole mechanism would be silently inert --
#: a false green of exactly the shape UX-4 F-1/F-5 recorded. `/tmp` is shared
#: by every process on the box, which is the property being relied on.
#:
#: Suffixed with the uid, opened `O_NOFOLLOW`, and created `0600`. `/tmp` is
#: world-writable and sticky and this name is predictable, so without
#: `O_NOFOLLOW` any local process could pre-create it as a symlink and have the
#: `ftruncate` below destroy whatever it pointed at. Per-uid because the runs
#: that must exclude each other all belong to one user -- the operator's gate
#: and the operator's sessions -- while `deploy.sh` (the `vibagent` user) and CI
#: are each alone on their own box anyway.
#: `scripts/gate/gate.sh` computes the same name with `id -u`.
SUITE_LOCK_PATH_TEMPLATE = "/tmp/vib-agent-suite-{uid}.lock"

#: The per-test bound. ONE bound, hard: on expiry every thread's stack is
#: dumped and the process exits. 900 s = 15 minutes, which catches the Sep 7
#: wedge (~18 minutes before a human noticed) while clearing the longest
#: WORST CASE any test in this suite declares for itself -- 720 s, from
#: `test_charts_threading.py`'s four sequential `join(timeout=180)` calls.
#:
#: WHY THERE IS NO SECOND, GRACEFUL BOUND -- REFUTED BY MEASUREMENT.
#: The design carried a SIGALRM bound that raised in the main thread, so one
#: test would fail and the suite would carry on. Measured on CPython 3.12.7,
#: that is not what happens when the main thread is inside `Thread.join(
#: timeout=...)`, which is precisely where every hang-prone test in this repo
#: waits (`test_render_proc.py`, `test_render_serial.py`,
#: `test_charts_threading.py`): `Thread._wait_for_tstate_lock`'s bpo-45274
#: handler sees its own `_tstate_lock` held -- held by the still-RUNNING
#: worker -- releases it and marks the thread stopped. The result is
#: `t.is_alive() == False` for a thread that is still running and still
#: holding `render_lock`, so `assert not t.is_alive()` PASSES on a stranded
#: worker and every later render blocks on a lock nothing will release.
#: A graceful bound that converts a hang into a silent pass plus a poisoned
#: lock is worse than no graceful bound. Pinned in
#: `tests/test_suite_lock.py::TestWhyThereIsNoGracefulBound`.
WEDGE_BACKSTOP_S = 900.0

_PYTEST_TIMEOUTS_ENV = "VIB_PYTEST_TIMEOUTS"


def _num_env(name: str, default: float) -> float:
    """Env override for one of the three bounds above.

    These exist because the pins must be able to DRIVE the mechanism: a suite
    lock whose threshold is 2000 and a timeout whose bound is 420 s cannot be
    exercised end to end inside a test that has to finish. `test_suite_lock.py`
    lowers them in child processes; the shipped defaults are pinned separately,
    by name and value, so lowering one here can never quietly become the
    product's behaviour. They are a seam, not a supported way to opt out --
    the way to run fewer tests is to run fewer tests.
    """
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def full_run_min_tests() -> int:
    return int(_num_env("VIB_SUITE_LOCK_MIN_TESTS", FULL_RUN_MIN_TESTS))


def _say(config, message: str) -> None:
    """Put a line where `gate.sh` will see it — its suite log is the artifact."""
    reporter = config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_line(message)
    else:  # pragma: no cover -- only without the terminal plugin
        print(message, file=sys.stderr)


def default_suite_lock_path() -> str:
    return SUITE_LOCK_PATH_TEMPLATE.format(uid=os.getuid())


def _suite_lock_path() -> Path:
    return Path(os.environ.get("VIB_SUITE_LOCK_PATH", default_suite_lock_path()))


def _holder_line() -> str:
    """What this run writes into the lock file for the NEXT one to read."""
    label = os.environ.get("VIB_SUITE_LOCK_LABEL", "pytest")
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"pid {os.getpid()} ({label}, cwd {Path.cwd()}, started {stamp})"


def _holder_of(path: Path) -> str:
    try:
        text = path.read_text(errors="replace").strip()
    except OSError:
        text = ""
    return text.splitlines()[0] if text else "an unidentified run"


def _inherited_lock_fd(path: Path) -> int | None:
    """The fd `gate.sh` passed down — VERIFIED, not taken on trust.

    `gate.sh` holds the lock on its own fd 9 and exports `VIB_SUITE_LOCK_FD`,
    because otherwise the gate would deadlock against the very suite it runs.
    The env var alone would be a bypass anyone could set by accident, so this
    `fstat`s the descriptor and compares it against the lock path's inode: a
    stale or wrong value simply falls through to a normal acquisition, and the
    run refuses as it should.
    """
    raw = os.environ.get("VIB_SUITE_LOCK_FD")
    if not raw:
        return None
    try:
        fd = int(raw)
        mine, theirs = os.fstat(fd), os.stat(path)
    except (ValueError, OSError):
        return None
    return fd if (mine.st_dev, mine.st_ino) == (theirs.st_dev, theirs.st_ino) else None


# ── the shard split (Session CI-1) ───────────────────────────────────────

#: Per-FILE weights in SECONDS, measured on a real runner by
#: `scripts/gate/shard_weights.py` from a `--durations=0` run, and entirely
#: optional -- absent, every file is weighted by its collected count instead.
#:
#: It exists because count-balance is the wrong unit here. Measured on this
#: tree, a pure count split puts `test_charts_threading.py`, `test_render_proc.py`,
#: `test_render_serial.py` AND `test_report_charts.py` in the same bin: they are
#: small files that cost minutes (crash/reap pins, four sequential
#: `join(timeout=180)`), while `test_intake_adversarial.py` is 149 tests that
#: cost milliseconds. The weights change BALANCE only -- membership is still a
#: partition either way, which is what the summary job actually asserts.
#:
#: Shape, so a generated file drops straight in and the units stay coherent:
#:     {"generated": "<iso stamp>", "per_test_default": 0.35,
#:      "files": {"tests/test_x.py": 12.3, ...}}
#: A file the JSON does not name is weighted `count * per_test_default`, so a
#: newly added test file is never weighted zero and never sinks one bin.
SHARD_WEIGHTS_PATH = Path(__file__).resolve().parents[1] / "scripts" / "gate" / "shard_weights.json"

#: Used only when the weights file is absent entirely, in which case every file
#: is weighted by count and the constant cancels out of every comparison.
_DEFAULT_PER_TEST_S = 1.0


def _parse_shard(raw: str) -> tuple[int, int]:
    """`"2/4"` -> `(2, 4)`. A UsageError on anything else, never a silent full run.

    A typo'd `--shard` that quietly ran everything would make one runner do
    four runners' work and still report a plausible number -- law #11's
    "measurement taken through a layer that can quietly return the wrong
    thing". So the parse refuses instead of falling back.
    """
    text = str(raw).strip()
    k_text, sep, n_text = text.partition("/")
    if not sep or not k_text.isdigit() or not n_text.isdigit():
        raise pytest.UsageError(f"--shard must be k/N with integer k and N, not {raw!r}")
    k, n = int(k_text), int(n_text)
    if n < 1 or not (1 <= k <= n):
        raise pytest.UsageError(f"--shard {raw!r}: need 1 <= k <= N and N >= 1")
    return k, n


def _shard_weights() -> tuple[dict[str, float], float]:
    """`(per-file seconds, per-test fallback)`. Never raises, never refuses.

    A weights file that is missing, unreadable or malformed degrades to pure
    count-balance rather than failing the run: it is an optimisation, and a
    shard that refuses to start because a JSON file drifted would be a worse
    outcome than a slow shard.
    """
    try:
        import json

        data = json.loads(SHARD_WEIGHTS_PATH.read_text())
    except (OSError, ValueError):
        return {}, _DEFAULT_PER_TEST_S
    if not isinstance(data, dict):
        return {}, _DEFAULT_PER_TEST_S

    raw_default = data.get("per_test_default", _DEFAULT_PER_TEST_S)
    per_test = (
        float(raw_default)
        if isinstance(raw_default, (int, float)) and raw_default > 0
        else _DEFAULT_PER_TEST_S
    )

    files = data.get("files")
    if not isinstance(files, dict):
        return {}, per_test
    out = {}
    for key, value in files.items():
        if isinstance(key, str) and isinstance(value, (int, float)) and value >= 0:
            out[key] = float(value)
    return out, per_test


def _shard_bins(items, n: int) -> list[list]:
    """Deterministic greedy bin-pack of whole FILES into `n` bins.

    Whole files, because module-scoped state is real here: the module autouse
    git fixture in `test_secrets_hygiene.py`, and the `render_lock`
    serialisation the `test_render_proc.py` / `test_charts_threading.py` /
    `test_render_serial.py` trio depends on. Splitting a file across runners
    would not fail -- it would pass, differently, which is worse.

    Determinism has to hold across runs AND across machines, so nothing here
    reads a hash seed, a clock, or the filesystem order: files are sorted by
    `(-weight, path)` and each goes to the lightest bin, ties to the lowest
    index. Same input, same split, everywhere.
    """
    groups: dict[str, list] = {}
    for item in items:
        groups.setdefault(item.nodeid.split("::")[0], []).append(item)

    measured, per_test = _shard_weights()

    def weight(path: str) -> float:
        return measured.get(path, len(groups[path]) * per_test)

    ordered = sorted(groups, key=lambda path: (-weight(path), path))

    bins: list[list] = [[] for _ in range(n)]
    loads = [0.0] * n
    for path in ordered:
        target = min(range(n), key=lambda i: (loads[i], i))
        bins[target].extend(groups[path])
        loads[target] += weight(path)
    return bins


def _apply_shard(config, items) -> None:
    """Keep this run's bin, deselect the rest. A no-op without `--shard`.

    `None` means the option was never given and is the only no-op. An EMPTY
    STRING means it was given and is empty, which is a refusal -- found by
    `test_ci_shard.py` while it was still `if not raw`. `--shard=$SHARD` with
    `SHARD` unset expands to exactly `--shard=`, and under the falsy test that
    ran the whole suite on one runner and reported a plausible number for it:
    a silent full run reached through an unset variable, which is the shape
    law #11 names.
    """
    raw = config.getoption("--shard")
    if raw is None:
        return
    k, n = _parse_shard(raw)
    if n == 1:
        _say(config, f"shard 1/1: identity — {len(items)} of {len(items)} tests")
        return

    keep = _shard_bins(items, n)[k - 1]
    keep_ids = {id(item) for item in keep}
    dropped = [item for item in items if id(item) not in keep_ids]
    total = len(items)

    # pytest's own accounting, not just ours: `pytest_deselected` is what makes
    # the run report "N deselected" rather than pretending the others were
    # never collected.
    if dropped:
        config.hook.pytest_deselected(items=dropped)
    items[:] = keep

    files = len({item.nodeid.split("::")[0] for item in keep})
    _say(config, f"shard {k}/{n}: {files} files, {len(keep)} of {total} tests")


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(session, config, items) -> None:
    """Take the machine-wide suite lock, but only for a full run.

    After collection rather than at session start, because the trigger IS the
    collected count and nothing has run yet either way. `--collect-only` never
    takes it: collection executes no tests and holds no renderer.

    **`trylast=True` is load-bearing, and was found by measurement.** pytest's
    own `-k`/`-m` filtering happens in its `pytest_collection_modifyitems`
    (`_pytest/mark/__init__.py`), and conftest plugins are called BEFORE it --
    so a plain hook here sees the unfiltered list. Measured on pytest 9.1.1: a
    plain hook reports 5 items for `pytest -k test_a` over a 5-test file, and
    `trylast=True` reports 1. Without it, every `pytest -k ...` over `tests/`
    would present 3228 items and be refused as a full run -- which is how
    sessions normally work, so the mechanism would have been unusable and
    everyone would have learned to unset the variable.
    """
    # PRE-shard, and that is the whole point. `--shard 1/4` over `tests/` is
    # still a full run of THIS BOX's renderers, so four shards started in
    # parallel on the operator's laptop must be refused exactly as four full
    # suites are -- otherwise `--shard` is a hole straight through law #9.
    # On CI each shard has a runner to itself and takes its own uncontended
    # lock, which costs nothing and records who is running.
    total_collected = len(items)
    _apply_shard(config, items)

    if config.option.collectonly or total_collected < full_run_min_tests():
        return

    path = _suite_lock_path()
    inherited = _inherited_lock_fd(path)
    if inherited is not None:
        # Same open file description as the shell's, so this is a no-op
        # re-lock rather than a second acquisition. The fd is gate.sh's; it
        # is not ours to close.
        fcntl.flock(inherited, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _say(config, f"suite lock: held by our caller via fd {inherited} ({path})")
        return

    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    except OSError as exc:
        # NEVER a refusal. `deploy/deploy.sh` runs this suite on the droplet as
        # the app user and is "the last thing between a broken commit and
        # production"; CI runs it too. A contention guard for one laptop does
        # not get to veto a deploy over a permissions problem on /tmp.
        _say(config, f"suite lock: SKIPPED, {path} is not usable ({exc}) — continuing")
        return

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        holder = _holder_of(path)
        os.close(fd)
        raise pytest.UsageError(
            f"another full test run holds {path}: {holder} — law #9: sessions "
            f"never run the full suite, the operator's gate.sh is the N/1 and "
            f"it runs alone on the box."
        )
    except OSError as exc:  # pragma: no cover -- e.g. a filesystem without flock
        os.close(fd)
        _say(config, f"suite lock: SKIPPED, {path} cannot be locked ({exc}) — continuing")
        return

    os.ftruncate(fd, 0)
    os.pwrite(fd, (_holder_line() + "\n").encode(), 0)
    # Kept open for the life of the session ON PURPOSE: the lock lives on the
    # open file description, so closing this fd would release it. Nothing needs
    # to clean up afterwards -- the kernel drops it when this process dies,
    # which is exactly right for the reboot-after-UE case.
    config._vib_suite_lock_fd = fd
    _say(config, f"suite lock: taken ({path}) — {total_collected} tests collected")


# ── the per-test backstop ────────────────────────────────────────────────

def _timeouts_enabled() -> bool:
    return os.environ.get(_PYTEST_TIMEOUTS_ENV) != "0"


_wedge_log = None


def _wedge_log_file():
    """Where the backstop writes its stack dump.

    A real file rather than stderr, because `exit=True` calls `_exit(1)`: under
    pytest's default fd-level capture, fd 2 is a capture buffer that is never
    flushed on that path, so a dump sent to stderr would be the one thing lost
    at the exact moment it was needed. This is also the reason for not simply
    setting pytest's built-in `faulthandler_timeout` ini option, which writes
    to a stashed stderr dup and hits that same problem (pytest issue #11572,
    cited in `_pytest/faulthandler.py` itself). Deleted at session end if still
    empty, so a clean run leaves nothing behind and the file's PRESENCE is the
    evidence.
    """
    global _wedge_log
    if _wedge_log is None:
        path = Path(os.environ.get(
            "VIB_WEDGE_LOG", f"/tmp/vib-agent-wedge-{os.getpid()}.log"))
        _wedge_log = path.open("w")
    return _wedge_log


def _arm_backstop() -> None:
    faulthandler.dump_traceback_later(
        _num_env("VIB_PYTEST_BACKSTOP_S", WEDGE_BACKSTOP_S),
        exit=True, file=_wedge_log_file())


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    """Bound every test — setup, call and teardown — with the wedge backstop.

    `faulthandler`'s timer runs on its own thread in C, so it fires even when
    the interpreter never comes back to Python, which is what a native or
    kernel wedge looks like and is exactly the Sep 7 shape. It dumps every
    thread's stack and exits, turning a silent 60-minute hold into evidence
    plus a free box.

    `hookwrapper=True` rather than a plain hook: `pytest_runtest_protocol` is
    `firstresult`, so a plain implementation returning non-None would REPLACE
    the protocol instead of wrapping it.
    """
    if not _timeouts_enabled():
        yield
        return
    _arm_backstop()
    try:
        yield
    finally:
        faulthandler.cancel_dump_traceback_later()


@pytest.hookimpl(trylast=True)
def pytest_exception_interact(node, call, report) -> None:
    """Re-arm the backstop that pytest's own faulthandler plugin just cancelled.

    `_pytest/faulthandler.py`'s `pytest_exception_interact` is `tryfirst` and
    calls `cancel_dump_traceback_later()` UNCONDITIONALLY — it does not check
    whether it was the one that armed the timer. `runner.py` calls that hook on
    every failing test, so without this the remainder of that test's protocol
    (its teardown, and any later phase) would run unprotected. `trylast` puts
    this after the cancel.
    """
    if _timeouts_enabled():
        _arm_backstop()


def pytest_sessionfinish(session, exitstatus) -> None:
    global _wedge_log
    if _wedge_log is not None:
        path = Path(_wedge_log.name)
        _wedge_log.close()
        _wedge_log = None
        try:
            if path.stat().st_size == 0:
                path.unlink()
        except OSError:
            pass

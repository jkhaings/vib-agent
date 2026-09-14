"""Session CI-1 — pins on the `--shard k/N` split in `tests/conftest.py`.

The split is precisely the kind of layer law #11 is about: one that can return
FEWER TESTS and still look green. A shard that silently dropped a bin would
report a smaller `N passed`, exit 0, and read as a healthy run — the same
shape as UX-4's control harness that ran zero tests and reported "nothing red".
So the mechanism gets a negative control of its own, on both halves:

  * that the four bins are a PARTITION of the collection — disjoint, exhaustive,
    and stable — rather than a filter that happens to return something;
  * that a MALFORMED `--shard` refuses rather than quietly running everything,
    which is how one runner would end up doing four runners' work and reporting
    a plausible number for it.

The same partition property is asserted a second time CI-side, over the real
run rather than a probe: the `summary` job compares the sum of the shards'
collected counts against `meta`'s own `--collect-only` total. Two proofs of one
property on purpose — this one is fast and runs everywhere, that one is taken
through the actual runners.

Child `pytest` processes follow `test_suite_lock.py`'s pattern, including its
F-5 lesson: `VIB_SUITE_LOCK_PATH` is set per test so a pin can never take the
operator's real lock.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]

#: Loaded BY PATH, exactly as `test_suite_lock.py` does it: `tests/conftest.py`
#: is not importable as `conftest` under pytest 9's default import mode.
_spec = importlib.util.spec_from_file_location(
    "_vib_shard_conftest", _ROOT / "tests" / "conftest.py")
suite_conftest = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(suite_conftest)

_KNOBS = (
    "VIB_SUITE_LOCK_PATH", "VIB_SUITE_LOCK_MIN_TESTS", "VIB_SUITE_LOCK_FD",
    "VIB_SUITE_LOCK_LABEL", "VIB_PYTEST_TIMEOUT_S", "VIB_PYTEST_BACKSTOP_S",
    "VIB_PYTEST_TIMEOUTS", "VIB_WEDGE_LOG",
)


class _Item:
    """The only thing `_shard_bins` reads off an item is its nodeid."""

    def __init__(self, nodeid: str) -> None:
        self.nodeid = nodeid

    def __repr__(self) -> str:  # pragma: no cover -- assertion messages only
        return f"<{self.nodeid}>"


def _corpus(files: int = 17, per_file: int = 7) -> list[_Item]:
    """A collection shaped like the real one: many files, uneven sizes."""
    items = []
    for f in range(files):
        for t in range((f % per_file) + 1):
            items.append(_Item(f"tests/test_probe_{f:02d}.py::TestA::test_{t}"))
    return items


def _run_pytest(target: Path, *args: str, **env_overrides) -> subprocess.CompletedProcess:
    # `--color=no` is load-bearing, not cosmetic. Callers below READ this
    # child's stdout -- `int(line.split(" passed")[0].split()[-1])` in
    # test_the_bins_of_a_real_run_add_back_up. pytest colourises on a TTY *or*
    # when FORCE_COLOR / PY_COLORS / CLICOLOR_FORCE is set, and then that
    # number arrives as '\x1b[32m\x1b[1m6', which int() refuses.
    #
    # Measured on master alone, ci2 not merged, Sep 9: with FORCE_COLOR=3 in
    # the parent's environment the pin FAILS; with it unset the same pin on
    # the same commit PASSES. It has been green in CI and on the operator's
    # terminal only because neither of those sets the variable -- an agent
    # session that does turns a green branch red, and the reader goes looking
    # at the shard splitter, which is exactly the false trail CI-2 §1 was
    # written to close.
    #
    # A FLAG rather than another `_KNOBS` entry: one argument covers all three
    # variables and a TTY too, and there is no list to keep in sync. `_KNOBS`
    # stays what it is -- the suite-lock and timeout knobs whose INHERITANCE
    # would perturb the child's behaviour, not its output encoding.
    env = {k: v for k, v in os.environ.items() if k not in _KNOBS}
    env["PYTHONPATH"] = f"{_ROOT / 'src'}{os.pathsep}{_ROOT / 'tests'}"
    env.update({k: str(v) for k, v in env_overrides.items()})
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "conftest", "-o", "addopts=",
         "--color=no", str(target), *args],
        capture_output=True, text=True, env=env, cwd=str(_ROOT), timeout=120,
    )


def _probe_tree(tmp_path: Path, files: int = 4, per_file: int = 2) -> Path:
    """`files` probe modules so the bin-packer has something to distribute.

    Whole FILES, because that is the unit it moves: one file with eight tests
    would land entirely in one bin and prove nothing about the split.
    """
    for f in range(files):
        body = "".join(f"def test_{t}():\n    assert True\n\n" for t in range(per_file))
        (tmp_path / f"test_probe_{f}.py").write_text(body)
    return tmp_path


# ══════════════════════════════════════════════════════════════════════════
# 1 · The split is a partition
# ══════════════════════════════════════════════════════════════════════════

class TestTheBinsAreAPartition:

    @pytest.mark.parametrize("n", [2, 3, 4, 8])
    def test_every_test_lands_in_exactly_one_bin(self, n):
        items = _corpus()
        bins = suite_conftest._shard_bins(items, n)

        seen = [i.nodeid for b in bins for i in b]
        assert sorted(seen) == sorted(i.nodeid for i in items), "the union is not the collection"
        assert len(seen) == len(set(seen)), "a test appears in more than one bin"

    def test_a_file_is_never_split_across_bins(self):
        """Module-scoped state is real here — `test_secrets_hygiene.py`'s
        autouse git fixture, and the `render_lock` serialisation the
        render_proc/render_serial/charts_threading trio depends on. A file cut
        in half across two runners would not fail; it would pass differently."""
        bins = suite_conftest._shard_bins(_corpus(), 4)
        owner = {}
        for index, bucket in enumerate(bins):
            for item in bucket:
                path = item.nodeid.split("::")[0]
                assert owner.setdefault(path, index) == index, f"{path} spans bins"

    def test_the_split_is_stable_across_calls(self):
        """Determinism has to hold across processes and machines, so the
        packer reads no hash seed, no clock and no directory order."""
        first = [[i.nodeid for i in b] for b in suite_conftest._shard_bins(_corpus(), 4)]
        second = [[i.nodeid for i in b] for b in suite_conftest._shard_bins(_corpus(), 4)]
        assert first == second

    def test_the_split_does_not_depend_on_collection_order(self):
        """Same set, different order in: same bins out. pytest's collection
        order is filesystem-dependent, so a packer that keyed on position would
        put a test in different shards on macOS and Linux."""
        items = _corpus()
        forward = {i.nodeid: b for b, bucket in enumerate(suite_conftest._shard_bins(items, 4))
                   for i in bucket}
        backward = {i.nodeid: b for b, bucket in enumerate(
            suite_conftest._shard_bins(list(reversed(items)), 4)) for i in bucket}
        assert forward == backward

    def test_no_bin_is_empty_when_there_are_more_files_than_bins(self):
        """An empty bin means a runner that collected 0 tests, which the
        `summary` job treats as red — so it must not be reachable by the split
        itself on any realistic collection."""
        bins = suite_conftest._shard_bins(_corpus(files=17), 4)
        assert all(bucket for bucket in bins), [len(b) for b in bins]


# ══════════════════════════════════════════════════════════════════════════
# 2 · Weights change balance, never membership
# ══════════════════════════════════════════════════════════════════════════

class TestWeightsAreAnOptimisationOnly:

    def test_a_missing_or_malformed_weights_file_degrades_to_count_balance(
        self, monkeypatch, tmp_path
    ):
        """It is an optimisation. A shard that REFUSED TO START because a JSON
        file drifted would be a worse outcome than a slow shard."""
        for content in (None, "{not json", '["a list"]', '{"files": "not a map"}'):
            path = tmp_path / "w.json"
            if content is None:
                path.unlink(missing_ok=True)
            else:
                path.write_text(content)
            monkeypatch.setattr(suite_conftest, "SHARD_WEIGHTS_PATH", path)
            measured, per_test = suite_conftest._shard_weights()
            assert measured == {}
            assert per_test > 0

    def test_weights_move_a_file_between_bins_without_breaking_the_partition(
        self, monkeypatch, tmp_path
    ):
        items = _corpus()
        unweighted = suite_conftest._shard_bins(items, 4)

        heavy = sorted({i.nodeid.split("::")[0] for i in items})[0]
        path = tmp_path / "w.json"
        path.write_text(f'{{"per_test_default": 0.1, "files": {{"{heavy}": 9999.0}}}}')
        monkeypatch.setattr(suite_conftest, "SHARD_WEIGHTS_PATH", path)
        weighted = suite_conftest._shard_bins(items, 4)

        assert sorted(i.nodeid for b in weighted for i in b) == \
               sorted(i.nodeid for b in unweighted for i in b), "weights changed membership"
        assert [len(b) for b in weighted] != [len(b) for b in unweighted], \
            "a 9999s file did not move anything — the weights are not being read"


# ══════════════════════════════════════════════════════════════════════════
# 3 · A malformed --shard REFUSES — the negative control
# ══════════════════════════════════════════════════════════════════════════

class TestAMalformedShardIsNeverASilentFullRun:

    @pytest.mark.parametrize("bad", ["bogus", "2", "0/4", "5/4", "2/0", "abc/4", "", "1/4/9"])
    def test_it_exits_four_instead_of_running_everything(self, tmp_path, bad):
        """pytest exits 4 on a UsageError. The failure this rules out is the
        quiet one: `--shard 24` (a typo for `2/4`) running the WHOLE suite on
        one runner and reporting a number that looks fine."""
        tree = _probe_tree(tmp_path)
        r = _run_pytest(tree, f"--shard={bad}", VIB_SUITE_LOCK_PATH=tmp_path / "lock")
        assert r.returncode == 4, r.stdout + r.stderr
        assert "8 passed" not in r.stdout, "a malformed --shard ran the whole selection"

    def test_shard_one_of_one_is_an_identity_pass(self, tmp_path):
        tree = _probe_tree(tmp_path)
        r = _run_pytest(tree, "--shard=1/1", VIB_SUITE_LOCK_PATH=tmp_path / "lock")
        assert r.returncode == 0, r.stdout + r.stderr
        assert "8 passed" in r.stdout, r.stdout

    def test_the_bins_of_a_real_run_add_back_up(self, tmp_path):
        """End to end through pytest rather than through `_shard_bins`: four
        child runs over one tree, and their passed counts must sum to the
        unsharded count. This is the probe-scale twin of what the `summary`
        job asserts over the real runners."""
        tree = _probe_tree(tmp_path, files=8, per_file=3)
        whole = _run_pytest(tree, VIB_SUITE_LOCK_PATH=tmp_path / "lock")
        assert "24 passed" in whole.stdout, whole.stdout

        total = 0
        for k in (1, 2, 3, 4):
            r = _run_pytest(tree, f"--shard={k}/4", VIB_SUITE_LOCK_PATH=tmp_path / "lock")
            assert r.returncode == 0, r.stdout + r.stderr
            line = [l for l in r.stdout.splitlines() if " passed" in l][-1]
            total += int(line.split(" passed")[0].split()[-1])
        assert total == 24, f"the four shards summed to {total}, not 24"


# ══════════════════════════════════════════════════════════════════════════
# 4 · The shard does not open a hole in law #9
# ══════════════════════════════════════════════════════════════════════════

class TestTheLockStillSeesAFullRun:

    def test_the_lock_trigger_reads_the_PRE_shard_count(self, tmp_path):
        """`--shard 1/4` over `tests/` is still a full run of THIS BOX's
        renderers. Four shards started in parallel on the operator's laptop
        must be refused exactly as four full suites are — Sep 7 was seven
        wedged `_render_child` processes and two reboots, and nothing about
        that changes because the runs each collect a quarter of the tests.

        The discriminator: 8 collected, threshold 5, shard 1/4 keeps 2. If the
        trigger read the POST-shard count it would see 2 < 5 and take nothing.
        """
        tree = _probe_tree(tmp_path)
        lock = tmp_path / "suite.lock"
        r = _run_pytest(
            tree, "--shard=1/4",
            VIB_SUITE_LOCK_PATH=lock, VIB_SUITE_LOCK_MIN_TESTS=5,
        )
        assert r.returncode == 0, r.stdout + r.stderr
        assert "suite lock: taken" in r.stdout, r.stdout
        assert "8 tests collected" in r.stdout, \
            "the lock reported the shard's count, not the full collection"

    def test_a_targeted_run_under_the_threshold_still_takes_nothing(self, tmp_path):
        """The negative control on the pin above: same tree, same shard, a
        threshold the PRE-shard count cannot reach. Without this, 'the lock was
        taken' would prove nothing about which count it read."""
        tree = _probe_tree(tmp_path)
        r = _run_pytest(
            tree, "--shard=1/4",
            VIB_SUITE_LOCK_PATH=tmp_path / "suite.lock", VIB_SUITE_LOCK_MIN_TESTS=99,
        )
        assert r.returncode == 0, r.stdout + r.stderr
        assert "suite lock" not in r.stdout, r.stdout

    def test_collect_only_never_takes_it_even_sharded(self, tmp_path):
        tree = _probe_tree(tmp_path)
        r = _run_pytest(
            tree, "--shard=1/4", "--collect-only",
            VIB_SUITE_LOCK_PATH=tmp_path / "suite.lock", VIB_SUITE_LOCK_MIN_TESTS=1,
        )
        assert r.returncode == 0, r.stdout + r.stderr
        assert "suite lock: taken" not in r.stdout, r.stdout

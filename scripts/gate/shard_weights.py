#!/usr/bin/env python3
"""Build `scripts/gate/shard_weights.json` from pytest `--durations=0` logs.

    python scripts/gate/shard_weights.py [--run <id>] shard-*/pytest.out

Session CI-1. `tests/conftest.py::_shard_bins` splits the suite by whole files
into N bins, and without this file it balances by COLLECTED COUNT -- which is
the wrong unit. Measured on this tree, a count split puts
`test_charts_threading.py`, `test_render_proc.py`, `test_render_serial.py` and
`test_report_charts.py` all in bin 4: four small files whose tests cost minutes
apiece (crash/reap pins, four sequential `join(timeout=180)`), while
`test_intake_adversarial.py` is 149 tests that cost milliseconds. Seconds are
the unit that matters, and only a real run can supply them.

So the sequence is: run CI once with `--durations=0` (count-balanced), feed the
four shard logs to this script, commit the JSON, run CI again. The second run
is the balanced one. THE WEIGHTS ARE AN OPTIMISATION AND NOTHING MORE -- the
split is a partition either way, which is the property the summary job asserts.

Reads the "slowest durations" section, which pytest emits as

    12.34s call     tests/test_x.py::TestY::test_z
     0.01s setup    tests/test_x.py::TestY::test_z

and sums call+setup+teardown per FILE, because the file is the unit the
bin-packer moves. `--durations=0` (not `--durations=N`) is required: any N
truncates the list, and a truncated list would silently under-weight every file
whose tests are individually fast but collectively slow -- the exact error this
script exists to correct.
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_OUT = _ROOT / "scripts" / "gate" / "shard_weights.json"

#: `12.34s call tests/a.py::T::t`. The phase must be one of the three pytest
#: emits; anything else is a line that merely looks like a duration.
_LINE = re.compile(r"^\s*([0-9]+\.[0-9]+)s\s+(call|setup|teardown)\s+(\S+?)::")


def collect(paths: list[Path]) -> tuple[dict[str, float], int]:
    seconds: dict[str, float] = defaultdict(float)
    tests: dict[str, set[str]] = defaultdict(set)
    for path in paths:
        for line in path.read_text(errors="replace").splitlines():
            match = _LINE.match(line)
            if match is None:
                continue
            value, _phase, file_part = match.groups()
            seconds[file_part] += float(value)
            tests[file_part].add(line.split(None, 2)[2])
    return dict(seconds), sum(len(v) for v in tests.values())


def main(argv: list[str]) -> int:
    # `--run <id>` is provenance, not behaviour. Session CI-2 found the
    # `source` field could not identify its own source: the workflow uploads
    # every shard's capture as `pytest.out` inside `shard-<k>/`, so `p.name`
    # for all four is the string "pytest.out" and the committed JSON recorded
    # `["pytest.out", "pytest.out", "pytest.out", "pytest.out"]`. A provenance
    # field that cannot tell you which run, or even which shard, is a comment
    # pretending to be a receipt. The parent directory goes in below, and the
    # run id goes in here.
    run = None
    argv = list(argv)
    if "--run" in argv:
        i = argv.index("--run")
        if i + 1 >= len(argv):
            print("error: --run needs a value (the GitHub run id)", file=sys.stderr)
            return 2
        run = argv[i + 1]
        del argv[i:i + 2]

    if not argv:
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        print("error: give one or more pytest logs written with --durations=0", file=sys.stderr)
        return 2

    paths = [Path(a) for a in argv]
    missing = [p for p in paths if not p.is_file()]
    if missing:
        for p in missing:
            print(f"error: no such log: {p}", file=sys.stderr)
        return 2

    seconds, n_entries = collect(paths)
    if not seconds:
        # A REFUSAL, not an empty file. An empty `files` map would be written,
        # committed, and then weight every file at `per_test_default` -- i.e.
        # silently revert to count-balance while looking measured. Law #11.
        print(
            "error: no duration lines found. `--durations=0` must be on the pytest\n"
            "       command line (a plain `--durations=N` truncates the list, and\n"
            "       `-q` alone prints none at all).",
            file=sys.stderr,
        )
        return 1

    total = sum(seconds.values())
    per_test = round(total / n_entries, 4) if n_entries else 1.0
    payload = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run": run,
        # `shard-1/pytest.out`, not `pytest.out` -- see --run above.
        "source": [f"{p.parent.name}/{p.name}" for p in paths],
        "per_test_default": per_test,
        "files": {k: round(v, 3) for k, v in sorted(seconds.items())},
    }
    _OUT.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n")

    print(f"{_OUT.relative_to(_ROOT)}: {len(seconds)} files, {total:.1f}s total")
    print(f"  per_test_default {per_test}s over {n_entries} timed phases")
    for name, value in sorted(seconds.items(), key=lambda kv: -kv[1])[:8]:
        print(f"  {value:8.1f}s  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

"""Write the Fmax-2 kHz outreach sample as three uploadable CSVs — keyless, deterministic.

    PYTHONPATH=src ./.venv/bin/python -m scripts.make_sample_csv            # writes OUT
    PYTHONPATH=src ./.venv/bin/python -m scripts.make_sample_csv /some/dir  # elsewhere

Session REPORT-2. A CAT analyst reviewing the outreach sample (`scripts/make_sample.py`,
`make_case("bpfo", seed=1)` — Fmax 500 Hz) said 500 Hz cannot evaluate the bearing
harmonics and asked for a higher Fmax. `make_case` now takes `fmax`/`lines`; this script
builds the SAME recipe — same machine, same 6206 bearing, same 1800 rpm, same seeded
peaks, same seed — at Fmax 2000 Hz with 8000 lines, so Δf stays at the 0.25 Hz the
500 Hz sample had, and writes it in the one shape the web form's spectrum lane reads:
`freq_hz,amplitude`, one file per direction.

Two things are done to the generator's arrays on the way out, both stated in the README
this script writes beside the files, because a CSV cannot carry them any other way:

  * A running-speed (1×) line at 30.0 Hz is added on every axis. The intake's
    cross-file speed check (webapp/assembly.py::_has_shaft_content) refuses a trio in
    which no channel carries a peak near k×shaft, and the seeded recipe has none — its
    peaks are the bearing tones. The line is deliberately small: below the route
    profile's amplitude floor (12.73× the spectrum mean), so it satisfies the intake
    check (≥3× the median) without reaching any shaft-order evidence set — the
    committed differential stays exactly what the 500 Hz sample's is. A real machine
    always shows one.
  * Each axis is scaled uniformly so the Parseval sum of its bins equals the recipe's
    stated overall velocity (adapters/uploads/tabular.py derives the ISO severity from
    that sum, and the generator states velocity separately from its spectrum). Uniform
    scaling leaves every peak-to-mean ratio, and therefore every detector decision,
    where it was.

The operator renders the PDF through the app: upload the three files through the live
form with the values the README lists. `outputs/demo_package/bpfo_synthetic/` — the
old sample — is not touched by this script.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))

from vib_agent.config import load_config, load_thresholds  # noqa: E402
from vib_agent.models import Case  # noqa: E402
from vib_agent.synth.generator import _add_peak, make_case  # noqa: E402

#: `route` is named explicitly (see scripts/make_sample.py for why).
PROFILE = "route"
OUT = _REPO / "outputs" / "demo_package" / "bpfo_synthetic_fmax2000"

FMAX_HZ = 2000.0
LINES = 8000  # Δf = 0.25 Hz — the 500 Hz sample's own resolution, kept
SEED = 1  # the seed scripts/make_sample.py uses
RPM = 1800.0
SHAFT_LINE_HZ = RPM / 60.0  # 30 Hz
#: Pre-scale amplitude of the added 1× line. ~7× the noise median (the intake
#: check needs ≥3×) and ~half the route amplitude floor (12.73× the mean).
SHAFT_LINE_AMP = 0.005

#: (file stem, generator axis) — the web form's direction → axis map
#: (webapp/assembly.py::DIRECTION_TO_AXIS: axial→x, radial_h→y, radial_v→z).
FILES: tuple[tuple[str, str], ...] = (("radial_h", "y"), ("radial_v", "z"), ("axial", "x"))

#: The form values the README states. `Case.machine` carries group/support/bearing.
FORM_VALUES: tuple[tuple[str, str], ...] = (
    ("Running speed", f"{RPM:.0f} rpm"),
    ("ISO group", "2 (medium, 15–300 kW)"),
    ("Support", "rigid"),
    ("Bearing model", "6206"),
    ("Velocity unit", "mm/s"),
    ("Detection", "RMS"),
    ("Mode", "spectrum (freq_hz,amplitude)"),
)


def build_case() -> Case:
    """The 2 kHz twin of the outreach sample, before the CSV-only adjustments."""
    iso_table = load_config("iso_zones")["zones"]
    thresholds = load_thresholds(PROFILE)
    return make_case("bpfo", iso_table=iso_table, thresholds=thresholds, seed=SEED,
                     fmax=FMAX_HZ, lines=LINES)


def axis_rows(case: Case, axis: str) -> list[tuple[float, float]]:
    """(freq_hz, amplitude) rows for one axis: the generator's spectrum plus the 1×
    line, scaled so the Parseval sum equals the recipe's stated velocity.

    The generator's grid is `i * fmax / lines` for i < lines, so its last bin sits
    one Δf short of Fmax (1999.75 Hz). A CSV carries no `fmax_hz` field — the
    upload lane reads Fmax as the top bin — so one closing bin at exactly Fmax is
    appended, at the noise level of the bin before it, and the file spans
    0–2000 Hz inclusive (8001 rows). Nothing about the tones moves.
    """
    spectrum = case.spectra[axis]
    freqs = list(spectrum.freq_hz) + [FMAX_HZ]
    amps = list(spectrum.amplitude)
    amps.append(amps[-1])
    _add_peak(amps, SHAFT_LINE_HZ, SHAFT_LINE_AMP, FMAX_HZ, LINES)
    velocity = getattr(case.sensor_data, f"{axis}_velocity_mm_sec")
    if not velocity:
        raise ValueError(f"the recipe states no velocity for axis {axis}")
    scale = velocity / math.sqrt(sum(a * a for a in amps))
    return [(f, a * scale) for f, a in zip(freqs, amps)]


def csv_text(rows: list[tuple[float, float]]) -> str:
    lines = ["freq_hz,amplitude"]
    lines += [f"{f:g},{a:.6g}" for f, a in rows]
    return "\n".join(lines) + "\n"


def readme_text(case: Case) -> str:
    velocities = {axis: getattr(case.sensor_data, f"{axis}_velocity_mm_sec") for _, axis in FILES}
    files = "\n".join(
        f"| `{stem}.csv` | {stem.replace('_', ' – ').replace('radial – h', 'radial – horizontal').replace('radial – v', 'radial – vertical')} "
        f"| `{axis}` | {velocities[axis]:.2f} mm/s RMS |"
        for stem, axis in FILES
    )
    form = "\n".join(f"| {k} | {v} |" for k, v in FORM_VALUES)
    return f"""# Outreach sample — {case.machine.name}, Fmax {FMAX_HZ:g} Hz

Session REPORT-2. The 500 Hz sample (`../bpfo_synthetic/`, `scripts/make_sample.py`) was
reviewed by a CAT analyst, who said 500 Hz cannot evaluate the bearing harmonics. These files
are the SAME synthetic case — `make_case("bpfo", seed={SEED})`: {case.machine.name},
{RPM:.0f} rpm, bearing {case.machine.bearing.model}, ISO group {case.machine.iso_group} /
{case.machine.iso_support} support, the same seeded BPFO tones — regenerated at
Fmax {FMAX_HZ:g} Hz with {LINES} lines (Δf 0.25 Hz; {LINES + 1} rows, 0–{FMAX_HZ:g} Hz
inclusive, the closing bin added so the upload lane reads Fmax as {FMAX_HZ:g} Hz), written
by `scripts/make_sample_csv.py`. The old sample is untouched.

**The operator renders the PDF through the app.** Upload the three files through the live
form, one per direction slot:

| file | direction slot | generator axis | overall (Parseval) |
|---|---|---|---|
{files}

with these form values:

| field | value |
|---|---|
{form}

A single upload of `radial_h.csv` alone gives the same committed diagnosis; the trio also
engages the axial-ratio and radial-dominance rules exactly as the NCD path does.

## What a CSV cannot carry, and what was done about it

* **A 1× running-speed line at {SHAFT_LINE_HZ:g} Hz was added on every axis** (pre-scale
  amplitude {SHAFT_LINE_AMP:g} against 0.36 at BPFO). The intake's cross-file speed check
  refuses a trio in which no channel carries a peak near k×shaft, and the seeded recipe has
  none. The line sits below the route profile's amplitude floor, so it satisfies the intake
  check without reaching any shaft-order evidence set; the committed differential is the
  500 Hz sample's. A real machine always shows one.
* **Each axis is scaled uniformly so the Parseval sum of its bins equals the recipe's
  stated overall velocity** (the spectrum lane derives ISO severity from that sum; the
  generator states velocity separately). Uniform scaling leaves every peak-to-mean ratio,
  and so every detector decision, unchanged.

Expected on upload: ISO Zone D ({velocities['y']:.2f} mm/s RMS on the radial-horizontal
channel against the 4.5 mm/s Zone C/D boundary), one committed finding — bearing
outer-race fault (BPFO) at 107.25 Hz (3.58×) — and a frequency-range line reading
"BPFO … visible to 18×". The regenerated CSVs are byte-identical on every run
(`tests/test_report2_sample_csv.py`).
"""


def write_sample(out_dir: Path) -> list[Path]:
    """Write the three CSVs and the README into `out_dir`; returns the paths written."""
    case = build_case()
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for stem, axis in FILES:
        path = out_dir / f"{stem}.csv"
        path.write_text(csv_text(axis_rows(case, axis)))
        written.append(path)
    readme = out_dir / "README.md"
    readme.write_text(readme_text(case))
    written.append(readme)
    return written


def main(argv: list[str]) -> int:
    out_dir = Path(argv[1]).resolve() if len(argv) > 1 else OUT
    for path in write_sample(out_dir):
        print(f"wrote {path.relative_to(_REPO) if path.is_relative_to(_REPO) else path}"
              f"  ({path.stat().st_size:,} bytes)")
    print("This script writes the CSVs only. The PDF is rendered through the app — see README.md.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

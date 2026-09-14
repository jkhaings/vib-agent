"""Write the multi-location route sample as 13 uploadable CSVs — keyless, deterministic.

    PYTHONPATH=src ./.venv/bin/python -m scripts.make_multi_sample            # writes OUT
    PYTHONPATH=src ./.venv/bin/python -m scripts.make_multi_sample /some/dir  # elsewhere

Session FIXTURE-1. Every fixture this repo owns is one machine at ONE measurement
point — `scripts/make_sample_csv.py`'s trio is a single location in three directions.
That is not what an analyst walks. A route is a machine TRAIN with a point at each
bearing, and the diagnostic act is deciding which point the fault is at, which is
exactly the judgement a single-point fixture cannot exercise.

So: one compressor train, 1800 rpm, bearing 6206, FOUR points — Motor DE, Motor NDE,
Compressor DE, Compressor NDE — each in three directions (H, V, axial), at Fmax
2000 Hz with 12,800 lines (the analyst's own resolution practice). A BPFO is planted
at Compressor DE and NOWHERE else. Every other point is healthy, in ISO Zone A or B.

Plus a 13th file, which is the one that is NOT a fault: `compressor_nde_h` again with
a monotone low-frequency rise added on top — a "ski slope", the integration artifact
that fakes severity. Its overall reads ~20 mm/s against the same point's true
1.20 mm/s. It is named at length on purpose; once uploaded, the filename is the only
label that travels with the file, because the webapp stores every upload as
`upload.<ext>`.

ONE MACHINE, TWO RECIPES, AND WHY THAT IS NOT TWO MACHINES. The faulted point is
`make_case("bpfo")` (`_comp_with_bearing()` — 1800 rpm, 6206) and the healthy points
are `make_case("healthy")`, whose recipe template happens to be `_pump()`. That
template never reaches the product: a CSV carries no machine metadata at all, so
every machine fact — type, speed, ISO group, support, bearing — comes from the upload
form, and the README below states one compressor for all four points. What the
healthy recipe contributes is its spectrum ARRAYS, nothing else.

Three things are done to the generator's arrays on the way out, each stated in the
README this script writes beside the files, because a CSV cannot carry them:

  * A running-speed (1x) line at 30.0 Hz on every file, `SHAFT_LINE_AMP` — REPORT-2's
    mechanism, reused unchanged. The intake's cross-file speed check
    (webapp/assembly.py::_has_shaft_content) refuses a trio in which no channel
    carries a peak near k x shaft, and neither seeded recipe has one. Measured at
    12,800 lines: it stands 7.6-7.8x the spectrum median on the twelve machine files
    (the check needs >= 3x), 16.9x on the ski-slope file, and stays BELOW the route
    profile's amplitude floor on all 13 — so it satisfies the intake without entering
    any shaft-order evidence set.
  * A WEAK axial BPFO at Compressor DE only. The `bpfo` recipe plants 107.16 Hz on the
    two radial axes and nothing on the axial one, but a real outer-race defect shows a
    little axially. `AXIAL_BPFO_AMP` is measured to land at 9.3x the median and 72% of
    the amplitude floor — visible on the figure, not committed evidence. The radial
    tones sit 35-40x OVER that floor, which is what "clearly on H and V, weak on A"
    means numerically.

Every ratio quoted above is a module constant below, rendered into the README from
the same name the pins assert against, so the prose cannot drift from the data.
  * Each file is scaled uniformly so the Parseval sum of its bins equals the overall
    velocity its point states (adapters/uploads/tabular.py derives ISO severity from
    that sum). Uniform scaling leaves every peak-to-mean ratio, and therefore every
    detector decision, where it was.

The operator renders the PDFs through the app: four separate jobs, one per point,
three files each — a job takes at most three files, so there is no twelve-channel
upload. See README.md for the form values.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import NamedTuple

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))

from vib_agent.config import load_config, load_thresholds  # noqa: E402
from vib_agent.models import Case  # noqa: E402
from vib_agent.synth.generator import _add_peak, make_case  # noqa: E402

#: `route` is named explicitly (see scripts/make_sample.py for why).
PROFILE = "route"
OUT = _REPO / "outputs" / "demo_package" / "multi_location"

FMAX_HZ = 2000.0
#: 12,800 lines at Fmax 2000 Hz -> df = 0.15625 Hz. The analyst's own practice, and
#: 192 bins per shaft order (the intake's floor is 2).
LINES = 12800
RPM = 1800.0
SHAFT_LINE_HZ = RPM / 60.0  # 30 Hz, exactly on bin 192
#: Pre-scale amplitude of the added 1x line. REPORT-2's constant, re-measured at
#: 12,800 lines: 7.7-9.3x the median (intake needs >= 3x), and sub-floor everywhere.
SHAFT_LINE_AMP = 0.005

#: The seeded recipe's outer-race tone. At 12,800 lines it quantises to bin 686,
#: i.e. 107.1875 Hz -- NOT the 107.25 Hz the 8,000-line sample reports. Pins that
#: carry a literal frequency must use the 12,800-line value.
BPFO_HZ = 107.16
BPFO_BIN_HZ = round(BPFO_HZ / FMAX_HZ * LINES) * FMAX_HZ / LINES  # 107.1875
#: Prose needs the same care the CSV column does: `{BPFO_BIN_HZ:g}` prints 107.188,
#: six significant figures again, which is a number this set does not contain.
BPFO_BIN_TEXT = f"{BPFO_BIN_HZ:.4f}"

#: `../bpfo_synthetic_fmax2000/`, for the one sentence that compares against it.
#: Stated, not derived from LINES -- it is a different script's constant.
SIBLING_LINES = 8000
#: Pre-scale amplitude of the weak axial BPFO at Compressor DE.
AXIAL_BPFO_AMP = 0.006

# ── the measured ratios the README quotes ────────────────────────────────────
# Stated here once and rendered from here, so the prose and the pins read the same
# constants. `tests/test_fixture1_multi_location.py` asserts the files actually land
# inside these bands, which is what stops the README asserting a number the data
# stopped having. (It already did once: an earlier draft claimed the 1x line stood
# "7.7-9.3x the median", conflating it with the axial BPFO's 9.3x. The 1x line is
# 7.6-7.8x on the twelve machine files -- and 16.9x on the ski-slope file, where the
# artifact adds to the bin. Every number below is measured, not chosen.)

#: 1x line vs the spectrum median, on the twelve machine files.
SHAFT_LINE_MEDIAN_BAND = (7.6, 7.8)
#: ...and on the ski-slope file, where the artifact lifts the same bin.
SKI_SLOPE_SHAFT_LINE_MEDIAN = 16.9
#: 1x line vs the route amplitude floor: sub-floor on all 13, by a clear margin.
SHAFT_LINE_FLOOR_CEILING = 0.65
#: The weak axial BPFO at Compressor DE: seen, not committed.
AXIAL_BPFO_MEDIAN_RATIO = 9.3
AXIAL_BPFO_FLOOR_RATIO = 0.72
#: The radial BPFO tones at Compressor DE, over the same floor.
RADIAL_BPFO_FLOOR_BAND = (35.0, 40.0)

#: The ski slope: `1 / (1 + (f/knee)^2)`, strictly decreasing in f, so it can never
#: introduce a local maximum and can never be read as a tone. 4 Hz keeps the artifact
#: in the bottom 2% of the span -- steepest at DC, the way a real one is -- and leaves
#: the 1x line the dominant discrete feature at 30 Hz.
SKI_SLOPE_KNEE_HZ = 4.0
SKI_SLOPE_TARGET_MM_S = 20.0

#: What every file in this set IS, declared. PART-C section D point 2 recorded the
#: public 500 Hz sample printing "Envelope spectrum (as supplied)" -- type stated,
#: unit never stated anywhere -- because the generator declares no unit for its array.
#: A CSV cannot carry one either (adapters/uploads/tabular.py::parse_spectrum reads
#: exactly `freq_hz,amplitude`, and a `# unit:` line would become a garbage data row),
#: so the declaration lives in the two places the intake actually reads: these form
#: values, and the per-file table in the README.
QUANTITY = "velocity spectrum"
UNIT = "mm/s RMS"
VELOCITY_UNIT_FORM = "mm_s"
DETECTION_FORM = "rms"
MODE_FORM = "spectrum"

#: (suffix, direction form value, generator axis, README label). The direction ->
#: axis map is webapp/assembly.py::DIRECTION_TO_AXIS: axial->x, radial_h->y,
#: radial_v->z.
DIRECTIONS: tuple[tuple[str, str, str, str], ...] = (
    ("h", "radial_h", "y", "radial - horizontal"),
    ("v", "radial_v", "z", "radial - vertical"),
    ("a", "axial", "x", "axial"),
)

ISO_GROUP = "2"
ISO_SUPPORT = "rigid"
BEARING_MODEL = "6206"
MACHINE_ALIAS = "Synthetic Compressor Train 01"
MACHINE_TYPE = "compressor"
#: There is no kW field on the form. ISO group is what carries machine size, and
#: group 2 IS the 15-300 kW band -- so the rating is stated here and the README says
#: which field an analyst actually types it into.
MACHINE_KW = 75.0


#: NamedTuple and not a dataclass, deliberately. Under `from __future__ import
#: annotations`, `dataclasses._is_type` resolves string annotations through
#: `sys.modules[cls.__module__]` -- which is None for a module loaded by path
#: without being registered, and that is exactly the idiom the repo's pins use for
#: `scripts/` (it is not a package; see tests/test_report2_sample_csv.py:40-44).
#: A dataclass here raises `AttributeError: 'NoneType' object has no attribute
#: '__dict__'` at import time, in the test rather than in the script. Measured.
class Point(NamedTuple):
    """One measurement point on the train."""

    key: str
    label: str
    recipe: str          # make_case() recipe name
    seed: int            # its own seed: four points sharing one would differ only
    #                      by a scale factor, which is not what a route looks like
    overalls: dict[str, float]   # direction suffix -> overall mm/s RMS
    planted: str


POINTS: tuple[Point, ...] = (
    Point("motor_de", "Motor DE", "healthy", 1,
          {"h": 1.10, "v": 0.90, "a": 0.50}, "nothing - healthy"),
    Point("motor_nde", "Motor NDE", "healthy", 2,
          {"h": 1.80, "v": 1.40, "a": 0.60}, "nothing - healthy"),
    Point("compressor_de", "Compressor DE", "bpfo", 3,
          {"h": 5.20, "v": 4.80, "a": 0.50},
          f"BPFO {BPFO_BIN_TEXT} Hz + 2x - clearly on H and V, weak on A"),
    Point("compressor_nde", "Compressor NDE", "healthy", 4,
          {"h": 1.20, "v": 1.00, "a": 0.55}, "nothing - healthy"),
)

#: The ski-slope file: Compressor NDE H again, artifact added on top.
SKI_SLOPE_POINT = "compressor_nde"
SKI_SLOPE_DIRECTION = "h"
SKI_SLOPE_STEM = "compressor_nde_h_ski_slope_artifact_not_a_fault"
SKI_SLOPE_PLANTED = (
    f"NOT A FAULT - a low-frequency integration artifact lifting the overall to "
    f"{SKI_SLOPE_TARGET_MM_S:g} mm/s"
)


class SampleFile(NamedTuple):
    """One written CSV, and everything the README and the pins say about it."""

    stem: str
    point_key: str
    point_label: str
    direction: str       # the form value
    direction_label: str
    axis: str
    overall_mm_s: float
    quantity: str
    unit: str
    planted: str
    ski_slope: bool


def _manifest() -> tuple[SampleFile, ...]:
    files: list[SampleFile] = []
    for point in POINTS:
        for suffix, direction, axis, label in DIRECTIONS:
            files.append(SampleFile(
                stem=f"{point.key}_{suffix}", point_key=point.key,
                point_label=point.label, direction=direction, direction_label=label,
                axis=axis, overall_mm_s=point.overalls[suffix], quantity=QUANTITY,
                unit=UNIT, planted=point.planted, ski_slope=False))
    base = next(f for f in files
                if f.point_key == SKI_SLOPE_POINT
                and f.stem.endswith(f"_{SKI_SLOPE_DIRECTION}"))
    files.append(SampleFile(
        stem=SKI_SLOPE_STEM, point_key=base.point_key, point_label=base.point_label,
        direction=base.direction, direction_label=base.direction_label, axis=base.axis,
        overall_mm_s=SKI_SLOPE_TARGET_MM_S, quantity=QUANTITY, unit=UNIT,
        planted=SKI_SLOPE_PLANTED, ski_slope=True))
    return tuple(files)


#: All 13, in write order. The pins iterate this rather than a hardcoded list.
FILES: tuple[SampleFile, ...] = _manifest()


def iso_zone(overall: float, iso_table: dict[str, dict[str, float]]) -> str:
    """The ISO 20816-3 zone this overall lands in, read from config rather than
    restated -- so the README can never drift from config/iso_zones.json."""
    bounds = iso_table[f"{ISO_GROUP}_{ISO_SUPPORT}"]
    if overall <= bounds["ab"]:
        return "A"
    if overall <= bounds["bc"]:
        return "B"
    if overall <= bounds["cd"]:
        return "C"
    return "D"


def build_case(point: Point) -> Case:
    """The 2 kHz / 12,800-line case behind one measurement point."""
    iso_table = load_config("iso_zones")["zones"]
    thresholds = load_thresholds(PROFILE)
    return make_case(point.recipe, iso_table=iso_table, thresholds=thresholds,
                     seed=point.seed, fmax=FMAX_HZ, lines=LINES)


def axis_rows(case: Case, point: Point, suffix: str, axis: str) -> list[tuple[float, float]]:
    """(freq_hz, amplitude) rows for one file: the generator's spectrum, plus the 1x
    line and (at Compressor DE, axially) the weak BPFO, scaled so the Parseval sum
    equals the overall this point states.

    The generator's grid is `i * fmax / lines` for i < lines, so its last bin sits one
    df short of Fmax. A CSV carries no `fmax_hz` field -- the upload lane reads Fmax as
    the top bin -- so one closing bin at exactly Fmax is appended, at the level of the
    bin before it, and the file spans 0-2000 Hz inclusive. Nothing about the tones
    moves, and `_add_peak` cannot touch the closing bin (it indexes `< lines`).
    """
    spectrum = case.spectra[axis]
    freqs = list(spectrum.freq_hz) + [FMAX_HZ]
    amps = list(spectrum.amplitude)
    amps.append(amps[-1])
    _add_peak(amps, SHAFT_LINE_HZ, SHAFT_LINE_AMP, FMAX_HZ, LINES)
    if point.recipe == "bpfo" and axis == "x":
        _add_peak(amps, BPFO_HZ, AXIAL_BPFO_AMP, FMAX_HZ, LINES)
    target = point.overalls[suffix]
    scale = target / math.sqrt(sum(a * a for a in amps))
    return [(f, a * scale) for f, a in zip(freqs, amps)]


def ski_slope_shape(freqs: list[float]) -> list[float]:
    """The unit artifact: 1 at DC, strictly decreasing, never zero. Strictly
    decreasing is the load-bearing property -- it is what makes this a slope and not
    a tone, so nothing in the set can mistake it for a fault."""
    return [1.0 / (1.0 + (f / SKI_SLOPE_KNEE_HZ) ** 2) for f in freqs]


def ski_slope_rows(base_rows: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """The healthy Compressor NDE H file, UNCHANGED, plus the artifact on top.

    No second rescale: the machine content keeps the amplitudes it has in
    `compressor_nde_h.csv`, so the only difference between the two files is the
    artifact. The scale is closed-form rather than searched -- with h the finished
    healthy array and g the unit shape, solving

        |h + s.g|^2 = T^2   ->   s^2.Sg^2 + 2s.Shg + (Sh^2 - T^2) = 0

    for its one positive root puts the overall exactly on target, deterministically.
    """
    freqs = [f for f, _ in base_rows]
    h = [a for _, a in base_rows]
    g = ski_slope_shape(freqs)
    a2 = sum(x * x for x in g)
    b2 = sum(y * x for y, x in zip(h, g))
    c2 = sum(y * y for y in h) - SKI_SLOPE_TARGET_MM_S ** 2
    if c2 >= 0:
        raise ValueError("the healthy file already exceeds the ski-slope target")
    scale = (-b2 + math.sqrt(b2 * b2 - a2 * c2)) / a2
    added = [scale * x for x in g]
    if any(b > a for a, b in zip(added, added[1:])):
        raise ValueError("the ski slope is not monotone decreasing")
    return [(f, y + x) for f, y, x in zip(freqs, h, added)]


#: The frequency column needs MORE precision than the 8,000-line sibling's `{f:g}`,
#: and this is not a style choice. `%g` defaults to 6 significant figures, which is
#: exactly enough for that sample's df of 0.25 Hz (its widest value is 1999.75) and
#: NOT enough for this one's 0.15625: 107.1875 Hz writes as `107.188`, 1999.84375 as
#: `1999.84`, and the bin spacing arrives at the parser jittering between 0.15 and
#: 0.16. Every multiple of 0.15625 (= 5/32) is exact in binary and has at most five
#: decimals, so ten significant figures round-trips the whole axis exactly while
#: staying compact -- `0`, `0.15625`, `1999.84375`. The amplitude keeps six: it is a
#: measurement, not a grid.
FREQ_FORMAT = ".10g"
AMPLITUDE_FORMAT = ".6g"


def csv_text(rows: list[tuple[float, float]]) -> str:
    lines = ["freq_hz,amplitude"]
    lines += [f"{f:{FREQ_FORMAT}},{a:{AMPLITUDE_FORMAT}}" for f, a in rows]
    return "\n".join(lines) + "\n"


def build_rows() -> dict[str, list[tuple[float, float]]]:
    """Every file's rows, keyed by stem. One `make_case` per point, not per file."""
    rows: dict[str, list[tuple[float, float]]] = {}
    for point in POINTS:
        case = build_case(point)
        for suffix, _direction, axis, _label in DIRECTIONS:
            rows[f"{point.key}_{suffix}"] = axis_rows(case, point, suffix, axis)
    rows[SKI_SLOPE_STEM] = ski_slope_rows(rows[f"{SKI_SLOPE_POINT}_{SKI_SLOPE_DIRECTION}"])
    return rows


def readme_text() -> str:
    iso_table = load_config("iso_zones")["zones"]
    bounds = iso_table[f"{ISO_GROUP}_{ISO_SUPPORT}"]
    zones = {p.key: iso_zone(max(p.overalls.values()), iso_table) for p in POINTS}

    per_point = "\n".join(
        f"| {p.label} | {p.overalls['h']:.2f} / {p.overalls['v']:.2f} / "
        f"{p.overalls['a']:.2f} | **{zones[p.key]}** | {p.planted} |"
        for p in POINTS
    )
    per_file = "\n".join(
        f"| `{f.stem}.csv` | {f.point_label} | {f.direction_label} | `{f.direction}` "
        f"| `{f.axis}` | {f.quantity}, {f.unit} | {f.overall_mm_s:.2f} | {f.planted} |"
        for f in FILES
    )
    form = "\n".join(f"| {k} | {v} |" for k, v in (
        ("Machine alias", MACHINE_ALIAS),
        ("Machine type", MACHINE_TYPE),
        ("Running speed", f"{RPM:.0f} rpm"),
        ("Rated power", f"{MACHINE_KW:g} kW - typed as the ISO group below, "
                        f"which is the field that carries machine size"),
        ("ISO group", f"{ISO_GROUP} (medium, 15-300 kW)"),
        ("Support / mounting", ISO_SUPPORT),
        ("Bearing model", f"{BEARING_MODEL} (the same bearing at all four points)"),
        ("Measurement location", "the point's name, per file (see the table below)"),
        ("Velocity unit", VELOCITY_UNIT_FORM),
        ("Detection", DETECTION_FORM),
        ("Mode", MODE_FORM),
    ))

    return f"""# Route sample - {MACHINE_ALIAS}, four points, Fmax {FMAX_HZ:g} Hz

Session FIXTURE-1. One compressor train, {RPM:.0f} rpm, bearing {BEARING_MODEL}, measured at
**four points** - {', '.join(p.label for p in POINTS)} - each in three
directions, at Fmax {FMAX_HZ:g} Hz with {LINES:,} lines (df {FMAX_HZ / LINES:g} Hz;
{LINES + 1:,} rows, 0-{FMAX_HZ:g} Hz inclusive, the closing bin added so the upload lane reads
Fmax as {FMAX_HZ:g} Hz). Written by `scripts/make_multi_sample.py`, keyless and deterministic.

**A BPFO is planted at Compressor DE and nowhere else.** Every other point is healthy.
That is the whole point of the set: the diagnostic act on a route is deciding *which*
point the fault is at, and a single-point fixture cannot exercise it.

The sibling sample `../bpfo_synthetic_fmax2000/` is the same fault at one point only,
at {SIBLING_LINES:,} lines. Neither script touches the other's directory.

## The machine - every form value a human types

| field | value |
|---|---|
{form}

ISO 20816-3 group {ISO_GROUP} / {ISO_SUPPORT} boundaries, from `config/iso_zones.json`:
A/B {bounds['ab']:g}, B/C {bounds['bc']:g}, C/D {bounds['cd']:g} mm/s RMS.

## The four points

| point | overall H / V / A (mm/s RMS) | zone | planted |
|---|---|---|---|
{per_point}

## The files

Upload **one point per job**: three files, one per direction slot. A job takes at most
three files, so there is no twelve-channel upload - four jobs, four reports.

| file | point | direction slot | form value | generator axis | declared type + unit | overall | planted |
|---|---|---|---|---|---|---|---|
{per_file}

Every file declares the same thing, in the two places the intake reads: the form values
`velocity_unit={VELOCITY_UNIT_FORM}` / `detection_type={DETECTION_FORM}` /
`mode={MODE_FORM}`, and the `declared type + unit` column above. Nothing is left to
assumption, so no report drawn from this set can print an amplitude "as supplied".

## The 13th file: `{SKI_SLOPE_STEM}.csv`

**This is not a fault case, and it is not a second machine.** It is
`{SKI_SLOPE_POINT}_{SKI_SLOPE_DIRECTION}.csv` - a healthy point - with a *"ski slope"*
added on top: the monotone low-frequency rise you get from integrating a noisy or
settling accelerometer signal to velocity. It carries no tone, no harmonic and no
bearing defect; the machine content in it is identical to the healthy file's.

What it does carry is an overall of {SKI_SLOPE_TARGET_MM_S:g} mm/s RMS against that point's true
{next(p for p in POINTS if p.key == SKI_SLOPE_POINT).overalls[SKI_SLOPE_DIRECTION]:.2f} mm/s -
about {SKI_SLOPE_TARGET_MM_S / next(p for p in POINTS if p.key == SKI_SLOPE_POINT).overalls[SKI_SLOPE_DIRECTION]:.1f}x, enough to read as Zone D on a machine that is in Zone
{zones[SKI_SLOPE_POINT]}. That is what the file is for: a severity claim that is entirely an
artifact of how the data was captured. The name is long because once uploaded the
filename is the only label that travels with the file - the webapp stores every upload
as `upload.csv`.

## What a CSV cannot carry, and what was done about it

* **A 1x running-speed line at {SHAFT_LINE_HZ:g} Hz on every file** (pre-scale amplitude
  {SHAFT_LINE_AMP:g}). The intake's cross-file speed check refuses a trio in which no channel
  carries a peak near k x shaft, and neither seeded recipe has one. The line stands
  {SHAFT_LINE_MEDIAN_BAND[0]:g}-{SHAFT_LINE_MEDIAN_BAND[1]:g}x the spectrum median on the twelve machine files (the check
  needs >= 3x) and {SKI_SLOPE_SHAFT_LINE_MEDIAN:g}x on the ski-slope file, where the artifact adds to the
  same bin. On all 13 it stays below the route profile's amplitude floor
  (< {SHAFT_LINE_FLOOR_CEILING:g}x it), so it satisfies the intake without reaching any shaft-order
  evidence set. A real machine always shows one.
* **A weak axial BPFO at Compressor DE** (pre-scale amplitude {AXIAL_BPFO_AMP:g}), because the
  seeded recipe puts the tone on the two radial axes and nothing on the axial one,
  while a real outer-race defect shows a little axially. Measured at {AXIAL_BPFO_MEDIAN_RATIO:g}x the median
  and {AXIAL_BPFO_FLOOR_RATIO:g}x the amplitude floor - visible on the figure, not committed evidence.
  The radial tones sit {RADIAL_BPFO_FLOOR_BAND[0]:g}-{RADIAL_BPFO_FLOOR_BAND[1]:g}x OVER that same floor, which is what "clearly on
  H and V, weak on A" means numerically.
* **Each file is scaled uniformly so the Parseval sum of its bins equals the overall
  its point states** (the spectrum lane derives ISO severity from that sum). Uniform
  scaling leaves every peak-to-mean ratio, and so every detector decision, unchanged.

Expected on upload: Compressor DE commits a bearing outer-race fault (BPFO) at
{BPFO_BIN_TEXT} Hz ({BPFO_BIN_HZ / (RPM / 60.0):.2f}x) in Zone {zones['compressor_de']}; the other three points commit
nothing and read Zone {zones['motor_de']}, {zones['motor_nde']} and {zones['compressor_nde']}. The
files are byte-identical on every run (`tests/test_fixture1_multi_location.py`).
"""


def write_sample(out_dir: Path) -> list[Path]:
    """Write the 13 CSVs and the README into `out_dir`; returns the paths written."""
    rows = build_rows()
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for sample in FILES:
        path = out_dir / f"{sample.stem}.csv"
        path.write_text(csv_text(rows[sample.stem]))
        written.append(path)
    readme = out_dir / "README.md"
    readme.write_text(readme_text())
    written.append(readme)
    return written


def main(argv: list[str]) -> int:
    out_dir = Path(argv[1]).resolve() if len(argv) > 1 else OUT
    for path in write_sample(out_dir):
        print(f"wrote {path.relative_to(_REPO) if path.is_relative_to(_REPO) else path}"
              f"  ({path.stat().st_size:,} bytes)")
    print(f"{len(FILES)} CSVs + README. The PDFs are rendered through the app, four jobs "
          f"of three files - see README.md.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

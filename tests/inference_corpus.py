"""Session G: the synthetic text-export corpus.

Twelve files in the shapes real collectors actually emit — header blocks,
semicolon/EU decimals, CPM and order axes, tab and whitespace columns, extra
columns, footers, waveforms, trend logs — plus the recipe each one SHOULD
produce. Written here (not committed as fixtures) so every file is generated
deterministically from one seed-free formula and the expected physics is
explicit in the test, not baked into an opaque blob.

Every spectrum carries a real 1x line at the stated running speed, because the
verification gate (adapters/uploads/verify.py) checks exactly that — a corpus
without running-speed content would only ever prove the gate can fail.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from vib_agent.adapters.uploads.recipe import ParseRecipe

RPM = 1800.0
SHAFT_HZ = RPM / 60.0
BPFO_HZ = 107.03
FMAX = 400.0
NBINS = 801


def _spectrum_points(*, scale: float = 1.0) -> list[tuple[float, float]]:
    """A velocity spectrum with 1x, 2x, BPFO and 2xBPFO on a small floor."""
    step = FMAX / (NBINS - 1)
    peaks = {SHAFT_HZ: 1.10, 2 * SHAFT_HZ: 0.30, BPFO_HZ: 2.10, 2 * BPFO_HZ: 0.95}
    points: list[tuple[float, float]] = []
    for i in range(NBINS):
        freq = round(i * step, 4)
        amp = 0.012
        for peak_hz, peak_amp in peaks.items():
            if abs(freq - peak_hz) < step / 2:
                amp = peak_amp
        points.append((freq, amp * scale))
    return points


def _waveform_points(*, fs: float = 20480.0, seconds: float = 0.5) -> list[tuple[float, float]]:
    """A modulated time waveform: shaft rotation plus a BPFO-rate impact train
    riding a 3 kHz resonance (what envelope demodulation is built to recover)."""
    import math

    n = int(fs * seconds)
    points: list[tuple[float, float]] = []
    for i in range(n):
        t = i / fs
        shaft = 0.4 * math.sin(2 * math.pi * SHAFT_HZ * t)
        impacts = (1.0 + 0.9 * math.sin(2 * math.pi * BPFO_HZ * t)) * math.sin(2 * math.pi * 3000.0 * t)
        points.append((round(t, 7), round(shaft + 0.6 * impacts, 6)))
    return points


@dataclass(frozen=True)
class CorpusFile:
    name: str            # file name (drives the extension under test)
    description: str     # what real-world export shape this imitates
    body: str            # the file's exact text
    recipe: ParseRecipe  # the recipe an inference pass should produce
    severity_expected: bool  # does this file support an ISO severity claim?
    # Session G2 shapes: `cells` means write an .xlsx from these rows instead of
    # `body`; `label_column` marks a transposed file whose first cell is a label.
    cells: list[list] | None = None
    label_column: bool = False


def _ams_style() -> CorpusFile:
    points = _spectrum_points()
    header = (
        "AMS Machinery Manager - Spectrum Export\n"
        "Route: MAIN PLANT / PUMP HOUSE\n"
        "Point: 12-P-101 MOH\n"
        "Date: 2026-03-14 09:41:22\n"
        "Machine Speed: 1800 RPM\n"
        "Units: IN/SEC PEAK\n"
        "Fmax: 400.00 Hz   Lines: 800\n"
        "----------------------------------------\n"
        "Frequency(Hz)\tAmplitude\n"
    )
    rows = "\n".join(f"{f:.4f}\t{a * 0.03937:.6f}" for f, a in points)
    return CorpusFile(
        name="ams_export.txt",
        description="AMS-style header block, tab-delimited, in/s peak",
        body=header + rows + "\n",
        recipe=ParseRecipe(kind="spectrum", delimiter="tab", skip_rows=8, header_rows=1,
                           columns=["x", "amplitude"], x_unit="hz", amplitude_unit="in_s",
                           detection="peak", rpm_source="file_header", rpm_value=1800.0),
        severity_expected=True,
    )


def _ptitude_style() -> CorpusFile:
    points = _spectrum_points()
    header = (
        "@ptitude Analyst Export\n"
        "MEASUREMENT;POINT 3H;VELOCITY SPECTRUM\n"
        "SPEED;1800;RPM\n"
        "UNIT;mm/s;RMS\n"
        "FREQ;AMPL\n"
    )
    rows = "\n".join(f"{f:.3f};{a:.5f}".replace(".", ",") for f, a in points)
    return CorpusFile(
        name="ptitude_export.txt",
        description="@ptitude-style semicolon delimiter with EU decimal commas",
        body=header + rows + "\n",
        recipe=ParseRecipe(kind="spectrum", delimiter="semicolon", decimal_mark="comma",
                           skip_rows=4, header_rows=1, columns=["x", "amplitude"],
                           x_unit="hz", amplitude_unit="mm_s", detection="rms",
                           rpm_source="file_header", rpm_value=1800.0),
        severity_expected=True,
    )


def _bare_pairs() -> CorpusFile:
    points = _spectrum_points()
    return CorpusFile(
        name="bare_pairs.txt",
        description="no header at all — two whitespace-separated columns",
        body="\n".join(f"{f:.4f} {a:.5f}" for f, a in points) + "\n",
        recipe=ParseRecipe(kind="spectrum", delimiter="whitespace", columns=["x", "amplitude"],
                           x_unit="hz", amplitude_unit="mm_s", detection="rms"),
        severity_expected=True,
    )


def _cpm_axis() -> CorpusFile:
    points = _spectrum_points()
    header = "Machine,PUMP-4,Speed,1800,RPM\nCPM,Velocity (mm/s RMS)\n"
    rows = "\n".join(f"{f * 60:.2f},{a:.5f}" for f, a in points)
    return CorpusFile(
        name="cpm_axis.txt",
        description="CPM frequency axis (the 60x unit trap)",
        body=header + rows + "\n",
        recipe=ParseRecipe(kind="spectrum", delimiter="comma", skip_rows=1, header_rows=1,
                           columns=["x", "amplitude"], x_unit="cpm", amplitude_unit="mm_s",
                           detection="rms"),
        severity_expected=True,
    )


def _orders_axis() -> CorpusFile:
    points = _spectrum_points()
    header = "ORDER ANALYSIS EXPORT\nShaft speed: 1800 rpm\nOrder|Amplitude(mm/s)\n"
    rows = "\n".join(f"{f / SHAFT_HZ:.5f}|{a:.5f}" for f, a in points)
    return CorpusFile(
        name="orders_axis.dat",
        description="order-normalised x axis, pipe delimiter, .dat extension",
        body=header + rows + "\n",
        recipe=ParseRecipe(kind="spectrum", delimiter="pipe", skip_rows=2, header_rows=1,
                           columns=["x", "amplitude"], x_unit="orders", amplitude_unit="mm_s",
                           detection="rms", rpm_source="file_header", rpm_value=1800.0),
        severity_expected=True,
    )


def _extra_columns() -> CorpusFile:
    points = _spectrum_points()
    header = "Index,Frequency [Hz],Amplitude [mm/s],Phase [deg],Flag\n"
    rows = "\n".join(f"{i},{f:.4f},{a:.5f},{(i * 7) % 360},OK" for i, (f, a) in enumerate(points))
    return CorpusFile(
        name="with_phase.txt",
        description="five columns — index, frequency, amplitude, phase, status flag",
        body=header + rows + "\n",
        recipe=ParseRecipe(kind="spectrum", delimiter="comma", header_rows=1,
                           columns=["ignore", "x", "amplitude", "ignore", "ignore"],
                           x_unit="hz", amplitude_unit="mm_s", detection="rms"),
        severity_expected=True,
    )


def _accel_units() -> CorpusFile:
    points = _spectrum_points(scale=0.15)
    header = "SPECTRUM EXPORT\nUnits: g RMS\nHz;g\n"
    rows = "\n".join(f"{f:.4f};{a:.6f}" for f, a in points)
    return CorpusFile(
        name="accel_g.asc",
        description="acceleration-g spectrum, .asc extension (no severity claim allowed)",
        body=header + rows + "\n",
        recipe=ParseRecipe(kind="spectrum", delimiter="semicolon", skip_rows=2, header_rows=1,
                           columns=["x", "amplitude"], x_unit="hz", amplitude_unit="g",
                           detection="rms"),
        severity_expected=False,
    )


def _footer_junk() -> CorpusFile:
    points = _spectrum_points()
    header = "FREQ,AMP\n"
    rows = "\n".join(f"{f:.4f},{a:.5f}" for f, a in points)
    footer = "\n*** END OF DATA ***\nOperator: J. Khaings\nChecksum: 4F2A\n"
    return CorpusFile(
        name="with_footer.txt",
        description="trailing non-numeric footer block after the data",
        body=header + rows + footer,
        recipe=ParseRecipe(kind="spectrum", delimiter="comma", header_rows=1,
                           columns=["x", "amplitude"], x_unit="hz", amplitude_unit="mm_s",
                           detection="rms"),
        severity_expected=True,
    )


def _mils_displacement() -> CorpusFile:
    points = _spectrum_points(scale=0.4)
    header = "PROXIMITY PROBE SPECTRUM\nUNITS: MILS PK-PK\nHZ  MILS\n"
    rows = "\n".join(f"{f:.4f}  {a:.5f}" for f, a in points)
    return CorpusFile(
        name="displacement.txt",
        description="displacement (mil pk-pk) — frequency ID only, never a severity claim",
        body=header + rows + "\n",
        recipe=ParseRecipe(kind="spectrum", delimiter="whitespace", skip_rows=2, header_rows=1,
                           columns=["x", "amplitude"], x_unit="hz", amplitude_unit="mil",
                           detection="peak_to_peak"),
        severity_expected=False,
    )


def _waveform_with_time() -> CorpusFile:
    points = _waveform_points()
    header = "TIME WAVEFORM EXPORT\nPoint: 12-P-101 MOH\nTime(s),Accel(g)\n"
    rows = "\n".join(f"{t:.7f},{v:.6f}" for t, v in points)
    return CorpusFile(
        name="waveform_time.txt",
        description="time waveform with an explicit time column (sample rate derived from data)",
        body=header + rows + "\n",
        recipe=ParseRecipe(kind="waveform", delimiter="comma", skip_rows=2, header_rows=1,
                           columns=["x", "amplitude"], x_unit="seconds", amplitude_unit="g",
                           detection="rms", fs_source="x_column"),
        severity_expected=False,
    )


def _waveform_header_fs() -> CorpusFile:
    points = _waveform_points()
    header = "WAVEFORM\nSampleRate: 20480 Hz\nSpeed: 1800 RPM\nAmplitude(g)\n"
    rows = "\n".join(f"{v:.6f}" for _, v in points)
    return CorpusFile(
        name="waveform_bare.dat",
        description="single-column waveform, sample rate stated only in the header",
        body=header + rows + "\n",
        recipe=ParseRecipe(kind="waveform", delimiter="whitespace", skip_rows=3, header_rows=1,
                           columns=["amplitude"], amplitude_unit="g", detection="rms",
                           fs_source="file_header", fs_value=20480.0,
                           rpm_source="file_header", rpm_value=1800.0),
        severity_expected=False,
    )


def _trend_log() -> CorpusFile:
    header = "TREND EXPORT\nPoint;12-P-101 MOH;Overall velocity mm/s RMS\nDate;Value\n"
    rows = []
    for day in range(30):
        value = 2.0 + 0.05 * day
        rows.append(f"2026-0{1 + day // 28}-{(day % 28) + 1:02d}T08:00:00;{value:.3f}")
    return CorpusFile(
        name="trend_log.txt",
        description="dated trend log, semicolon delimited",
        body=header + "\n".join(rows) + "\n",
        recipe=ParseRecipe(kind="trend", delimiter="semicolon", skip_rows=2, header_rows=1,
                           columns=["timestamp", "value"], amplitude_unit="mm_s",
                           detection="rms", timestamp_format="iso8601"),
        severity_expected=True,
    )


CORPUS: tuple[CorpusFile, ...] = (
    _ams_style(),
    _ptitude_style(),
    _bare_pairs(),
    _cpm_axis(),
    _orders_axis(),
    _extra_columns(),
    _accel_units(),
    _footer_junk(),
    _mils_displacement(),
    _waveform_with_time(),
    _waveform_header_fs(),
    _trend_log(),
)


def write_corpus(directory: Path, corpus: tuple[CorpusFile, ...] = CORPUS) -> dict[str, Path]:
    """Materialise every corpus file; returns {name: path}."""
    directory.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    for item in corpus:
        path = directory / item.name
        if item.cells is not None:
            import openpyxl

            workbook = openpyxl.Workbook()
            sheet = workbook.active
            for row in item.cells:
                sheet.append(row)
            workbook.save(path)
        else:
            path.write_text(item.body, encoding="utf-8")
        paths[item.name] = path
    return paths


def write_all(directory: Path) -> dict[str, Path]:
    return write_corpus(directory, ALL_CORPUS)


# ══════════════════════════════════════════════════════════════════════════
# Session G2 — weird-but-real shapes: transposed rows, multi-header and
# units-row spreadsheets, thousands separators, two channels in one file.
# ══════════════════════════════════════════════════════════════════════════


def _transposed_pairs() -> CorpusFile:
    """Some analysers export a frequency ROW above an amplitude ROW."""
    points = _spectrum_points()
    freq_row = "\t".join(f"{f:.3f}" for f, _ in points)
    amp_row = "\t".join(f"{a:.5f}" for _, a in points)
    body = ("TRANSPOSED EXPORT\nSpeed: 1800 RPM\n"
            f"Frequency (Hz)\t{freq_row}\nAmplitude (mm/s RMS)\t{amp_row}\n")
    # the label cell is column 0 of each row, so it is skipped as a header column
    return CorpusFile(
        name="transposed_rows.txt",
        description="transposed: one row per series, label in the first cell",
        body=body,
        recipe=ParseRecipe(kind="spectrum", delimiter="tab", skip_rows=2, header_rows=0,
                           orientation="rows", columns=["x", "amplitude"], x_unit="hz",
                           amplitude_unit="mm_s", detection="rms",
                           rpm_source="file_header", rpm_value=1800.0),
        severity_expected=True,
        label_column=True,
    )


def _thousands_separators() -> CorpusFile:
    """CPM axis large enough to carry thousands separators — 1,800.00 CPM."""
    points = _spectrum_points()
    header = "FREQ (CPM);AMPLITUDE (mm/s RMS)\n"
    rows = "\n".join(f"{f * 60:,.2f};{a:.5f}" for f, a in points)
    return CorpusFile(
        name="thousands.txt",
        description="thousands separators in a CPM axis, semicolon delimited",
        body=header + rows + "\n",
        recipe=ParseRecipe(kind="spectrum", delimiter="semicolon", header_rows=1,
                           thousands_separator="comma", columns=["x", "amplitude"],
                           x_unit="cpm", amplitude_unit="mm_s", detection="rms"),
        severity_expected=True,
    )


def _units_row_under_header() -> CorpusFile:
    points = _spectrum_points()
    header = "Frequency\tVelocity\n[Hz]\t[mm/s RMS]\n"
    rows = "\n".join(f"{f:.4f}\t{a:.5f}" for f, a in points)
    return CorpusFile(
        name="units_row.dat",
        description="a units row under the column-name row (two header rows)",
        body=header + rows + "\n",
        recipe=ParseRecipe(kind="spectrum", delimiter="tab", header_rows=2,
                           columns=["x", "amplitude"], x_unit="hz",
                           amplitude_unit="mm_s", detection="rms"),
        severity_expected=True,
    )


def _two_channels_one_file() -> CorpusFile:
    """Horizontal AND vertical amplitudes side by side. One recipe reads ONE
    channel — the second is marked `ignore`, and the confirm card says so."""
    points = _spectrum_points()
    header = "Hz,Horizontal (mm/s),Vertical (mm/s)\n"
    rows = "\n".join(f"{f:.4f},{a:.5f},{a * 0.6:.5f}" for f, a in points)
    return CorpusFile(
        name="two_channels.txt",
        description="two amplitude columns in one file — one read, one declared ignored",
        body=header + rows + "\n",
        recipe=ParseRecipe(kind="spectrum", delimiter="comma", header_rows=1,
                           columns=["x", "amplitude", "ignore"], x_unit="hz",
                           amplitude_unit="mm_s", detection="rms"),
        severity_expected=True,
    )


def _tab_with_blank_columns() -> CorpusFile:
    points = _spectrum_points()
    header = "Point\tFreq\t\tAmp\tStatus\n"
    rows = "\n".join(f"12-P-101\t{f:.4f}\t\t{a:.5f}\tOK" for f, a in points)
    return CorpusFile(
        name="tab_blanks.asc",
        description="tab-delimited with an empty spacer column and a label column",
        body=header + rows + "\n",
        recipe=ParseRecipe(kind="spectrum", delimiter="tab", header_rows=1,
                           columns=["ignore", "x", "ignore", "amplitude", "ignore"],
                           x_unit="hz", amplitude_unit="mm_s", detection="rms"),
        severity_expected=True,
    )


def _xlsx_multi_header() -> CorpusFile:
    """A spreadsheet with a title block, a column-name row and a units row."""
    rows = [["ROUTE EXPORT", "", ""],
            ["Machine", "PUMP-4", ""],
            ["Speed", 1800, "RPM"],
            ["Frequency", "Velocity", "Phase"],
            ["Hz", "mm/s RMS", "deg"]]
    for i, (f, a) in enumerate(_spectrum_points()):
        rows.append([f, a, (i * 7) % 360])
    return CorpusFile(
        name="multi_header.xlsx",
        description="xlsx with a title block, a name row, a units row and a phase column",
        body="",
        recipe=ParseRecipe(kind="spectrum", delimiter="tab", skip_rows=3, header_rows=2,
                           columns=["x", "amplitude", "ignore"], x_unit="hz",
                           amplitude_unit="mm_s", detection="rms",
                           rpm_source="file_header", rpm_value=1800.0),
        severity_expected=True,
        cells=rows,
    )


def _xlsx_orders_axis() -> CorpusFile:
    rows = [["ORDER SPECTRUM", "", ""], ["Shaft", 1800, "rpm"], ["Order", "in/s pk", ""]]
    for f, a in _spectrum_points():
        rows.append([f / SHAFT_HZ, a * 0.03937, ""])
    return CorpusFile(
        name="orders.xlsx",
        description="xlsx in shaft orders and in/s peak — two unit traps at once",
        body="",
        recipe=ParseRecipe(kind="spectrum", delimiter="tab", skip_rows=2, header_rows=1,
                           columns=["x", "amplitude", "ignore"], x_unit="orders",
                           amplitude_unit="in_s", detection="peak",
                           rpm_source="file_header", rpm_value=1800.0),
        severity_expected=True,
        cells=rows,
    )


def _csv_wrong_headers() -> CorpusFile:
    """A .csv that our TEMPLATE cannot read (no freq_hz/amplitude columns) —
    the file that used to be an error card and is now the fallback lane."""
    points = _spectrum_points()
    header = "Frequenz;Schwinggeschwindigkeit\n"
    rows = "\n".join(f"{f:.3f};{a:.5f}".replace(".", ",") for f, a in points)
    return CorpusFile(
        name="german_headers.csv",
        description="CSV with non-template headers and EU decimals — template fails, inference reads it",
        body=header + rows + "\n",
        recipe=ParseRecipe(kind="spectrum", delimiter="semicolon", decimal_mark="comma",
                           header_rows=1, columns=["x", "amplitude"], x_unit="hz",
                           amplitude_unit="mm_s", detection="rms"),
        severity_expected=True,
    )


G2_CORPUS: tuple[CorpusFile, ...] = (
    _transposed_pairs(),
    _thousands_separators(),
    _units_row_under_header(),
    _two_channels_one_file(),
    _tab_with_blank_columns(),
    _xlsx_multi_header(),
    _xlsx_orders_axis(),
    _csv_wrong_headers(),
)

ALL_CORPUS: tuple[CorpusFile, ...] = CORPUS + G2_CORPUS

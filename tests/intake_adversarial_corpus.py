"""INTAKE-HARDEN: the adversarial intake corpus.

Twenty small files in the shapes that break text-export readers — encodings a
Windows instrument actually emits, line endings from three decades, European
decimals, junk that looks numeric, data that lies about itself. Each one carries
the recipe a careful reader would write for it and the outcome that recipe MUST
produce: a Case with the right physics, or a clean refusal. Never a Case with
the wrong physics.

Convention, matching tests/inference_corpus.py: the files are generated from one
seed-free formula so the expected physics is explicit in code rather than baked
into an opaque blob. Unlike that module, these are ALSO materialised to
tests/fixtures/intake_adversarial/ and committed, because half of them exist to
test BYTES (a BOM, a bare CR, a cp1252 degree sign, a NUL) and bytes that only
ever live inside a test run have never been proved to survive a checkout.
`scripts/gen_intake_corpus.py` regenerates them; `test_intake_adversarial.py`
asserts the committed bytes still match this generator exactly.

Every spectrum carries a real 1x line at the stated running speed and a BPFO
line at 107.03 Hz, so a file that reads correctly is analysable and a file that
reads incorrectly is visibly so.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from vib_agent.adapters.uploads.recipe import ParseRecipe

RPM = 1800.0
SHAFT_HZ = RPM / 60.0          # 30 Hz
BPFO_HZ = 107.03
FMAX = 250.0
NBINS = 501                    # 0.5 Hz bins — 60x finer than the shaft line

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "intake_adversarial"


def spectrum_points(*, scale: float = 1.0, floor: float = 0.012) -> list[tuple[float, float]]:
    """A velocity spectrum with 1x, 2x, BPFO and 2xBPFO on a small floor."""
    step = FMAX / (NBINS - 1)
    peaks = {SHAFT_HZ: 1.10, 2 * SHAFT_HZ: 0.30, BPFO_HZ: 2.10, 2 * BPFO_HZ: 0.95}
    points: list[tuple[float, float]] = []
    for i in range(NBINS):
        freq = round(i * step, 4)
        amp = floor
        for peak_hz, peak_amp in peaks.items():
            if abs(freq - peak_hz) < step / 2:
                amp = peak_amp
        points.append((freq, amp * scale))
    return points


# ── outcome vocabulary ───────────────────────────────────────────────────────
# What the CORRECT recipe must do with this file. Anything else is a finding.
#   "case"    -> execute_recipe returns a Case
#   "refuse"  -> execute_recipe raises RecipeExecutionError (clean, analyst-safe)
#   "not_text"-> read_sample raises NotTextError before inference is ever attempted
Outcome = str
# What verify_case must then say about that Case.
Gate = str  # "pass" | "fail" | "n/a"


@dataclass(frozen=True)
class AdversarialFile:
    name: str
    description: str          # the real-world export shape this imitates
    data: bytes               # the file's exact bytes (encoding is part of the test)
    recipe: ParseRecipe       # the recipe a careful reader would write
    outcome: Outcome
    gate: Gate
    severity_expected: bool   # may this file carry an ISO 20816 severity claim?
    hazard: str               # what this file is here to break


def _utf16(body: str, *, big_endian: bool) -> bytes:
    return body.encode("utf-16-be" if big_endian else "utf-16-le")


def _bom(big_endian: bool) -> bytes:
    return b"\xfe\xff" if big_endian else b"\xff\xfe"


# ══════════════════════════════════════════════════════════════════════════
# Encodings
# ══════════════════════════════════════════════════════════════════════════


def _utf16_le_bom() -> AdversarialFile:
    """Excel's "Unicode Text (*.txt)" export: UTF-16 LE, BOM, tab-delimited.
    This is not an exotic format — it is what Save As gives a Windows analyst."""
    body = "Frequency (Hz)\tVelocity (mm/s RMS)\n" + "".join(
        f"{f:.4f}\t{a:.5f}\n" for f, a in spectrum_points())
    return AdversarialFile(
        name="utf16le_bom.txt",
        description="Excel 'Unicode Text' export — UTF-16 LE with BOM, tab-delimited",
        data=_bom(False) + _utf16(body, big_endian=False),
        recipe=ParseRecipe(kind="spectrum", delimiter="tab", header_rows=1,
                           columns=["x", "amplitude"], x_unit="hz",
                           amplitude_unit="mm_s", detection="rms"),
        outcome="case", gate="pass", severity_expected=True,
        hazard="a UTF-16 body is NUL-laden when mis-decoded and reads as binary",
    )


def _utf16_be_bom() -> AdversarialFile:
    body = "FREQ;AMPL\n" + "".join(f"{f:.4f};{a:.5f}\n" for f, a in spectrum_points())
    return AdversarialFile(
        name="utf16be_bom.txt",
        description="UTF-16 BE with BOM, semicolon-delimited (big-endian instrument export)",
        data=_bom(True) + _utf16(body, big_endian=True),
        recipe=ParseRecipe(kind="spectrum", delimiter="semicolon", header_rows=1,
                           columns=["x", "amplitude"], x_unit="hz",
                           amplitude_unit="mm_s", detection="rms"),
        outcome="case", gate="pass", severity_expected=True,
        hazard="byte order — a BE file mis-read as LE yields CJK glyphs, not numbers",
    )


def _utf8_bom() -> AdversarialFile:
    """The single most common real-world encoding wart: Notepad/Excel prepend
    EF BB BF, and the first data value silently stops being a number."""
    body = "".join(f"{f:.4f},{a:.5f}\n" for f, a in spectrum_points())
    return AdversarialFile(
        name="utf8_bom_nohdr.txt",
        description="UTF-8 with BOM and NO header row — the BOM lands on the first datum",
        data=b"\xef\xbb\xbf" + body.encode("utf-8"),
        recipe=ParseRecipe(kind="spectrum", delimiter="comma", columns=["x", "amplitude"],
                           x_unit="hz", amplitude_unit="mm_s", detection="rms"),
        outcome="case", gate="pass", severity_expected=True,
        hazard="the BOM becomes U+FEFF on row 1, so row 1 is silently lost",
    )


def _cp1252_degrees() -> AdversarialFile:
    """A Windows-authored report header full of the characters that are not
    ASCII and not UTF-8: degree, micro, plus-minus, non-breaking space."""
    header = (
        "VIBRATION REPORT\r\n"
        "Bearing temp: 45\xb0C \xb1 2\xb0\r\n"
        "Displacement units: \xb5m pk-pk (not used here)\r\n"
        "Amplitude units: mm/s RMS\r\n"
        "Freq [Hz];Ampl\r\n"
    )
    rows = "".join(f"{f:.4f};{a:.5f}\r\n" for f, a in spectrum_points())
    return AdversarialFile(
        name="cp1252_degrees.txt",
        description="cp1252 header with degree/micro/plus-minus signs, CRLF, semicolon data",
        data=(header + rows).encode("cp1252"),
        recipe=ParseRecipe(kind="spectrum", delimiter="semicolon", skip_rows=4, header_rows=1,
                           columns=["x", "amplitude"], x_unit="hz",
                           amplitude_unit="mm_s", detection="rms"),
        outcome="case", gate="pass", severity_expected=True,
        hazard="0xB0/0xB5/0xB1 are invalid UTF-8 — a strict decode raises, a lax one mangles",
    )


# ══════════════════════════════════════════════════════════════════════════
# Line endings
# ══════════════════════════════════════════════════════════════════════════


def _crlf() -> AdversarialFile:
    header = "Hz\tmm/s\r\n"
    rows = "".join(f"{f:.4f}\t{a:.5f}\r\n" for f, a in spectrum_points())
    return AdversarialFile(
        name="crlf.txt",
        description="DOS CRLF line endings throughout, tab-delimited",
        data=(header + rows).encode("utf-8"),
        recipe=ParseRecipe(kind="spectrum", delimiter="tab", header_rows=1,
                           columns=["x", "amplitude"], x_unit="hz",
                           amplitude_unit="mm_s", detection="rms"),
        outcome="case", gate="pass", severity_expected=True,
        hazard="a trailing CR clinging to the last field breaks float() on every row",
    )


def _bare_cr() -> AdversarialFile:
    """Classic-Mac / some DAQ exports: CR only, no LF anywhere. A reader that
    splits on \\n sees ONE enormous line."""
    body = "Hz,mm/s\r" + "".join(f"{f:.4f},{a:.5f}\r" for f, a in spectrum_points())
    return AdversarialFile(
        name="bare_cr.dat",
        description="bare-CR line endings (no LF in the file at all)",
        data=body.encode("utf-8"),
        recipe=ParseRecipe(kind="spectrum", delimiter="comma", header_rows=1,
                           columns=["x", "amplitude"], x_unit="hz",
                           amplitude_unit="mm_s", detection="rms"),
        outcome="case", gate="pass", severity_expected=True,
        hazard="split('\\n') sees one line; only splitlines() sees 501",
    )


# ══════════════════════════════════════════════════════════════════════════
# Numbers
# ══════════════════════════════════════════════════════════════════════════


def _decimal_comma() -> AdversarialFile:
    """A CPM axis in EU decimals: '1800,00' — the literal shape a decimal-comma
    misread turns into 180000. Semicolon-delimited, as EU exports must be."""
    header = "Drehzahl;1800;U/min\nFrequenz (CPM);Schwinggeschwindigkeit (mm/s RMS)\n"
    rows = "".join(f"{f * 60:.2f};{a:.5f}\n".replace(".", ",") for f, a in spectrum_points())
    return AdversarialFile(
        name="decimal_comma_cpm.txt",
        description="German export: CPM axis, decimal commas, semicolon delimiter",
        data=(header + rows).encode("utf-8"),
        recipe=ParseRecipe(kind="spectrum", delimiter="semicolon", decimal_mark="comma",
                           skip_rows=1, header_rows=1, columns=["x", "amplitude"],
                           x_unit="cpm", amplitude_unit="mm_s", detection="rms"),
        outcome="case", gate="pass", severity_expected=True,
        hazard="'0,78' is 0.78 — read as a thousands group it becomes 78",
    )


def _thousands_eu_dot() -> AdversarialFile:
    """1.800,00 — dot groups thousands, comma is the decimal mark."""
    header = "FREQ (CPM);AMPL (mm/s RMS)\n"
    rows = "".join(f"{f * 60:,.2f};{a:.5f}\n".translate(str.maketrans(",.", ".,"))
                   for f, a in spectrum_points())
    return AdversarialFile(
        name="thousands_eu_dot.txt",
        description="EU thousands style 1.800,00 in a CPM axis, semicolon delimiter",
        data=(header + rows).encode("utf-8"),
        recipe=ParseRecipe(kind="spectrum", delimiter="semicolon", decimal_mark="comma",
                           header_rows=1, columns=["x", "amplitude"],
                           x_unit="cpm", amplitude_unit="mm_s", detection="rms"),
        outcome="case", gate="pass", severity_expected=True,
        hazard="the dot is a GROUPING mark here, the exact inverse of US style",
    )


def _thousands_space() -> AdversarialFile:
    """1 800,00 — SI/French grouping with a space, which also happens to be the
    thing a whitespace delimiter would split on."""
    header = "FREQ (CPM);AMPL (mm/s RMS)\n"
    rows = "".join(f"{f * 60:,.2f};{a:.5f}\n".replace(",", " ").replace(".", ",")
                   for f, a in spectrum_points())
    return AdversarialFile(
        name="thousands_space.txt",
        description="SI thousands style 1 800,00 — grouping char collides with whitespace",
        data=(header + rows).encode("utf-8"),
        recipe=ParseRecipe(kind="spectrum", delimiter="semicolon", decimal_mark="comma",
                           thousands_separator="space", header_rows=1,
                           columns=["x", "amplitude"], x_unit="cpm",
                           amplitude_unit="mm_s", detection="rms"),
        outcome="case", gate="pass", severity_expected=True,
        hazard="a whitespace-delimiter recipe would shear every row in half",
    )


def _scientific() -> AdversarialFile:
    header = "Frequency,Amplitude\n"
    rows = "".join(f"{f:.6E},{a:.6E}\n" for f, a in spectrum_points())
    return AdversarialFile(
        name="scientific.txt",
        description="both columns in scientific notation, uppercase E",
        data=(header + rows).encode("utf-8"),
        recipe=ParseRecipe(kind="spectrum", delimiter="comma", header_rows=1,
                           columns=["x", "amplitude"], x_unit="hz",
                           amplitude_unit="mm_s", detection="rms"),
        outcome="case", gate="pass", severity_expected=True,
        hazard="'1.200000E+01' contains a '+' and an 'E'; naive cleaners drop both",
    )


def _scientific_eu() -> AdversarialFile:
    """Scientific notation AND decimal commas at once: 1,200000E+01."""
    header = "Frequenz;Amplitude\n"
    rows = "".join(f"{f:.6E};{a:.6E}\n".replace(".", ",") for f, a in spectrum_points())
    return AdversarialFile(
        name="scientific_eu.asc",
        description="scientific notation with EU decimal commas (1,2E+01)",
        data=(header + rows).encode("utf-8"),
        recipe=ParseRecipe(kind="spectrum", delimiter="semicolon", decimal_mark="comma",
                           header_rows=1, columns=["x", "amplitude"], x_unit="hz",
                           amplitude_unit="mm_s", detection="rms"),
        outcome="case", gate="pass", severity_expected=True,
        hazard="the comma-to-dot swap must not touch the exponent sign",
    )


# ══════════════════════════════════════════════════════════════════════════
# Junk in and around the data block
# ══════════════════════════════════════════════════════════════════════════


def _comments_and_blanks() -> AdversarialFile:
    """Comment lines and blank lines interleaved THROUGH the data, not just at
    the top — a logger that annotates as it writes."""
    lines = ["# Spectrum export v2.1", "# Point: 12-P-101 MOH", "", "Hz,Amp"]
    for i, (f, a) in enumerate(spectrum_points()):
        if i and i % 97 == 0:
            lines.append("")
            lines.append(f"# --- block {i // 97} ---")
        lines.append(f"{f:.4f},{a:.5f}")
    return AdversarialFile(
        name="comments_blanks.dat",
        description="hash comments and blank lines interleaved through the data block",
        data=("\n".join(lines) + "\n").encode("utf-8"),
        recipe=ParseRecipe(kind="spectrum", delimiter="comma", skip_rows=3, header_rows=1,
                           columns=["x", "amplitude"], x_unit="hz",
                           amplitude_unit="mm_s", detection="rms"),
        outcome="case", gate="pass", severity_expected=True,
        hazard="mid-data junk must be dropped without shifting the rows around it",
    )


def _numeric_footer() -> AdversarialFile:
    """A footer that is NUMERIC — a statistics block with the same column count
    as the data. Non-numeric footers are already handled; this one is not
    obviously junk to a row-shaped filter."""
    header = "Hz,Amp\n"
    rows = "".join(f"{f:.4f},{a:.5f}\n" for f, a in spectrum_points())
    footer = ("0.0000,0.00000\n"          # a summary row that restarts the axis
              "1.0000,2.10000\n"
              "2.0000,0.01200\n"
              "*** STATISTICS ***\n"
              "Overall,3.41200\n"
              "Peak,2.10000\n")
    return AdversarialFile(
        name="numeric_footer.txt",
        description="a numeric statistics block appended after the spectrum",
        data=(header + rows + footer).encode("utf-8"),
        recipe=ParseRecipe(kind="spectrum", delimiter="comma", header_rows=1,
                           columns=["x", "amplitude"], x_unit="hz",
                           amplitude_unit="mm_s", detection="rms"),
        outcome="case", gate="fail", severity_expected=True,
        hazard="numeric footer rows restart the axis — the gate must see a non-axis",
    )


def _truncated() -> AdversarialFile:
    """A transfer that died mid-row: the last line has one field and no newline."""
    header = "Hz\tAmp\n"
    points = spectrum_points()
    rows = "".join(f"{f:.4f}\t{a:.5f}\n" for f, a in points[:-1])
    return AdversarialFile(
        name="truncated.txt",
        description="truncated mid-row — final line carries only the frequency",
        data=(header + rows + f"{points[-1][0]:.4f}\t").encode("utf-8"),
        recipe=ParseRecipe(kind="spectrum", delimiter="tab", header_rows=1,
                           columns=["x", "amplitude"], x_unit="hz",
                           amplitude_unit="mm_s", detection="rms"),
        outcome="case", gate="pass", severity_expected=True,
        hazard="the partial row must be dropped, never read as amplitude 0",
    )


def _fixed_width() -> AdversarialFile:
    """Column-aligned output with no delimiter — and a handful of over-range
    bins whose value fills its field and touches the neighbouring column."""
    header = "     FREQ     AMPL\n"
    lines = []
    for i, (f, a) in enumerate(spectrum_points()):
        value = 12345.6789 if i in (200, 201, 202) else a   # over-range bins
        lines.append(f"{f:9.4f}{value:9.4f}")
    return AdversarialFile(
        name="fixed_width.txt",
        description="fixed-width columns; three over-range rows merge into one field",
        data=(header + "\n".join(lines) + "\n").encode("utf-8"),
        recipe=ParseRecipe(kind="spectrum", delimiter="whitespace", header_rows=1,
                           columns=["x", "amplitude"], x_unit="hz",
                           amplitude_unit="mm_s", detection="rms"),
        outcome="case", gate="pass", severity_expected=True,
        hazard="a merged row must be DROPPED, not read as a single huge frequency",
    )


def _two_blocks() -> AdversarialFile:
    """Two spectra in one file — horizontal then vertical, each with its own
    header. One recipe cannot read two spectra; reading them as one produces an
    axis that runs 0-250, then 0-250 again."""
    points = spectrum_points()
    block_h = "CHANNEL: 1H\nHz,Amp\n" + "".join(f"{f:.4f},{a:.5f}\n" for f, a in points)
    block_v = "\nCHANNEL: 1V\nHz,Amp\n" + "".join(f"{f:.4f},{a * 0.6:.5f}\n" for f, a in points)
    return AdversarialFile(
        name="two_blocks.txt",
        description="two spectra (1H then 1V) concatenated in one file",
        data=(block_h + block_v).encode("utf-8"),
        recipe=ParseRecipe(kind="spectrum", delimiter="comma", skip_rows=1, header_rows=1,
                           columns=["x", "amplitude"], x_unit="hz",
                           amplitude_unit="mm_s", detection="rms"),
        outcome="case", gate="fail", severity_expected=True,
        hazard="the axis restarts halfway down — a merged two-channel read",
    )


def _binary_tail() -> AdversarialFile:
    """Clean text for the first 10 KB (so the 8 KB sample sees nothing wrong),
    then a binary blob — a container whose text header precedes packed data."""
    header = "Hz,Amp\n"
    rows = "".join(f"{f:.4f},{a:.5f}\n" for f, a in spectrum_points())
    blob = bytes(range(256)) * 8
    return AdversarialFile(
        name="binary_tail.txt",
        description="text spectrum followed by a raw binary blob (sample sees only text)",
        data=(header + rows).encode("utf-8") + blob,
        recipe=ParseRecipe(kind="spectrum", delimiter="comma", header_rows=1,
                           columns=["x", "amplitude"], x_unit="hz",
                           amplitude_unit="mm_s", detection="rms"),
        outcome="case", gate="pass", severity_expected=True,
        hazard="the sample is clean; only the executor ever meets the NULs",
    )


def _binary_head() -> AdversarialFile:
    """The same trick the other way round: binary from byte zero under a .txt
    suffix. Nothing may reach inference at all."""
    return AdversarialFile(
        name="binary_head.txt",
        description="a binary file wearing a .txt suffix",
        data=b"\x00\x01\x02\x03MAGIC\x00\xff\xfe\x00\x00" + bytes(range(256)) * 20,
        recipe=ParseRecipe(kind="spectrum", delimiter="comma", columns=["x", "amplitude"],
                           x_unit="hz", amplitude_unit="mm_s", detection="rms"),
        outcome="not_text", gate="n/a", severity_expected=False,
        hazard="extension is analyst-supplied; the bytes must be what decides",
    )


# ══════════════════════════════════════════════════════════════════════════
# Data that lies about itself
# ══════════════════════════════════════════════════════════════════════════


def _non_monotonic() -> AdversarialFile:
    """A frequency axis that runs backwards through the middle — a sort that
    went wrong, or a column that was never a frequency axis."""
    points = spectrum_points()
    shuffled = points[:150] + points[150:260][::-1] + points[260:]
    header = "Hz;Amp\n"
    rows = "".join(f"{f:.4f};{a:.5f}\n" for f, a in shuffled)
    return AdversarialFile(
        name="non_monotonic.txt",
        description="frequency axis reverses for 110 rows in the middle of the file",
        data=(header + rows).encode("utf-8"),
        recipe=ParseRecipe(kind="spectrum", delimiter="semicolon", header_rows=1,
                           columns=["x", "amplitude"], x_unit="hz",
                           amplitude_unit="mm_s", detection="rms"),
        outcome="case", gate="fail", severity_expected=True,
        hazard="a non-axis that still looks like two tidy numeric columns",
    )


def _negative_amplitudes() -> AdversarialFile:
    """Spectrum amplitudes cannot be negative. These are (a real-part export,
    or a column that is actually phase)."""
    points = spectrum_points()
    header = "Hz,Amp\n"
    rows = "".join(f"{f:.4f},{a if i % 3 else -a:.5f}\n" for i, (f, a) in enumerate(points))
    return AdversarialFile(
        name="negative_amps.txt",
        description="every third amplitude is negative — a real-part or phase column",
        data=(header + rows).encode("utf-8"),
        recipe=ParseRecipe(kind="spectrum", delimiter="comma", header_rows=1,
                           columns=["x", "amplitude"], x_unit="hz",
                           amplitude_unit="mm_s", detection="rms"),
        outcome="case", gate="fail", severity_expected=True,
        hazard="negative 'amplitudes' would compute a severity from a phase column",
    )


def _all_zero() -> AdversarialFile:
    """A dead channel, a disconnected accelerometer, or a column of padding."""
    header = "Hz,Amp\n"
    rows = "".join(f"{f:.4f},0.00000\n" for f, _ in spectrum_points())
    return AdversarialFile(
        name="all_zero.txt",
        description="frequency axis intact, every amplitude exactly zero",
        data=(header + rows).encode("utf-8"),
        recipe=ParseRecipe(kind="spectrum", delimiter="comma", header_rows=1,
                           columns=["x", "amplitude"], x_unit="hz",
                           amplitude_unit="mm_s", detection="rms"),
        outcome="case", gate="fail", severity_expected=True,
        hazard="a silent channel must never read as a clean bill of health",
    )


def _db_units() -> AdversarialFile:
    """A dB-scaled spectrum. The recipe vocabulary has no 'db', so the only
    HONEST declaration is 'unknown' — which suppresses severity and keeps
    fault-frequency identification. Declaring it a velocity unit instead is the
    immune test, and the highest-value one in this corpus."""
    header = "SPECTRUM EXPORT\nUnits: dB re 1e-6 m/s2\nHz,dB\n"
    rows = "".join(
        f"{f:.4f},{20.0 * math.log10(max(a, 1e-4) / 1e-4):.2f}\n"   # ~41.6 floor, ~86.4 peak
        for f, a in spectrum_points())
    return AdversarialFile(
        name="db_units.txt",
        description="dB-scaled amplitudes (log axis) — no honest velocity reading exists",
        data=(header + rows).encode("utf-8"),
        recipe=ParseRecipe(kind="spectrum", delimiter="comma", skip_rows=2, header_rows=1,
                           columns=["x", "amplitude"], x_unit="hz",
                           amplitude_unit="unknown", detection="rms"),
        outcome="case", gate="pass", severity_expected=False,
        hazard="declared as mm/s a dB floor of 41.6 reads as ISO Zone D on a healthy machine",
    )


def _accel_ms2() -> AdversarialFile:
    """Acceleration in m/s2 — an exact 9.80665 conversion to g, and no severity
    claim under any circumstances."""
    header = "ACCEL SPECTRUM\nUnits: m/s^2 RMS\nSpeed: 1800 rpm\nHz|m/s2\n"
    rows = "".join(f"{f:.4f}|{a * 1.5:.5f}\n" for f, a in spectrum_points())
    return AdversarialFile(
        name="accel_ms2.asc",
        description="acceleration in m/s2, pipe-delimited, speed stated in the header",
        data=(header + rows).encode("utf-8"),
        recipe=ParseRecipe(kind="spectrum", delimiter="pipe", skip_rows=3, header_rows=1,
                           columns=["x", "amplitude"], x_unit="hz", amplitude_unit="m_s2",
                           detection="rms", rpm_source="file_header", rpm_value=1800.0),
        outcome="case", gate="pass", severity_expected=False,
        hazard="acceleration numbers are velocity-sized; only the DECLARED unit stops a severity claim",
    )


def _orders_no_speed() -> AdversarialFile:
    """Shaft orders with no speed anywhere in the file — the conversion depends
    entirely on the form's RPM, so an order axis read as Hz spans 0-8.3 'Hz'."""
    header = "Order\tAmplitude (mm/s RMS)\n"
    rows = "".join(f"{f / SHAFT_HZ:.5f}\t{a:.5f}\n" for f, a in spectrum_points())
    return AdversarialFile(
        name="orders_no_speed.txt",
        description="shaft-order x axis with no speed stated in the file",
        data=(header + rows).encode("utf-8"),
        recipe=ParseRecipe(kind="spectrum", delimiter="tab", header_rows=1,
                           columns=["x", "amplitude"], x_unit="orders",
                           amplitude_unit="mm_s", detection="rms"),
        outcome="case", gate="pass", severity_expected=True,
        hazard="orders read as Hz compresses the whole axis below the shaft line",
    )


def _numeric_preamble() -> AdversarialFile:
    """A B&K-style preamble line that is itself NUMERIC — bin count, fmin, fmax —
    sitting above the data with no column names anywhere. Skipping one row too
    few reads the preamble as a spectrum point at 501 Hz."""
    header = f"{NBINS} 0.0000 {FMAX:.4f}\n"
    rows = "".join(f"{f:.4f} {a:.5f}\n" for f, a in spectrum_points())
    return AdversarialFile(
        name="numeric_preamble.dat",
        description="numeric preamble line (bins/fmin/fmax) above headerless data",
        data=(header + rows).encode("utf-8"),
        recipe=ParseRecipe(kind="spectrum", delimiter="whitespace", skip_rows=1,
                           columns=["x", "amplitude"], x_unit="hz",
                           amplitude_unit="mm_s", detection="rms"),
        outcome="case", gate="pass", severity_expected=True,
        hazard="skip_rows off by one reads the preamble as a 501 Hz point",
    )


def _mostly_prose() -> AdversarialFile:
    """A PDF-to-text dump: a page of prose with a scatter of numbers. Nothing
    here is a spectrum, and the executor must say so rather than analysing the
    handful of rows that happen to have two numeric fields."""
    prose = (
        "MONTHLY CONDITION REPORT\n"
        "Machine 12-P-101 was surveyed on 14 March. Overall vibration has risen\n"
        "since the previous route, from 2.1 mm/s to 3.4 mm/s. The analyst notes\n"
        "an increase at running speed and recommends a repeat reading in 30 days.\n"
        "Bearing temperatures were normal throughout.\n"
        "\n"
        "Readings taken:\n"
        "1 2.1\n2 3.4\n3 2.9\n"
        "\n"
        "No further action at this time. Report ends.\n"
    )
    return AdversarialFile(
        name="prose_report.txt",
        description="a narrative report with a few numbers — not a data export at all",
        data=prose.encode("utf-8"),
        recipe=ParseRecipe(kind="spectrum", delimiter="whitespace", columns=["x", "amplitude"],
                           x_unit="hz", amplitude_unit="mm_s", detection="rms"),
        outcome="refuse", gate="n/a", severity_expected=False,
        hazard="three numeric-looking lines are not a spectrum",
    )


CORPUS: tuple[AdversarialFile, ...] = (
    # encodings
    _utf16_le_bom(),
    _utf16_be_bom(),
    _utf8_bom(),
    _cp1252_degrees(),
    # line endings
    _crlf(),
    _bare_cr(),
    # numbers
    _decimal_comma(),
    _thousands_eu_dot(),
    _thousands_space(),
    _scientific(),
    _scientific_eu(),
    # junk
    _comments_and_blanks(),
    _numeric_footer(),
    _truncated(),
    _fixed_width(),
    _two_blocks(),
    _binary_tail(),
    _binary_head(),
    # data that lies
    _non_monotonic(),
    _negative_amplitudes(),
    _all_zero(),
    _db_units(),
    _accel_ms2(),
    _orders_no_speed(),
    _numeric_preamble(),
    _mostly_prose(),
)

BY_NAME: dict[str, AdversarialFile] = {item.name: item for item in CORPUS}


def write_corpus(directory: Path) -> dict[str, Path]:
    """Materialise every fixture, byte for byte. Returns {name: path}."""
    directory.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    for item in CORPUS:
        path = directory / item.name
        path.write_bytes(item.data)
        paths[item.name] = path
    return paths

# Outreach sample — Synthetic Compressor 01, Fmax 2000 Hz

Session REPORT-2. The 500 Hz sample (`../bpfo_synthetic/`, `scripts/make_sample.py`) was
reviewed by a CAT analyst, who said 500 Hz cannot evaluate the bearing harmonics. These files
are the SAME synthetic case — `make_case("bpfo", seed=1)`: Synthetic Compressor 01,
1800 rpm, bearing 6206, ISO group 2 /
rigid support, the same seeded BPFO tones — regenerated at
Fmax 2000 Hz with 8000 lines (Δf 0.25 Hz; 8001 rows, 0–2000 Hz
inclusive, the closing bin added so the upload lane reads Fmax as 2000 Hz), written
by `scripts/make_sample_csv.py`. The old sample is untouched.

**The operator renders the PDF through the app.** Upload the three files through the live
form, one per direction slot:

| file | direction slot | generator axis | overall (Parseval) |
|---|---|---|---|
| `radial_h.csv` | radial – horizontal | `y` | 5.20 mm/s RMS |
| `radial_v.csv` | radial – vertical | `z` | 4.80 mm/s RMS |
| `axial.csv` | axial | `x` | 0.50 mm/s RMS |

with these form values:

| field | value |
|---|---|
| Running speed | 1800 rpm |
| ISO group | 2 (medium, 15–300 kW) |
| Support | rigid |
| Bearing model | 6206 |
| Velocity unit | mm/s |
| Detection | RMS |
| Mode | spectrum (freq_hz,amplitude) |

A single upload of `radial_h.csv` alone gives the same committed diagnosis; the trio also
engages the axial-ratio and radial-dominance rules exactly as the NCD path does.

## What a CSV cannot carry, and what was done about it

* **A 1× running-speed line at 30 Hz was added on every axis** (pre-scale
  amplitude 0.005 against 0.36 at BPFO). The intake's cross-file speed check
  refuses a trio in which no channel carries a peak near k×shaft, and the seeded recipe has
  none. The line sits below the route profile's amplitude floor, so it satisfies the intake
  check without reaching any shaft-order evidence set; the committed differential is the
  500 Hz sample's. A real machine always shows one.
* **Each axis is scaled uniformly so the Parseval sum of its bins equals the recipe's
  stated overall velocity** (the spectrum lane derives ISO severity from that sum; the
  generator states velocity separately). Uniform scaling leaves every peak-to-mean ratio,
  and so every detector decision, unchanged.

Expected on upload: ISO Zone D (5.20 mm/s RMS on the radial-horizontal
channel against the 4.5 mm/s Zone C/D boundary), one committed finding — bearing
outer-race fault (BPFO) at 107.25 Hz (3.58×) — and a frequency-range line reading
"BPFO … visible to 18×". The regenerated CSVs are byte-identical on every run
(`tests/test_report2_sample_csv.py`).

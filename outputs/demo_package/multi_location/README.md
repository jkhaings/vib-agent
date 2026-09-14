# Route sample - Synthetic Compressor Train 01, four points, Fmax 2000 Hz

Session FIXTURE-1. One compressor train, 1800 rpm, bearing 6206, measured at
**four points** - Motor DE, Motor NDE, Compressor DE, Compressor NDE - each in three
directions, at Fmax 2000 Hz with 12,800 lines (df 0.15625 Hz;
12,801 rows, 0-2000 Hz inclusive, the closing bin added so the upload lane reads
Fmax as 2000 Hz). Written by `scripts/make_multi_sample.py`, keyless and deterministic.

**A BPFO is planted at Compressor DE and nowhere else.** Every other point is healthy.
That is the whole point of the set: the diagnostic act on a route is deciding *which*
point the fault is at, and a single-point fixture cannot exercise it.

The sibling sample `../bpfo_synthetic_fmax2000/` is the same fault at one point only,
at 8,000 lines. Neither script touches the other's directory.

## The machine - every form value a human types

| field | value |
|---|---|
| Machine alias | Synthetic Compressor Train 01 |
| Machine type | compressor |
| Running speed | 1800 rpm |
| Rated power | 75 kW - typed as the ISO group below, which is the field that carries machine size |
| ISO group | 2 (medium, 15-300 kW) |
| Support / mounting | rigid |
| Bearing model | 6206 (the same bearing at all four points) |
| Measurement location | the point's name, per file (see the table below) |
| Velocity unit | mm_s |
| Detection | rms |
| Mode | spectrum |

ISO 20816-3 group 2 / rigid boundaries, from `config/iso_zones.json`:
A/B 1.4, B/C 2.8, C/D 4.5 mm/s RMS.

## The four points

| point | overall H / V / A (mm/s RMS) | zone | planted |
|---|---|---|---|
| Motor DE | 1.10 / 0.90 / 0.50 | **A** | nothing - healthy |
| Motor NDE | 1.80 / 1.40 / 0.60 | **B** | nothing - healthy |
| Compressor DE | 5.20 / 4.80 / 0.50 | **D** | BPFO 107.1875 Hz + 2x - clearly on H and V, weak on A |
| Compressor NDE | 1.20 / 1.00 / 0.55 | **A** | nothing - healthy |

## The files

Upload **one point per job**: three files, one per direction slot. A job takes at most
three files, so there is no twelve-channel upload - four jobs, four reports.

| file | point | direction slot | form value | generator axis | declared type + unit | overall | planted |
|---|---|---|---|---|---|---|---|
| `motor_de_h.csv` | Motor DE | radial - horizontal | `radial_h` | `y` | velocity spectrum, mm/s RMS | 1.10 | nothing - healthy |
| `motor_de_v.csv` | Motor DE | radial - vertical | `radial_v` | `z` | velocity spectrum, mm/s RMS | 0.90 | nothing - healthy |
| `motor_de_a.csv` | Motor DE | axial | `axial` | `x` | velocity spectrum, mm/s RMS | 0.50 | nothing - healthy |
| `motor_nde_h.csv` | Motor NDE | radial - horizontal | `radial_h` | `y` | velocity spectrum, mm/s RMS | 1.80 | nothing - healthy |
| `motor_nde_v.csv` | Motor NDE | radial - vertical | `radial_v` | `z` | velocity spectrum, mm/s RMS | 1.40 | nothing - healthy |
| `motor_nde_a.csv` | Motor NDE | axial | `axial` | `x` | velocity spectrum, mm/s RMS | 0.60 | nothing - healthy |
| `compressor_de_h.csv` | Compressor DE | radial - horizontal | `radial_h` | `y` | velocity spectrum, mm/s RMS | 5.20 | BPFO 107.1875 Hz + 2x - clearly on H and V, weak on A |
| `compressor_de_v.csv` | Compressor DE | radial - vertical | `radial_v` | `z` | velocity spectrum, mm/s RMS | 4.80 | BPFO 107.1875 Hz + 2x - clearly on H and V, weak on A |
| `compressor_de_a.csv` | Compressor DE | axial | `axial` | `x` | velocity spectrum, mm/s RMS | 0.50 | BPFO 107.1875 Hz + 2x - clearly on H and V, weak on A |
| `compressor_nde_h.csv` | Compressor NDE | radial - horizontal | `radial_h` | `y` | velocity spectrum, mm/s RMS | 1.20 | nothing - healthy |
| `compressor_nde_v.csv` | Compressor NDE | radial - vertical | `radial_v` | `z` | velocity spectrum, mm/s RMS | 1.00 | nothing - healthy |
| `compressor_nde_a.csv` | Compressor NDE | axial | `axial` | `x` | velocity spectrum, mm/s RMS | 0.55 | nothing - healthy |
| `compressor_nde_h_ski_slope_artifact_not_a_fault.csv` | Compressor NDE | radial - horizontal | `radial_h` | `y` | velocity spectrum, mm/s RMS | 20.00 | NOT A FAULT - a low-frequency integration artifact lifting the overall to 20 mm/s |

Every file declares the same thing, in the two places the intake reads: the form values
`velocity_unit=mm_s` / `detection_type=rms` /
`mode=spectrum`, and the `declared type + unit` column above. Nothing is left to
assumption, so no report drawn from this set can print an amplitude "as supplied".

## The 13th file: `compressor_nde_h_ski_slope_artifact_not_a_fault.csv`

**This is not a fault case, and it is not a second machine.** It is
`compressor_nde_h.csv` - a healthy point - with a *"ski slope"*
added on top: the monotone low-frequency rise you get from integrating a noisy or
settling accelerometer signal to velocity. It carries no tone, no harmonic and no
bearing defect; the machine content in it is identical to the healthy file's.

What it does carry is an overall of 20 mm/s RMS against that point's true
1.20 mm/s -
about 16.7x, enough to read as Zone D on a machine that is in Zone
A. That is what the file is for: a severity claim that is entirely an
artifact of how the data was captured. The name is long because once uploaded the
filename is the only label that travels with the file - the webapp stores every upload
as `upload.csv`.

## What a CSV cannot carry, and what was done about it

* **A 1x running-speed line at 30 Hz on every file** (pre-scale amplitude
  0.005). The intake's cross-file speed check refuses a trio in which no channel
  carries a peak near k x shaft, and neither seeded recipe has one. The line stands
  7.6-7.8x the spectrum median on the twelve machine files (the check
  needs >= 3x) and 16.9x on the ski-slope file, where the artifact adds to the
  same bin. On all 13 it stays below the route profile's amplitude floor
  (< 0.65x it), so it satisfies the intake without reaching any shaft-order
  evidence set. A real machine always shows one.
* **A weak axial BPFO at Compressor DE** (pre-scale amplitude 0.006), because the
  seeded recipe puts the tone on the two radial axes and nothing on the axial one,
  while a real outer-race defect shows a little axially. Measured at 9.3x the median
  and 0.72x the amplitude floor - visible on the figure, not committed evidence.
  The radial tones sit 35-40x OVER that same floor, which is what "clearly on
  H and V, weak on A" means numerically.
* **Each file is scaled uniformly so the Parseval sum of its bins equals the overall
  its point states** (the spectrum lane derives ISO severity from that sum). Uniform
  scaling leaves every peak-to-mean ratio, and so every detector decision, unchanged.

Expected on upload: Compressor DE commits a bearing outer-race fault (BPFO) at
107.1875 Hz (3.57x) in Zone D; the other three points commit
nothing and read Zone A, B and A. The
files are byte-identical on every run (`tests/test_fixture1_multi_location.py`).

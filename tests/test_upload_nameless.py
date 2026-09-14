"""HOTFIX regression suite — nameless uploads, every supported format.

The webapp discards the user's original filename and stores every upload as
`upload.<ext>` (webapp/app.py). So any adapter that derives meaning from the
filename — a fault class, a load, an expected-answer block, a timestamp —
rejects EVERY real upload. This bit the CWRU path (which parsed a
`Normal_N / OR0##@6_N / …` starter-set convention out of the stem) exactly as
it bit the wind-turbine path in Phase 7B.

This is the class rule, applied across cwru / mfpt / wind_turbine / mafaulda /
tabular / wav / uff, not a per-bug patch: every supported format must parse a
file whose stem is literally `upload` through the real webapp dispatch
(parse_upload). Fixtures are synthesised in-repo at their config-exact shapes;
no binary fixtures are committed.
"""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
import pytest
from scipy.io import savemat, wavfile

from vib_agent.adapters.uploads import parse_upload
from vib_agent.adapters.uploads.common import UploadForm
from vib_agent.config import load_config
from vib_agent.models import Case

RPM = 1797.0


@pytest.fixture(scope="module")
def cfgs():
    return {
        "bearings_cfg": load_config("bearings"),
        "cwru_cfg": load_config("cwru"),
        "mfpt_cfg": load_config("mfpt"),
        "wt_cfg": load_config("wind_turbine"),
        "mafaulda_cfg": load_config("mafaulda"),
    }


def _form(**overrides) -> UploadForm:
    base = dict(machine_alias="Blind Test", rpm=RPM, iso_group="2", iso_support="rigid", machine_type="motor")
    base.update(overrides)
    return UploadForm(**base)


# ── config-exact synthetic builders, each writing to a stem of literally "upload"
def _build_cwru(tmp: Path, cfgs) -> tuple[Path, UploadForm]:
    fs = float(cfgs["cwru_cfg"]["fs_hz"])
    t = np.arange(int(fs * 1.0)) / fs
    sig = np.sin(2 * np.pi * 3000 * t) * (1 + 0.6 * np.sin(2 * np.pi * 107 * t))
    sig += 0.02 * np.random.default_rng(1).standard_normal(t.size)
    p = tmp / "upload.mat"
    savemat(str(p), {"X097_DE_time": sig.reshape(-1, 1)})
    return p, _form(bearing_model="6205")


def _build_mfpt(tmp: Path, cfgs) -> tuple[Path, UploadForm]:
    fs, t = 12000.0, np.arange(int(12000 * 0.5)) / 12000.0
    sig = np.sin(2 * np.pi * 3000 * t) * (1 + 0.5 * np.sin(2 * np.pi * 81.12 * t))
    sig += 0.01 * np.random.default_rng(1).standard_normal(t.size)
    p = tmp / "upload.mat"
    savemat(str(p), {"bearing": {"sr": fs, "gs": sig.reshape(-1, 1), "load": "270", "rate": 25.0}})
    return p, _form(rpm=1500.0, bearing_model="6206")


def _build_wind_turbine(tmp: Path, cfgs) -> tuple[Path, UploadForm]:
    wt = cfgs["wt_cfg"]
    fs = float(wt["sample_rate_hz"])
    n = int(round(fs * float(wt["record_seconds"])))
    shaft_hz = 30.0
    t = np.arange(n) / fs
    vib = np.sin(2 * np.pi * shaft_hz * t) + 0.3 * np.sin(2 * np.pi * 3800 * t)
    vib += 0.05 * np.random.default_rng(7).standard_normal(n)
    pulse_rate = shaft_hz * int(wt["tach_pulses_per_rev"])
    tach = np.arange(0.0, float(wt["record_seconds"]) + 1.0, 1.0 / pulse_rate)
    p = tmp / "upload.mat"
    savemat(str(p), {"tach": tach.reshape(-1, 1), "vibration": vib.reshape(-1, 1)})
    return p, _form(machine_alias="WT-HS-Bearing")


def _build_mafaulda(tmp: Path, cfgs) -> tuple[Path, UploadForm]:
    maf = cfgs["mafaulda_cfg"]
    fs = float(maf["sample_rate_hz"])
    n = int(round(fs * float(maf["record_seconds"])))
    cols = list(maf["columns"])
    shaft_hz = 30.0
    t = np.arange(n) / fs
    rng = np.random.default_rng(3)
    data = np.zeros((n, len(cols)))
    # narrow pulse tach, one spike per rev
    period = 1.0 / shaft_hz
    for k in range(int(float(maf["record_seconds"]) * shaft_hz) + 1):
        idx = int(round(k * period * fs))
        if idx < n:
            data[idx, cols.index("tachometer")] = 5.0
    for c in cols[1:]:
        base = 0.02 * rng.standard_normal(n)
        if c == "underhang_radial":
            base += np.sin(2 * np.pi * shaft_hz * t)
        data[:, cols.index(c)] = base
    p = tmp / "upload.csv"
    np.savetxt(p, data, delimiter=",")
    return p, _form(machine_alias="ABVT-rig", rpm=1770.0, mode="mafaulda")


def _build_tabular_spectrum(tmp: Path, cfgs) -> tuple[Path, UploadForm]:
    n, fmax = 400, 200.0
    freqs = [i * fmax / n for i in range(n)]
    amps = [0.001] * n
    amps[min(range(n), key=lambda i: abs(freqs[i] - 107.03))] = 0.5
    p = tmp / "upload.csv"
    with open(p, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["freq_hz", "amplitude"])
        for fr, a in zip(freqs, amps):
            w.writerow([fr, a])
    return p, _form(mode="spectrum")


def _build_wav(tmp: Path, cfgs) -> tuple[Path, UploadForm]:
    fs = 12000
    t = np.arange(int(fs * 1.0)) / fs
    sig = np.sin(2 * np.pi * 3000 * t) * (1 + 0.5 * np.sin(2 * np.pi * 107.03 * t))
    sig += 0.01 * np.random.default_rng(1).standard_normal(t.size)
    norm = sig / np.max(np.abs(sig)) * 0.8
    p = tmp / "upload.wav"
    wavfile.write(str(p), fs, (norm * 32767).astype(np.int16))
    return p, _form()


def _build_uff(tmp: Path, cfgs) -> tuple[Path, UploadForm]:
    import pyuff

    fs = 12000.0
    t = np.arange(int(fs * 1.0)) / fs
    sig = np.sin(2 * np.pi * 3000 * t) * (1 + 0.5 * np.sin(2 * np.pi * 107.03 * t))
    p = tmp / "upload.uff"
    dset = pyuff.prepare_58(
        func_type=1,
        rsp_node=1, rsp_dir=1, ref_node=1, ref_dir=1,
        data=sig.tolist(), x=t.tolist(), abscissa_spacing=1,
        orddenom_spec_data_type=0, ordinate_spec_data_type=0,
        abscissa_spec_data_type=0, z_axis_spec_data_type=0,
    )
    u = pyuff.UFF(str(p))
    u.write_sets([dset], mode="add")
    return p, _form()


_BUILDERS = {
    "cwru": _build_cwru,
    "mfpt": _build_mfpt,
    "wind_turbine": _build_wind_turbine,
    "mafaulda": _build_mafaulda,
    "tabular_spectrum": _build_tabular_spectrum,
    "wav": _build_wav,
    "uff": _build_uff,
}


class TestNamelessUploadAllFormats:
    """Class rule: EVERY supported format parses a stem-'upload' file."""

    @pytest.mark.parametrize("fmt", sorted(_BUILDERS))
    def test_nameless_upload_parses(self, fmt, tmp_path, cfgs):
        path, form = _BUILDERS[fmt](tmp_path, cfgs)
        assert path.stem == "upload", "precondition: the webapp stores the file as upload.<ext>"
        case, kind, note = parse_upload(path, form, **cfgs)
        assert isinstance(case, Case)
        assert case.spectra, f"{fmt}: adapter produced no spectrum"
        # machine context came from the FORM, never the filename
        assert case.machine.name == form.machine_alias
        assert "upload" not in (case.machine.name or "")


# ── forbidden internal identifiers that must never surface in user-facing copy
_FORBIDDEN = ("upload", "convention", "_DE_time", "starter-set", "path.name",
              "keys:", "Normal_N", "OR0", "@6_", "stem", "docs section", "official docs")


def _user_message(exc: Exception) -> str:
    # what the child prints as `PARSE_ERROR: {exc}` -> becomes the ERROR card copy
    return str(exc)


class TestErrorCopyHasNoInternalIdentifiers:
    """Every user-facing parse failure is in user terms — it never names the
    stored filename ('upload'), a filename convention, or an internal channel/key
    dump. Mirrors what the ERROR card shows (webapp/_parse_child.py: PARSE_ERROR: {exc})."""

    def _assert_clean(self, exc: Exception):
        msg = _user_message(exc)
        low = msg.lower()
        for tok in _FORBIDDEN:
            assert tok.lower() not in low, f"error copy leaks internal identifier {tok!r}: {msg!r}"

    def test_cwru_mat_without_de_channel(self, tmp_path, cfgs):
        p = tmp_path / "upload.mat"
        savemat(str(p), {"X097_FE_time": np.zeros((300, 1))})  # sniffs unknown -> CWRU path
        with pytest.raises(ValueError) as ei:
            parse_upload(p, _form(), **cfgs)
        self._assert_clean(ei.value)

    def test_cwru_mat_too_short(self, tmp_path, cfgs):
        p = tmp_path / "upload.mat"
        savemat(str(p), {"X097_DE_time": np.zeros((10, 1))})
        with pytest.raises(ValueError) as ei:
            parse_upload(p, _form(), **cfgs)
        self._assert_clean(ei.value)

    def test_tabular_missing_columns(self, tmp_path, cfgs):
        p = tmp_path / "upload.csv"
        with open(p, "w", newline="") as f:
            csv.writer(f).writerows([["a", "b"], [1, 2]])
        with pytest.raises(ValueError) as ei:
            parse_upload(p, _form(mode="spectrum"), **cfgs)
        self._assert_clean(ei.value)

    def test_wav_empty(self, tmp_path, cfgs):
        p = tmp_path / "upload.wav"
        wavfile.write(str(p), 12000, np.array([], dtype=np.int16))
        with pytest.raises(ValueError) as ei:
            parse_upload(p, _form(), **cfgs)
        self._assert_clean(ei.value)

    def test_mafaulda_wrong_shape(self, tmp_path, cfgs):
        p = tmp_path / "upload.csv"
        np.savetxt(p, np.zeros((100, 8)), delimiter=",")  # wrong sample count
        with pytest.raises(ValueError) as ei:
            parse_upload(p, _form(mode="mafaulda"), **cfgs)
        self._assert_clean(ei.value)

    def test_uff_wrong_dataset(self, tmp_path, cfgs):
        import pyuff

        p = tmp_path / "upload.uff"
        dset = {"type": 15, "node_nums": [1], "x": [0.0], "y": [0.0], "z": [0.0]}
        u = pyuff.UFF(str(p))
        u.write_sets(dset, "add")
        with pytest.raises(ValueError) as ei:
            parse_upload(p, _form(), **cfgs)
        self._assert_clean(ei.value)

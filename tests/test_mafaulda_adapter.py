"""MAFAULDA adapter tests (Phase 8 — imbalance + misalignment validation).

Synthetic-fixture tests always run (no external data needed). The end-to-end
tests are skipped when data/mafaulda/*.csv is absent — same data-gated design as
tests/test_mfpt_adapter.py and tests/test_wind_turbine_adapter.py.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from vib_agent.adapters.mafaulda import (
    cross_check_speed,
    label_from_path,
    load_mafaulda_csv,
    raw_spectrum,
    shaft_hz_from_tach,
    to_case,
)
from vib_agent.config import load_config
from vib_agent.pdm_core.bearing_rca import bearing_frequencies
from vib_agent.models import BearingSpec

_DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "mafaulda"


@pytest.fixture
def mafaulda_cfg() -> dict:
    return load_config("mafaulda")


@pytest.fixture
def bearings_cfg() -> dict:
    return load_config("bearings")


def _pulse_tach(shaft_hz: float, fs: float, seconds: float) -> np.ndarray:
    """A narrow-pulse tachometer signal: one tall spike per revolution over a
    quiet baseline — the shape that makes 'loudest FFT peak' unreliable (its
    2nd harmonic can outweigh the fundamental)."""
    n = int(round(fs * seconds))
    t = np.arange(n) / fs
    sig = np.zeros(n)
    period = 1.0 / shaft_hz
    for k in range(int(seconds * shaft_hz) + 1):
        idx = int(round(k * period * fs))
        if idx < n:
            sig[idx] = 5.0  # tall narrow pulse
    return sig


def _synth_csv(path: Path, mafaulda_cfg: dict, *, shaft_hz: float = 30.0,
               one_x_axis: str = "underhang_radial") -> Path:
    """Write a valid 8-column MAFAULDA CSV with a pulse tach and a 1x tone on a
    chosen accelerometer channel."""
    fs = float(mafaulda_cfg["sample_rate_hz"])
    seconds = float(mafaulda_cfg["record_seconds"])
    n = int(round(fs * seconds))
    t = np.arange(n) / fs
    cols = list(mafaulda_cfg["columns"])
    rng = np.random.default_rng(3)
    data = np.zeros((n, len(cols)))
    data[:, cols.index("tachometer")] = _pulse_tach(shaft_hz, fs, seconds)
    for c in cols[1:]:
        base = 0.02 * rng.standard_normal(n)
        if c == one_x_axis:
            base += np.sin(2 * np.pi * shaft_hz * t)
        data[:, cols.index(c)] = base
    np.savetxt(path, data, delimiter=",")
    return path


class TestLabelFromPath:
    def test_normal(self):
        assert label_from_path("data/mafaulda/normal/12.288.csv") == ("normal", None, 12.288)

    def test_imbalance_reads_weight_and_speed(self):
        assert label_from_path("data/mafaulda/imbalance/20g/29.4912.csv") == ("imbalance", "20g", 29.4912)

    def test_horizontal_misalignment(self):
        fam, sev, spd = label_from_path("data/mafaulda/horizontal-misalignment/1.0mm/30.5.csv")
        assert fam == "horizontal-misalignment" and sev == "1.0mm" and spd == 30.5

    def test_vertical_misalignment(self):
        fam, sev, spd = label_from_path("data/mafaulda/vertical-misalignment/0.51mm/55.0.csv")
        assert fam == "vertical-misalignment" and sev == "0.51mm"

    def test_non_numeric_filename_raises(self):
        with pytest.raises(ValueError, match="rotation frequency"):
            label_from_path("data/mafaulda/normal/upload.csv")


class TestBearingGeometry:
    def test_reproduces_published_orders(self, bearings_cfg):
        # Official docs publish FTF 0.3750, BPFO 2.9980, BPFI 5.0020, BSF 1.8710
        # (CPM/rpm = orders x shaft). Our frozen formulas must match — this is
        # WHY the entry is not marked _verify.
        entry = bearings_cfg["bearings"]["MAFAULDA_ABVT"]
        b = BearingSpec(**{k: v for k, v in entry.items() if not k.startswith("_")})
        f = bearing_frequencies(b, 1.0)
        assert f.BPFO == pytest.approx(2.9980, abs=0.01)
        assert f.BPFI == pytest.approx(5.0020, abs=0.01)
        assert f.BSF == pytest.approx(1.8710, abs=0.01)
        assert f.FTF == pytest.approx(0.3750, abs=0.01)


class TestShaftFromTach:
    def test_returns_fundamental_not_loudest(self, mafaulda_cfg):
        # The whole point: a pulse train's 2nd harmonic can be louder than its
        # fundamental. The fundamental (lowest significant peak) must win.
        fs = float(mafaulda_cfg["sample_rate_hz"])
        tach = _pulse_tach(30.0, fs, float(mafaulda_cfg["record_seconds"]))
        got = shaft_hz_from_tach(tach, fs, search_band_hz=tuple(mafaulda_cfg["tach_search_band_hz"]))
        assert got == pytest.approx(30.0, abs=0.5)

    def test_low_speed_not_doubled(self, mafaulda_cfg):
        # The real failure case: normal/12.288.csv where argmax picked 2x.
        fs = float(mafaulda_cfg["sample_rate_hz"])
        tach = _pulse_tach(12.0, fs, float(mafaulda_cfg["record_seconds"]))
        got = shaft_hz_from_tach(tach, fs, search_band_hz=tuple(mafaulda_cfg["tach_search_band_hz"]))
        assert got == pytest.approx(12.0, abs=0.5)  # not ~24

    def test_no_peak_raises_never_falls_back(self, mafaulda_cfg):
        fs = float(mafaulda_cfg["sample_rate_hz"])
        flat = np.zeros(int(fs * float(mafaulda_cfg["record_seconds"])))
        with pytest.raises(ValueError, match="no significant tach peak"):
            shaft_hz_from_tach(flat, fs, search_band_hz=(5, 100))


class TestCrossCheck:
    def test_slip_inside_tolerance_not_flagged(self):
        flagged, delta = cross_check_speed(29.5, 29.4912, tolerance_pct=2.0)
        assert not flagged and delta < 2.0

    def test_doubling_is_flagged(self):
        flagged, delta = cross_check_speed(24.0, 12.288, tolerance_pct=2.0)
        assert flagged and delta > 90.0

    def test_flags_never_raises(self):
        assert cross_check_speed(99.0, 30.0, tolerance_pct=2.0)[0] is True


class TestLoadAndCase:
    def test_load_asserts_column_count(self, tmp_path, mafaulda_cfg):
        p = tmp_path / "12.0.csv"
        np.savetxt(p, np.zeros((10, 5)), delimiter=",")  # wrong column count
        with pytest.raises(ValueError, match="columns"):
            load_mafaulda_csv(p, mafaulda_cfg=mafaulda_cfg)

    def test_load_asserts_sample_count(self, tmp_path, mafaulda_cfg):
        p = tmp_path / "12.0.csv"
        np.savetxt(p, np.zeros((100, 8)), delimiter=",")  # too few rows
        with pytest.raises(ValueError, match="samples|expected"):
            load_mafaulda_csv(p, mafaulda_cfg=mafaulda_cfg)

    def test_raw_spectrum_is_not_envelope(self, mafaulda_cfg):
        # A pure 30 Hz tone must land at 30 Hz in a RAW spectrum (an envelope
        # of the same tone would be near-DC). This guards the deliberate
        # raw-vs-envelope decision that Phase 8 rests on.
        fs = 50000.0
        t = np.arange(int(fs * 5)) / fs
        tone = np.sin(2 * np.pi * 30.0 * t)
        sp = raw_spectrum(tone, fs, region_hz=(0, 500))
        fr = np.asarray(sp.freq_hz)
        am = np.asarray(sp.amplitude)
        peak_hz = fr[int(np.argmax(am))]
        assert peak_hz == pytest.approx(30.0, abs=1.0)

    def test_to_case_maps_axes_and_attaches_bearing(self, tmp_path, mafaulda_cfg, bearings_cfg):
        # Write the fixture directly under a labelled dir so to_case() reads the
        # family/severity/speed from the directory structure.
        labelled = tmp_path / "imbalance" / "6g" / "30.0.csv"
        labelled.parent.mkdir(parents=True)
        _synth_csv(labelled, mafaulda_cfg, shaft_hz=30.0)
        case = to_case(labelled, mafaulda_cfg=mafaulda_cfg, bearings_cfg=bearings_cfg)
        assert case.source == "mafaulda"
        assert set(case.spectra.keys()) == {"x", "y", "z"}
        assert case.machine.axial_axis == "x"
        assert case.machine.bearing.model == "MAFAULDA_ABVT"
        assert case.validation_scope == ["rca"]
        assert case.expected.fault_type == "imbalance"
        assert case.expected.severity_param == "6g"
        assert case.expected.speed_hz == 30.0
        assert case.expected.faults == ["imbalance"]


class TestUploadPath:
    def test_csv_mode_mafaulda_routes_here(self, tmp_path, mafaulda_cfg, bearings_cfg):
        from vib_agent.adapters.uploads import parse_upload
        from vib_agent.adapters.uploads.common import UploadForm

        # webapp stores the upload as upload.csv (stem discarded, ext kept)
        src = _synth_csv(tmp_path / "src.csv", mafaulda_cfg, shaft_hz=30.0)
        up = tmp_path / "upload.csv"
        up.write_bytes(src.read_bytes())
        form = UploadForm(machine_alias="rig", rpm=1800.0, iso_group="2",
                          iso_support="rigid", machine_type="motor", mode="mafaulda")
        case, kind, note = parse_upload(
            up, form, bearings_cfg=bearings_cfg, cwru_cfg=load_config("cwru"),
            mfpt_cfg=load_config("mfpt"), wt_cfg=load_config("wind_turbine"),
            mafaulda_cfg=mafaulda_cfg,
        )
        assert kind == "mafaulda_csv"
        assert set(case.spectra.keys()) == {"x", "y", "z"}
        assert case.machine.bearing.model == "MAFAULDA_ABVT"

    def test_upload_without_cfg_raises(self, tmp_path, bearings_cfg, mafaulda_cfg):
        from vib_agent.adapters.uploads import parse_upload
        from vib_agent.adapters.uploads.common import UploadForm

        src = _synth_csv(tmp_path / "src.csv", mafaulda_cfg, shaft_hz=30.0)
        up = tmp_path / "upload.csv"
        up.write_bytes(src.read_bytes())
        form = UploadForm(machine_alias="rig", rpm=1800.0, iso_group="2",
                          iso_support="rigid", machine_type="motor", mode="mafaulda")
        with pytest.raises(Exception, match="mafaulda_cfg"):
            parse_upload(up, form, bearings_cfg=bearings_cfg, mafaulda_cfg=None)


@pytest.mark.skipif(not list(_DATA_DIR.rglob("*.csv")), reason="data/mafaulda absent")
class TestRealData:
    def test_subset_present(self):
        assert len(list(_DATA_DIR.rglob("*.csv"))) >= 40

    def test_real_normal_shaft_in_range(self, mafaulda_cfg):
        f = sorted((_DATA_DIR / "normal").glob("*.csv"))[0]
        sig = load_mafaulda_csv(f, mafaulda_cfg=mafaulda_cfg)
        shaft = shaft_hz_from_tach(
            sig["tachometer"], float(mafaulda_cfg["sample_rate_hz"]),
            search_band_hz=tuple(mafaulda_cfg["tach_search_band_hz"]),
        )
        assert 11.0 < shaft < 62.0

    def test_real_low_speed_file_not_doubled(self, mafaulda_cfg):
        # normal/12.288.csv is the argmax-picks-2x trap; the fundamental must win.
        f = _DATA_DIR / "normal" / "12.288.csv"
        if not f.exists():
            pytest.skip("12.288.csv not in subset")
        sig = load_mafaulda_csv(f, mafaulda_cfg=mafaulda_cfg)
        shaft = shaft_hz_from_tach(
            sig["tachometer"], float(mafaulda_cfg["sample_rate_hz"]),
            search_band_hz=tuple(mafaulda_cfg["tach_search_band_hz"]),
        )
        assert shaft == pytest.approx(12.0, abs=0.5), f"got {shaft} (doubled?)"

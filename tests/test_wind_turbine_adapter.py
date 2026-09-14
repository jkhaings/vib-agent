"""Wind-turbine run-to-failure adapter tests (Phase 7B — the TIME dimension).

Synthetic-fixture tests always run (no external data needed). The end-to-end
tests are skipped when data/wind_turbine/*.mat is absent — same data-gated
design as tests/test_mfpt_adapter.py and tests/test_cwru_adapter.py.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest
from scipy.io import savemat

from vib_agent.adapters.uploads import _sniff_mat_kind
from vib_agent.adapters.wind_turbine import (
    cross_check_shaft_rate,
    load_wt_mat,
    one_x_from_spectrum,
    overall_rms_g,
    parse_timestamp,
    shaft_hz_from_tach,
    to_case,
)
from vib_agent.config import load_config

_DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "wind_turbine"


@pytest.fixture
def wt_cfg() -> dict:
    return load_config("wind_turbine")


@pytest.fixture
def bearings_cfg() -> dict:
    return load_config("bearings")


def _synth_wt_mat(path: Path, wt_cfg: dict, *, shaft_hz: float = 30.0, seconds: float | None = None) -> Path:
    """A minimal file with this dataset's exact shape: {tach, vibration}.
    tach is a pulse-TIME vector at 2 pulses/rev, not a waveform."""
    fs = float(wt_cfg["sample_rate_hz"])
    seconds = seconds if seconds is not None else float(wt_cfg["record_seconds"])
    n = int(round(fs * seconds))
    t = np.arange(n) / fs
    rng = np.random.default_rng(7)
    # 1x shaft + a bearing-band resonance + noise
    vib = (
        1.0 * np.sin(2 * np.pi * shaft_hz * t)
        + 0.3 * np.sin(2 * np.pi * 3800.0 * t)
        + 0.05 * rng.standard_normal(n)
    )
    ppr = int(wt_cfg["tach_pulses_per_rev"])
    pulse_rate = shaft_hz * ppr
    tach = np.arange(0.0, 40.0, 1.0 / pulse_rate)
    savemat(str(path), {"tach": tach.reshape(-1, 1), "vibration": vib.reshape(-1, 1)})
    return path


class TestTimestampParsing:
    def test_parses_dataset_naming(self):
        ts = parse_timestamp("data-20130307T015746Z.mat")
        assert ts == datetime(2013, 3, 7, 1, 57, 46, tzinfo=timezone.utc)

    def test_chronology_orders_correctly(self):
        names = ["data-20130425T232202Z.mat", "data-20130307T015746Z.mat", "data-20130401T000000Z.mat"]
        assert [p[5:13] for p in sorted(names, key=parse_timestamp)] == ["20130307", "20130401", "20130425"]

    @pytest.mark.parametrize("bad", ["sensor-2013.mat", "data-2013-03-07.mat", "data.mat", "data-20130307T0157Z.mat"])
    def test_unparseable_name_raises_never_defaults(self, bad):
        # Chronology is INVARIANT 2 — a name that doesn't parse must never
        # silently become "now", which would corrupt the ordering.
        with pytest.raises(ValueError, match="chronology|does not match"):
            parse_timestamp(bad)


class TestShaftRateFromTach:
    def test_two_pulses_per_rev_halves_the_pulse_rate(self):
        # 60.09 Hz pulses at 2 ppr -> 30.04 Hz shaft (the evidence-backed
        # configuration; see config/wind_turbine.json _pulses_per_rev_note)
        tach = np.arange(0.0, 10.0, 1.0 / 60.0885)
        assert shaft_hz_from_tach(tach, pulses_per_rev=2) == pytest.approx(30.044, rel=1e-3)

    def test_one_pulse_per_rev_is_the_pulse_rate(self):
        tach = np.arange(0.0, 10.0, 1.0 / 60.0885)
        assert shaft_hz_from_tach(tach, pulses_per_rev=1) == pytest.approx(60.0885, rel=1e-3)

    def test_rejects_non_monotonic_pulse_times(self):
        with pytest.raises(ValueError, match="monotonically increasing"):
            shaft_hz_from_tach(np.array([0.0, 0.02, 0.01, 0.03]), pulses_per_rev=2)

    def test_rejects_too_few_pulses(self):
        with pytest.raises(ValueError, match=">= 2"):
            shaft_hz_from_tach(np.array([0.5]), pulses_per_rev=2)


class TestCrossCheck:
    def test_agreement_inside_tolerance_is_not_flagged(self):
        flagged, delta = cross_check_shaft_rate(30.044, 30.00, tolerance_pct=3.0, source_name="t")
        assert not flagged
        assert delta == pytest.approx(0.147, abs=0.01)

    def test_disagreement_beyond_tolerance_is_flagged(self):
        # e.g. what a 1-pulse/rev misreading would look like against a real 1x
        flagged, delta = cross_check_shaft_rate(60.089, 30.00, tolerance_pct=3.0, source_name="t")
        assert flagged
        assert delta > 100.0

    def test_flags_rather_than_raises(self):
        # A variable-speed turbine legitimately drifts; the phase's job is to
        # REPORT disagreement, not crash on it.
        flagged, _ = cross_check_shaft_rate(99.0, 30.0, tolerance_pct=3.0, source_name="t")
        assert flagged is True


class TestLoadAndCase:
    def test_load_asserts_record_length(self, tmp_path, wt_cfg):
        p = _synth_wt_mat(tmp_path / "data-20130307T015746Z.mat", wt_cfg, seconds=2.0)
        with pytest.raises(ValueError, match="expected|sample rate"):
            load_wt_mat(p, wt_cfg=wt_cfg)

    def test_load_returns_signal_fs_timestamp(self, tmp_path, wt_cfg):
        p = _synth_wt_mat(tmp_path / "data-20130307T015746Z.mat", wt_cfg)
        d = load_wt_mat(p, wt_cfg=wt_cfg)
        assert d["fs"] == wt_cfg["sample_rate_hz"]
        assert len(d["vibration"]) == int(wt_cfg["sample_rate_hz"] * wt_cfg["record_seconds"])
        assert d["timestamp"] == datetime(2013, 3, 7, 1, 57, 46, tzinfo=timezone.utc)

    def test_missing_variable_raises(self, tmp_path, wt_cfg):
        p = tmp_path / "data-20130307T015746Z.mat"
        savemat(str(p), {"vibration": np.zeros(10)})
        with pytest.raises(ValueError, match="tach"):
            load_wt_mat(p, wt_cfg=wt_cfg)

    def test_load_tolerates_a_nameless_upload(self, tmp_path, wt_cfg):
        # REGRESSION (Phase 7B Part C): the webapp deliberately discards the
        # user's original filename and stores the upload as `upload`, so a
        # loader that DEMANDS a data-YYYYMMDDTHHMMSSZ name rejects every real
        # upload. Timestamp is best-effort; chronology is enforced by the
        # caller that actually orders files.
        p = _synth_wt_mat(tmp_path / "data-20130307T015746Z.mat", wt_cfg)
        upload = tmp_path / "upload"
        upload.write_bytes(p.read_bytes())
        d = load_wt_mat(upload, wt_cfg=wt_cfg)
        assert d["timestamp"] is None
        assert len(d["vibration"]) == int(wt_cfg["sample_rate_hz"] * wt_cfg["record_seconds"])

    def test_one_x_recovers_the_injected_shaft_rate(self, tmp_path, wt_cfg):
        p = _synth_wt_mat(tmp_path / "data-20130307T015746Z.mat", wt_cfg, shaft_hz=30.0)
        d = load_wt_mat(p, wt_cfg=wt_cfg)
        f1x, _ = one_x_from_spectrum(d["vibration"], d["fs"], expected_hz=30.044, search_pct=10.0)
        assert f1x == pytest.approx(30.0, abs=0.5)

    def test_to_case_scope_and_no_fabricated_bearing(self, tmp_path, wt_cfg, bearings_cfg):
        p = _synth_wt_mat(tmp_path / "data-20130307T015746Z.mat", wt_cfg)
        case = to_case(p, wt_cfg=wt_cfg, bearings_cfg=bearings_cfg)
        assert case.source == "wind_turbine"
        assert case.validation_scope == ["rca", "trend_relative", "anomaly"]
        # Geometry is BLOCKED (BLOCKED_phase7b_bearing_geometry.md) — the
        # adapter must NOT invent one to make the RCA look fuller.
        assert case.machine.bearing is None
        assert case.sensor_data.rpm == pytest.approx(30.0 * 60, rel=1e-2)
        assert case.sensor_data.y_rms_ACC_G > 0

    def test_to_case_history_is_caller_supplied(self, tmp_path, wt_cfg, bearings_cfg):
        from vib_agent.models import HistoryPoint

        p = _synth_wt_mat(tmp_path / "data-20130307T015746Z.mat", wt_cfg)
        hist = [HistoryPoint(ts=datetime(2013, 3, d, tzinfo=timezone.utc), value=2.0) for d in (1, 2, 3)]
        case = to_case(p, wt_cfg=wt_cfg, bearings_cfg=bearings_cfg, history=hist)
        assert case.history is not None and len(case.history) == 3
        assert to_case(p, wt_cfg=wt_cfg, bearings_cfg=bearings_cfg).history is None

    def test_overall_rms_matches_numpy(self):
        x = np.array([3.0, -4.0, 3.0, -4.0])
        assert overall_rms_g(x) == pytest.approx(3.5355, abs=1e-3)


class TestUploadSniff:
    def test_sniffs_wind_turbine_shape(self, tmp_path, wt_cfg):
        p = _synth_wt_mat(tmp_path / "data-20130307T015746Z.mat", wt_cfg)
        assert _sniff_mat_kind(p) == "wind_turbine"

    def test_requires_both_names_not_either(self, tmp_path):
        # A stray 'vibration' from some other vendor must NOT be claimed here.
        p = tmp_path / "other.mat"
        savemat(str(p), {"vibration": np.zeros(10)})
        assert _sniff_mat_kind(p) == "unknown"

    def test_does_not_shadow_mfpt_or_cwru(self, tmp_path):
        mfpt = tmp_path / "m.mat"
        savemat(str(mfpt), {"bearing": {"sr": 97656.0, "gs": np.zeros(10), "rate": 25.0}})
        assert _sniff_mat_kind(mfpt) == "mfpt"
        cwru = tmp_path / "c.mat"
        savemat(str(cwru), {"X097_DE_time": np.zeros(10)})
        assert _sniff_mat_kind(cwru) == "cwru"

    def test_upload_parse_declines_bearing_and_history(self, tmp_path, wt_cfg, bearings_cfg):
        from vib_agent.adapters.uploads import parse_upload
        from vib_agent.adapters.uploads.common import UploadForm

        # Named `upload` exactly as the webapp stores it — see
        # test_load_tolerates_a_nameless_upload.
        src = _synth_wt_mat(tmp_path / "data-20130307T015746Z.mat", wt_cfg)
        p = tmp_path / "upload.mat"
        p.write_bytes(src.read_bytes())
        form = UploadForm(machine_alias="WT", rpm=1800.0, iso_group="2", iso_support="rigid", machine_type="motor")
        case, kind, note = parse_upload(
            p, form, bearings_cfg=bearings_cfg, cwru_cfg=load_config("cwru"),
            mfpt_cfg=load_config("mfpt"), wt_cfg=wt_cfg,
        )
        assert kind == "wind_turbine_mat"
        assert case.machine.bearing is None
        assert "no prior readings" in note.lower()
        assert "bearing geometry" in note.lower()


@pytest.mark.skipif(not list(_DATA_DIR.glob("data-*.mat")), reason="data/wind_turbine absent")
class TestRealData:
    def test_all_fifty_files_present_and_chronological(self):
        files = sorted(_DATA_DIR.glob("data-*.mat"), key=parse_timestamp)
        assert len(files) == 50
        stamps = [parse_timestamp(f) for f in files]
        assert all(b > a for a, b in zip(stamps, stamps[1:]))

    def test_real_shaft_rate_cross_checks_within_tolerance(self, wt_cfg):
        # The spec's mandated check: tach-derived rate vs the raw spectrum's
        # 1x peak, on an early file. >3% would flag the pulses-per-rev call.
        f = sorted(_DATA_DIR.glob("data-*.mat"), key=parse_timestamp)[0]
        d = load_wt_mat(f, wt_cfg=wt_cfg)
        shaft = shaft_hz_from_tach(d["tach"], pulses_per_rev=int(wt_cfg["tach_pulses_per_rev"]))
        f1x, _ = one_x_from_spectrum(
            d["vibration"], d["fs"], expected_hz=shaft, search_pct=float(wt_cfg["one_x_search_pct"])
        )
        flagged, delta = cross_check_shaft_rate(
            shaft, f1x, tolerance_pct=float(wt_cfg["shaft_cross_check_tolerance_pct"]), source_name=f.name
        )
        assert not flagged, f"shaft rate disagreement {delta:.2f}% — re-check tach_pulses_per_rev"
        assert 29.0 < shaft < 32.0

    def test_real_file_sniffs_as_wind_turbine(self):
        f = sorted(_DATA_DIR.glob("data-*.mat"))[0]
        assert _sniff_mat_kind(f) == "wind_turbine"

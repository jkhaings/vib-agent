"""MFPT adapter tests (Phase 7 — cross-rig validation + real-world field
test).

Geometry sanity, round-trip, and synthetic-fixture tests always run (no
external data needed). The end-to-end tests are skipped when data/mfpt/*.mat
is absent — same data-gated design as tests/test_cwru_adapter.py.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from scipy.io import savemat

from vib_agent.adapters.mfpt import (
    assert_shaft_rate,
    equivalent_bearing_from_frequencies,
    load_mfpt_mat,
    to_case,
)
from vib_agent.config import load_config, load_thresholds
from vib_agent.models import BearingSpec
from vib_agent.pdm_core.bearing_rca import bearing_frequencies
from vib_agent.pipeline import run_analysis

_DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "mfpt"

# Published via the dataset's own 'MFPT Fault Data Sets/5 - Analyses/
# NiceBearing.m' + 'GetBearFreqRatio.m' (Eric Bechhoefer, 2009): roller
# 0.235in, pitch 1.245in, contact angle 0deg, 8 elements. His outer/inner
# race ratio formulas are algebraically identical to bearing_frequencies()'s
# BPFO/BPFI (verified by inspection, Phase 7).
_PUBLISHED_ORDERS = {"BPFO": 3.245, "BPFI": 4.755}


@pytest.fixture
def mfpt_cfg() -> dict:
    return load_config("mfpt")


@pytest.fixture
def bearings_cfg() -> dict:
    return load_config("bearings")


@pytest.fixture
def bearing_spec(bearings_cfg) -> BearingSpec:
    raw = bearings_cfg["bearings"]["MFPT_NICE"]
    return BearingSpec(**{k: v for k, v in raw.items() if not k.startswith("_")})


def _bpfo_modulated_waveform(
    *, fs: float, duration: float, carrier_hz: float, mod_hz: float, seed: int = 1
) -> np.ndarray:
    t = np.arange(int(fs * duration)) / fs
    signal = np.sin(2 * np.pi * carrier_hz * t) * (1 + 0.5 * np.sin(2 * np.pi * mod_hz * t))
    signal += 0.01 * np.random.default_rng(seed).standard_normal(len(t))
    return signal


def _write_rig_fixture(path: Path, *, gs: np.ndarray, sr: float, rate: float, load: str = "270") -> None:
    savemat(str(path), {"bearing": {"sr": sr, "gs": gs.reshape(-1, 1), "load": load, "rate": rate}})


class TestGeometrySanity:
    """Independent check of MFPT_NICE against the dataset's own published
    orders (not the CWRU orders — a different bearing)."""

    def test_mfpt_nice_orders_within_one_percent_of_published(self, bearing_spec):
        freqs = bearing_frequencies(bearing_spec, shaft_hz=1.0)
        computed = {"BPFO": freqs.BPFO, "BPFI": freqs.BPFI}
        for key, published in _PUBLISHED_ORDERS.items():
            delta_pct = abs(computed[key] - published) / published * 100.0
            assert delta_pct < 1.0, (
                f"{key}: computed {computed[key]:.4f} vs published {published} ({delta_pct:.2f}% off)"
            )


class TestEquivalentBearingFromFrequencies:
    def test_exactly_reproduces_bpfo_bpfi_when_orders_sum_to_an_integer(self):
        # n_balls = round(outer + inner) is exact (no rounding error) when
        # the two orders already sum to a whole number.
        outer_order, inner_order = 8.0, 11.0
        bearing = equivalent_bearing_from_frequencies(outer_order, inner_order)
        freqs = bearing_frequencies(bearing, shaft_hz=1.0)
        assert freqs.BPFO == pytest.approx(outer_order, abs=1e-9)
        assert freqs.BPFI == pytest.approx(inner_order, abs=1e-9)

    def test_closely_approximates_bpfo_bpfi_when_orders_dont_sum_to_an_integer(self):
        # n_balls rounds 18.92 -> 19, so reproduction is close but not
        # exact -- bounded by the rounding, not unbounded.
        outer_order, inner_order = 8.2, 10.72
        bearing = equivalent_bearing_from_frequencies(outer_order, inner_order)
        freqs = bearing_frequencies(bearing, shaft_hz=1.0)
        assert freqs.BPFO == pytest.approx(outer_order, abs=0.1)
        assert freqs.BPFI == pytest.approx(inner_order, abs=0.1)

    def test_round_trips_a_synthetic_triple(self):
        # Pick a bearing, compute its orders, then verify the inversion
        # recovers the same BPFO/BPFI (not necessarily the same n_balls,
        # since only the ratio r and n=outer+inner are recoverable).
        bearing = BearingSpec(n_balls=9, ball_dia_mm=9.53, pitch_dia_mm=46.0, contact_angle_deg=0.0)
        freqs = bearing_frequencies(bearing, shaft_hz=1.0)
        derived = equivalent_bearing_from_frequencies(freqs.BPFO, freqs.BPFI)
        derived_freqs = bearing_frequencies(derived, shaft_hz=1.0)
        assert derived_freqs.BPFO == pytest.approx(freqs.BPFO, abs=1e-6)
        assert derived_freqs.BPFI == pytest.approx(freqs.BPFI, abs=1e-6)

    def test_implausible_n_balls_raises(self):
        with pytest.raises(ValueError, match="implausibly low"):
            equivalent_bearing_from_frequencies(1.0, 1.5)


class TestAssertShaftRate:
    def test_within_tolerance_passes(self):
        assert_shaft_rate(25.02, expected_hz=25.0, tolerance_pct=1.0, source_name="test.mat")

    def test_pre_2013_bug_value_raises(self):
        with pytest.raises(ValueError, match="50Hz-vs-25Hz"):
            assert_shaft_rate(50.0, expected_hz=25.0, tolerance_pct=1.0, source_name="test.mat")


class TestSyntheticFixtureEndToEnd:
    """A synthetic rig-shaped .mat, written with scipy.io.savemat, proves
    the full parse -> envelope -> RCA chain without needing the real
    dataset present."""

    def test_seeded_bpfo_recovers_bearing_outer_race(self, tmp_path, mfpt_cfg, bearings_cfg):
        fs = 12000.0
        signal = _bpfo_modulated_waveform(fs=fs, duration=1.0, carrier_hz=3000.0, mod_hz=81.12)
        path = tmp_path / "outer_270_1.mat"
        _write_rig_fixture(path, gs=signal, sr=fs, rate=25.0)

        case = to_case(path, mfpt_cfg=mfpt_cfg, bearings_cfg=bearings_cfg)
        assert case.source == "mfpt"
        assert case.validation_scope == ["rca"]
        assert case.expected.faults == ["bearing_outer_race"]

        iso_table = load_config("iso_zones")["zones"]
        thresholds = load_thresholds("route")
        result = run_analysis(case, iso_table=iso_table, thresholds=thresholds)
        primary = [f for f in result.findings if f.fault.startswith("bearing_")]
        assert primary and primary[0].fault == "bearing_outer_race"

    def test_unknown_filename_raises(self, tmp_path, mfpt_cfg, bearings_cfg):
        fs = 12000.0
        signal = _bpfo_modulated_waveform(fs=fs, duration=0.5, carrier_hz=3000.0, mod_hz=81.12)
        path = tmp_path / "not_a_real_mfpt_file.mat"
        _write_rig_fixture(path, gs=signal, sr=fs, rate=25.0)
        with pytest.raises(ValueError, match="not a known MFPT filename"):
            to_case(path, mfpt_cfg=mfpt_cfg, bearings_cfg=bearings_cfg)

    def test_bad_shaft_rate_raises_for_rig_file(self, tmp_path, mfpt_cfg, bearings_cfg):
        fs = 12000.0
        signal = _bpfo_modulated_waveform(fs=fs, duration=0.5, carrier_hz=3000.0, mod_hz=81.12)
        path = tmp_path / "baseline_1.mat"
        _write_rig_fixture(path, gs=signal, sr=fs, rate=50.0)  # the pre-2013-fix bug value
        with pytest.raises(ValueError, match="50Hz-vs-25Hz"):
            to_case(path, mfpt_cfg=mfpt_cfg, bearings_cfg=bearings_cfg)

    def test_real_world_style_fixture_with_embedded_orders(self, tmp_path, mfpt_cfg, bearings_cfg):
        fs = 6000.0
        signal = _bpfo_modulated_waveform(fs=fs, duration=1.0, carrier_hz=1500.0, mod_hz=6.0)
        path = tmp_path / "real_world_planet_bearing.mat"
        savemat(
            str(path),
            {
                "bearing": {
                    "sr": fs,
                    "gs": signal.reshape(-1, 1),
                    "load": np.array([]),
                    "rate": 1.0,
                    "ball": 3.0,
                    "cage": 0.4,
                    "outer": 6.0,
                    "inner": 8.0,
                }
            },
        )
        case = to_case(path, mfpt_cfg=mfpt_cfg, bearings_cfg=bearings_cfg)
        assert case.expected.fault_type == "real_world"
        assert case.expected.faults == []  # never a fabricated answer key
        assert case.machine.bearing.n_balls == 14  # round(6.0 + 8.0)

    def test_real_world_file_missing_orders_raises(self, tmp_path, mfpt_cfg, bearings_cfg):
        fs = 6000.0
        signal = _bpfo_modulated_waveform(fs=fs, duration=0.5, carrier_hz=1500.0, mod_hz=6.0)
        path = tmp_path / "real_world_oil_pump_bearing.mat"
        savemat(str(path), {"bearing": {"sr": fs, "gs": signal.reshape(-1, 1), "load": "", "rate": 1.0}})
        with pytest.raises(ValueError, match="missing embedded"):
            to_case(path, mfpt_cfg=mfpt_cfg, bearings_cfg=bearings_cfg)


def _has_file(name: str) -> bool:
    return (_DATA_DIR / f"{name}.mat").exists()


class TestEndToEndRealData:
    """Skipped cleanly when the MFPT .mat files aren't present locally
    (data-gated design — see module docstring)."""

    @pytest.mark.skipif(not _has_file("outer_270_1"), reason="data/mfpt/outer_270_1.mat not present")
    def test_outer_race_file_diagnoses_bearing_outer_race(self, mfpt_cfg, bearings_cfg):
        case = to_case(_DATA_DIR / "outer_270_1.mat", mfpt_cfg=mfpt_cfg, bearings_cfg=bearings_cfg)
        iso_table = load_config("iso_zones")["zones"]
        thresholds = load_thresholds("route")
        result = run_analysis(case, iso_table=iso_table, thresholds=thresholds)
        primary = [f for f in result.findings if f.fault.startswith("bearing_")]
        assert primary and primary[0].fault == "bearing_outer_race"

    @pytest.mark.skipif(not _has_file("inner_vload_7"), reason="data/mfpt/inner_vload_7.mat not present")
    def test_inner_race_file_diagnoses_bearing_inner_race(self, mfpt_cfg, bearings_cfg):
        case = to_case(_DATA_DIR / "inner_vload_7.mat", mfpt_cfg=mfpt_cfg, bearings_cfg=bearings_cfg)
        iso_table = load_config("iso_zones")["zones"]
        thresholds = load_thresholds("route")
        result = run_analysis(case, iso_table=iso_table, thresholds=thresholds)
        primary = [f for f in result.findings if f.fault.startswith("bearing_")]
        assert primary and primary[0].fault == "bearing_inner_race"

    @pytest.mark.skipif(not _has_file("baseline_1"), reason="data/mfpt/baseline_1.mat not present")
    def test_load_mfpt_mat_returns_signal_fs_rate(self, mfpt_cfg):
        data = load_mfpt_mat(_DATA_DIR / "baseline_1.mat")
        assert isinstance(data["gs"], np.ndarray)
        assert len(data["gs"]) > 0
        assert data["sr"] == 97656.0
        assert data["rate"] == pytest.approx(25.0, abs=0.01)

    @pytest.mark.skipif(
        not all(_has_file(n) for n in ("baseline_1", "baseline_2", "baseline_3")),
        reason="data/mfpt baseline files not present",
    )
    def test_all_rig_files_report_shaft_rate_near_25hz(self, mfpt_cfg):
        # The Feb-2013 dataset-fix guard: every rig file in the currently
        # fetched copy must carry the corrected 25Hz value.
        for name in mfpt_cfg["rig_files"]:
            if name.startswith("_") or not _has_file(name):
                continue
            data = load_mfpt_mat(_DATA_DIR / f"{name}.mat")
            assert data["rate"] == pytest.approx(25.0, abs=0.25), f"{name}: rate={data['rate']}"

    @pytest.mark.skipif(
        not all(_has_file(n) for n in ("real_world_intermediate_speed_bearing", "real_world_oil_pump_bearing", "real_world_planet_bearing")),
        reason="data/mfpt real-world files not present",
    )
    def test_real_world_files_never_get_a_fabricated_answer_key(self, mfpt_cfg, bearings_cfg):
        for name in (
            "real_world_intermediate_speed_bearing",
            "real_world_oil_pump_bearing",
            "real_world_planet_bearing",
        ):
            case = to_case(_DATA_DIR / f"{name}.mat", mfpt_cfg=mfpt_cfg, bearings_cfg=bearings_cfg)
            assert case.expected.faults == []
            assert case.expected.fault_type == "real_world"

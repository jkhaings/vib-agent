"""CWRU adapter tests (Phase 3.5).

Multiplier sanity and envelope-recovery tests always run (no external data
needed). The end-to-end tests are skipped when data/cwru/*.mat is absent —
this is the data-gated design: the phase's code and tests are complete and
green without the dataset; the actual benchmark run happens once the user
places the .mat files locally.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from vib_agent.config import load_config, load_thresholds
from vib_agent.models import BearingSpec
from vib_agent.pdm_core.bearing_rca import bearing_frequencies
from vib_agent.adapters.cwru import (
    envelope_spectrum,
    filename_to_expected,
    load_cwru_mat,
    raw_spectrum,
    to_case,
)
from vib_agent.pipeline import run_analysis

_DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "cwru"

# Published CWRU orders (multiples of shaft rate) for the SKF 6205-2RS JEM
# drive-end bearing. Source: CWRU Bearing Data Center documentation.
_PUBLISHED_ORDERS = {"BPFO": 3.585, "BPFI": 5.415, "BSF": 2.357, "FTF": 0.399}


@pytest.fixture
def cwru_cfg() -> dict:
    return load_config("cwru")


@pytest.fixture
def bearing_spec() -> BearingSpec:
    raw = load_config("bearings")["bearings"]["SKF_6205"]
    return BearingSpec(**{k: v for k, v in raw.items() if not k.startswith("_")})


class TestMultiplierSanity:
    """Independent check of the ported bearing_frequencies() formulas: at
    shaft_hz=1.0, the returned Hz values ARE the orders."""

    def test_skf_6205_orders_within_one_percent_of_published(self, bearing_spec):
        freqs = bearing_frequencies(bearing_spec, shaft_hz=1.0)
        computed = {"BPFO": freqs.BPFO, "BPFI": freqs.BPFI, "BSF": freqs.BSF, "FTF": freqs.FTF}
        for key, published in _PUBLISHED_ORDERS.items():
            delta_pct = abs(computed[key] - published) / published * 100.0
            assert delta_pct < 1.0, f"{key}: computed {computed[key]:.4f} vs published {published} ({delta_pct:.2f}% off)"


class TestEnvelopeSpectrum:
    def test_recovers_am_modulation_frequency(self):
        fs = 12000.0
        duration = 2.0  # 0.5 Hz resolution
        t = np.arange(0, duration, 1.0 / fs)
        carrier_freq = 3000.0  # inside the default 1500-5500 Hz band
        mod_freq = 107.0  # ~ CWRU's published BPFO at 1797 rpm

        carrier = np.sin(2 * np.pi * carrier_freq * t)
        modulation = 1.0 + 0.8 * np.sin(2 * np.pi * mod_freq * t)
        rng = np.random.default_rng(0)
        signal = modulation * carrier + 0.01 * rng.standard_normal(len(t))

        spectrum = envelope_spectrum(signal, fs)
        freq = np.array(spectrum.freq_hz)
        amp = np.array(spectrum.amplitude)

        peak_idx = np.argmax(amp[1:]) + 1  # skip DC bin
        peak_freq = freq[peak_idx]
        assert abs(peak_freq - mod_freq) < 2.0

    def test_returns_only_requested_region(self):
        fs = 12000.0
        t = np.arange(0, 1.0, 1.0 / fs)
        signal = np.sin(2 * np.pi * 3000.0 * t)
        spectrum = envelope_spectrum(signal, fs, region_hz=(0.0, 300.0))
        assert max(spectrum.freq_hz) <= 300.0


class TestRawSpectrum:
    def test_returns_nonempty_spectrum(self):
        fs = 12000.0
        t = np.arange(0, 1.0, 1.0 / fs)
        signal = np.sin(2 * np.pi * 100.0 * t)
        spectrum = raw_spectrum(signal, fs)
        assert len(spectrum.freq_hz) == len(spectrum.amplitude)
        assert len(spectrum.freq_hz) > 0


class TestFilenameParsing:
    def test_normal(self, cwru_cfg):
        info = filename_to_expected("Normal_0", cwru_cfg)
        assert info == {"fault_type": "normal", "diameter_in": None, "rpm": 1797.0, "primary_fault": None}

    def test_outer_race_with_position(self, cwru_cfg):
        info = filename_to_expected("OR007@6_0", cwru_cfg)
        assert info["fault_type"] == "outer"
        assert info["diameter_in"] == pytest.approx(0.007)
        assert info["primary_fault"] == "bearing_outer_race"
        assert info["rpm"] == 1797.0

    def test_inner_race(self, cwru_cfg):
        info = filename_to_expected("IR014_0", cwru_cfg)
        assert info["fault_type"] == "inner"
        assert info["diameter_in"] == pytest.approx(0.014)
        assert info["primary_fault"] == "bearing_inner_race"

    def test_ball(self, cwru_cfg):
        info = filename_to_expected("B021_0", cwru_cfg)
        assert info["fault_type"] == "ball"
        assert info["diameter_in"] == pytest.approx(0.021)
        assert info["primary_fault"] == "bearing_ball_spin"

    def test_load_suffix_maps_to_rpm(self, cwru_cfg):
        assert filename_to_expected("Normal_3", cwru_cfg)["rpm"] == 1730.0

    def test_invalid_filename_raises(self, cwru_cfg):
        with pytest.raises(ValueError):
            filename_to_expected("not_a_cwru_file", cwru_cfg)


def _has_file(name: str) -> bool:
    return (_DATA_DIR / f"{name}.mat").exists()


class TestEndToEnd:
    """Skipped cleanly when the CWRU .mat starter files aren't present
    locally (data-gated design — see module docstring)."""

    @pytest.mark.skipif(not _has_file("OR007@6_0"), reason="data/cwru/OR007@6_0.mat not present")
    def test_outer_race_file_diagnoses_bearing_outer_race(self, cwru_cfg, bearing_spec):
        case = to_case(_DATA_DIR / "OR007@6_0.mat", cwru_cfg=cwru_cfg, bearing_spec=bearing_spec)
        assert case.validation_scope == ["rca"]
        iso_table = load_config("iso_zones")["zones"]
        thresholds = load_thresholds("route")
        result = run_analysis(case, iso_table=iso_table, thresholds=thresholds)
        primary = [f for f in result.findings if f.fault.startswith("bearing_")]
        assert primary
        assert primary[0].fault == "bearing_outer_race"

    @pytest.mark.skipif(not _has_file("IR007_0"), reason="data/cwru/IR007_0.mat not present")
    def test_inner_race_file_diagnoses_bearing_inner_race(self, cwru_cfg, bearing_spec):
        case = to_case(_DATA_DIR / "IR007_0.mat", cwru_cfg=cwru_cfg, bearing_spec=bearing_spec)
        iso_table = load_config("iso_zones")["zones"]
        thresholds = load_thresholds("route")
        result = run_analysis(case, iso_table=iso_table, thresholds=thresholds)
        primary = [f for f in result.findings if f.fault.startswith("bearing_")]
        assert primary
        assert primary[0].fault == "bearing_inner_race"

    @pytest.mark.skipif(not _has_file("Normal_0"), reason="data/cwru/Normal_0.mat not present")
    def test_normal_file_has_no_bearing_finding(self, cwru_cfg, bearing_spec):
        case = to_case(_DATA_DIR / "Normal_0.mat", cwru_cfg=cwru_cfg, bearing_spec=bearing_spec)
        iso_table = load_config("iso_zones")["zones"]
        thresholds = load_thresholds("route")
        result = run_analysis(case, iso_table=iso_table, thresholds=thresholds)
        bearing_findings = [f for f in result.findings if f.fault.startswith("bearing_")]
        assert bearing_findings == []

    @pytest.mark.skipif(not _has_file("Normal_0"), reason="data/cwru/Normal_0.mat not present")
    def test_load_cwru_mat_returns_signal_fs_rpm(self, cwru_cfg):
        signal, fs, rpm = load_cwru_mat(_DATA_DIR / "Normal_0.mat", cwru_cfg)
        assert isinstance(signal, np.ndarray)
        assert len(signal) > 0
        assert fs == 12000.0
        # Normal_0.mat carries an explicit *RPM key (actual measured speed,
        # 1796) which load_cwru_mat correctly prefers over the nominal
        # filename-inferred value (1797) — real data, not a round number.
        assert rpm == 1796.0

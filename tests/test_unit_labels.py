"""Session R3-DIFF (item 4) — the report says "g" only where the number is g.

`SensorData.*_rms_ACC_G` is named for the NCD sensor's own field, and the
spectrum figure's channel header printed whatever landed in it as "N.NNN g".
For half the upload paths that is a false unit claim, and the adapters that
write those values say so in their own comments:

  * a CSV/XLSX velocity upload stores overall_rms_mms * 0.08 -- an acceleration
    PROXY existing only so the quality gate's machine-running check has a
    nonzero number ("it is not a real g reading", adapters/uploads/tabular.py).
    The measurement is millimetres per second and the row above says so.
  * an UNSCALED WAV stores the normalized-waveform RMS: arbitrary units by
    construction, which is exactly why severity and ISO zone are struck from its
    validation_scope ("it is not a real g reading", adapters/uploads/wav.py).

A scaled WAV and every acceleration benchmark genuinely are g and are unchanged.

Session CHARTS-2 (item 4) cut the channel header to one row — Type, Unit, Δf,
Fmax — so the "RMS" row these cases were written against is gone, and with it
`charts._rms_label`. **Every claim below survives unchanged**; it is now made
against the surface that replaced it. `spectrum_unit_label` carries the SAME
discriminators (its docstring says so, and R3-DIFF's own comment said it first),
and it feeds the header's Unit cell, the y-axis, the caption and both parameter
rows — so "a velocity upload is never labelled g" is asserted over MORE of the
report than it was, not less.

One claim could not survive as written: `test_the_unscaled_wav_number_itself_is_
unchanged` asserted the proxy NUMBER still appeared on the figure. No surface
prints it now. What is pinned instead is that the number is untouched on the
Case and that no figure text claims a unit for it — which is the honest end of
the same thread, since the row was already reading "not measured".
"""

from __future__ import annotations

import numpy as np
import pytest
from scipy.io import wavfile

import tests.report_v2_samples as SAMPLES
from vib_agent.adapters.uploads import parse_upload
from vib_agent.adapters.uploads.common import UploadForm
from vib_agent.config import load_config
from vib_agent.pipeline import run_analysis
from vib_agent.report.charts import _channel_header

FS = 12000.0


@pytest.fixture(scope="module")
def cfg() -> dict:
    return SAMPLES._config()


def _header(case, cfg) -> dict[str, str]:
    # `run_analysis` is no longer an input to the header, but it is still run:
    # a case that cannot be analysed is not a case this claim is about.
    run_analysis(case, iso_table=cfg["iso_table"], thresholds=cfg["thresholds"],
                 rules=cfg["rules"])
    axis = sorted((case.spectra or {}).keys())[0]
    return dict(_channel_header(case.spectra[axis], case))


def _no_g_anywhere(header: dict[str, str]) -> bool:
    """No cell of the header claims grams. Stronger than the old assertion,
    which only looked at the one row that has since been removed."""
    return all("g" not in value.split() for value in header.values())


def _wav(tmp_path, cfg, *, sensitivity):
    n = int(FS)
    t = np.arange(n) / FS
    signal = np.sin(2 * np.pi * 3000 * t) * (1 + 0.5 * np.sin(2 * np.pi * 107.16 * t))
    signal += 0.01 * np.random.default_rng(1).standard_normal(n)
    path = tmp_path / "upload.wav"
    wavfile.write(str(path), int(FS), ((signal / np.max(np.abs(signal))) * 0.8 * 32767).astype(np.int16))
    form = UploadForm(machine_alias="W", rpm=1800.0, iso_group="2", iso_support="rigid", machine_type="motor",
                      bearing_model="6206", wav_sensitivity=sensitivity)
    case, _kind, _note = parse_upload(path, form, bearings_cfg=cfg["bearings"])
    return case


class TestAFalseGIsNotPrinted:
    def test_a_velocity_upload_does_not_claim_g(self, cfg):
        """The kit trio is a mm/s velocity spectrum. Its RMS row used to read
        "0.338 g" -- a number that is 0.08 x the millimetres-per-second reading
        printed directly above it."""
        header = _header(SAMPLES.multiaxis_trio(cfg), cfg)
        assert header["Unit"] == "mm/s RMS"
        assert _no_g_anywhere(header)

    def test_an_unscaled_wav_does_not_claim_g(self, tmp_path, cfg):
        """Arbitrary units by construction, so the header states NO unit at all
        -- the cell is omitted rather than filled with a non-claim (CHARTS-2
        item 4 / E2 item 3). The caption still carries "(as supplied)"."""
        header = _header(_wav(tmp_path, cfg, sensitivity=None), cfg)
        assert "Unit" not in header
        assert _no_g_anywhere(header)

    def test_the_unscaled_wav_number_itself_is_unchanged(self, tmp_path, cfg):
        """The proxy value is untouched on the Case; what changed is that no
        figure surface prints it, and none claims a unit for it."""
        case = _wav(tmp_path, cfg, sensitivity=None)
        assert case.sensor_data.y_rms_ACC_G is not None
        header = _header(case, cfg)
        assert f"{case.sensor_data.y_rms_ACC_G:.3f}" not in " ".join(header.values())
        assert _no_g_anywhere(header)


class TestTheProxyRelationshipIsWhyThisIsCorrect:
    """Not an opinion about labelling — the number really is the velocity, scaled.
    If a future change makes these genuinely independent, this test fails first."""

    def test_the_synthetic_generator_writes_the_documented_proxy(self, cfg):
        from vib_agent.adapters.uploads.tabular import _ACCEL_PROXY_SCALE

        case = SAMPLES.demo_bearing_case(cfg)
        sd = case.sensor_data
        for axis in ("x", "y", "z"):
            rms = getattr(sd, f"{axis}_rms_ACC_G")
            velocity = getattr(sd, f"{axis}_velocity_mm_sec")
            if rms is None or not velocity:
                continue
            assert rms / velocity == pytest.approx(_ACCEL_PROXY_SCALE), (
                f"{axis}: rms_ACC_G is no longer velocity x {_ACCEL_PROXY_SCALE}"
            )

    def test_a_real_ncd_packet_never_reaches_this_header(self):
        """The one case where *_rms_ACC_G IS a real accelerometer reading
        ALONGSIDE a real velocity is the NCD sensor. The rule above would
        mislabel it — it cannot, because the channel header is rendered per
        SPECTRUM and an NCD packet carries none. Pinned so that a future NCD
        path that grows spectra fails here rather than shipping a wrong unit."""
        from tests.fixtures import REFERENCE_CASES
        from vib_agent.models import SensorData

        sd = SensorData(**REFERENCE_CASES["T01_healthy_zone_a"]["sensor_data"])
        assert sd.x_rms_ACC_G and sd.x_velocity_mm_sec  # both real, both present
        assert sd.x_rms_ACC_G / sd.x_velocity_mm_sec != pytest.approx(0.08)  # not the proxy
        from vib_agent.pdm_core.bearing_rca import peaks_from_ncd

        assert peaks_from_ncd(sd).axis_mean_amp is None  # no spectrum -> no figure -> no header


class TestARealGStillSaysG:
    def test_a_scaled_wav_is_acceleration(self, tmp_path, cfg):
        """A sensitivity WAS supplied, so the amplitude really is in g."""
        header = _header(_wav(tmp_path, cfg, sensitivity=0.05), cfg)
        assert header["Unit"] == "g"

    @pytest.mark.parametrize("source", ["mafaulda", "cwru", "mfpt", "wind_turbine"])
    def test_every_acceleration_benchmark_keeps_its_unit(self, source, cfg, tmp_path):
        """These datasets ARE accelerometer recordings; relabelling them would
        trade one false claim for another."""
        from vib_agent.models import Case, MachineMeta, SensorData, Spectrum

        machine = MachineMeta(mac="X", name="X", machine_class="medium", mounting="rigid",
                              axial_axis="x", coupled=True, rpm_nominal=1800.0, active=True,
                              iso_group="2", iso_support="rigid")
        spectrum = Spectrum(freq_hz=[0.0, 1.0, 2.0], amplitude=[0.1, 0.2, 0.1],
                            fmax_hz=2.0, kind="envelope")
        case = Case(name="b", machine=machine,
                    sensor_data=SensorData(rpm=1800.0, y_rms_ACC_G=1.034),
                    spectra={"y": spectrum}, source=source, validation_scope=["rca"])
        assert _header(case, cfg)["Unit"] == "g"


class TestTheRuleIsDrivenByTheCaseNotAGuess:
    """R3-DIFF's two ordering pins, against the resolver that carries the rule
    now. `spectrum_unit_label` reads the same discriminators `_rms_label` did."""

    def test_no_case_means_no_unit_invention(self, cfg):
        """With no Case to read provenance from, nothing may assert a real
        acceleration reading it cannot substantiate."""
        from vib_agent.models import Spectrum
        from vib_agent.report.charts import (
            SPECTRUM_UNIT_AS_SUPPLIED, _channel_header, spectrum_unit_label,
        )

        spectrum = Spectrum(freq_hz=[0.0, 1.0, 2.0], amplitude=[0.1, 0.2, 0.1],
                            fmax_hz=2.0, kind="envelope")
        assert spectrum_unit_label(None, spectrum) == SPECTRUM_UNIT_AS_SUPPLIED
        # ...and the header states no unit at all rather than a non-claim.
        assert "Unit" not in dict(_channel_header(spectrum, None))

    def test_a_velocity_upload_is_mms_not_g_despite_a_full_scope(self, cfg):
        """A velocity upload has full validation_scope, so the "severity is in
        scope -> g" branch must not be reached before the velocity branch --
        ordering, pinned. This is the exact shape that produced "0.338 g"."""
        from vib_agent.models import Case, MachineMeta, SensorData, Spectrum
        from vib_agent.report.charts import SPECTRUM_UNIT_VELOCITY, spectrum_unit_label

        machine = MachineMeta(mac="X", name="X", machine_class="medium", mounting="rigid",
                              axial_axis="x", coupled=True, rpm_nominal=1800.0, active=True,
                              iso_group="2", iso_support="rigid")
        case = Case(name="v", machine=machine, sensor_data=SensorData(rpm=1800.0),
                    source="upload", validation_scope=["zone", "severity", "rca", "trend"])
        velocity = Spectrum(freq_hz=[0.0, 1.0, 2.0], amplitude=[0.1, 0.2, 0.1],
                            fmax_hz=2.0, kind="velocity")
        assert spectrum_unit_label(case, velocity) == SPECTRUM_UNIT_VELOCITY

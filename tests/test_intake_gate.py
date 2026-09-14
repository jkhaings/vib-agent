"""INTAKE-HARDEN, fix 3 — two gate rules that catch a CLASS, not an instance.

The gate already refused a misread frequency axis in one direction. It did not
refuse it in the other, and it had no opinion at all about the SCALE of what it
was reading:

  * `_speed_presence` fails an axis that has been COMPRESSED (Hz read as CPM
    leaves nothing at 30 Hz). The same 60x error with the sign flipped — a CPM
    axis read as Hz — got a warning, because the inflated axis still spans the
    shaft line and the check widens its own tolerance to 1.5x the bin step, so
    the coarser the misread the wider the window it accepts. Analysis then
    proceeded on a 60x-wrong axis and found no bearing fault on a faulted
    machine: a clean bill of health, computed confidently.

  * `max_amplitude` was 1e6, three orders of magnitude above anything physical,
    so an inflated amplitude column was never questioned. A dB-scaled spectrum
    declared as mm/s gives an overall of 940 mm/s and the gate said nothing.

Both new rules are stated as questions about the interpretation, not about the
machine, which is what keeps them out of config/thresholds.json.
"""

from __future__ import annotations

import pytest

from tests.intake_adversarial_corpus import BY_NAME, FIXTURE_DIR, RPM
from vib_agent.adapters.uploads.common import UploadForm
from vib_agent.adapters.uploads.recipe import execute_recipe
from vib_agent.adapters.uploads.verify import failed, verify_case
from vib_agent.config import load_config
from vib_agent.models import Case, HistoryPoint, MachineMeta, SensorData, Spectrum


@pytest.fixture(scope="module")
def bearings_cfg():
    return load_config("bearings")


@pytest.fixture(scope="module")
def tolerances():
    return load_config("webapp")["inference"]


def _form():
    return UploadForm(machine_alias="Gate", rpm=RPM, iso_group="2", iso_support="rigid", machine_type="motor",
                      bearing_model="6206")


def _run(name, recipe, bearings_cfg):
    return execute_recipe(FIXTURE_DIR / name, recipe, _form(), bearings_cfg=bearings_cfg)


def _names(case, tolerances):
    return [c.name for c in failed(verify_case(case, rpm=RPM, tolerances=tolerances))]


def _velocity_case(overall: float, *, bin_step: float = 0.5) -> Case:
    freqs = [round(i * bin_step, 6) for i in range(1, 200)]
    amps = [0.01] * 199
    return Case(
        name="M",
        machine=MachineMeta(mac="M", name="M", active=True, type="motor"),
        sensor_data=SensorData(rpm=RPM, y_velocity_mm_sec=overall),
        spectra={"y": Spectrum(freq_hz=freqs, amplitude=amps, fmax_hz=max(freqs),
                               kind="velocity")},
        source="upload",
    )


# ══════════════════════════════════════════════════════════════════════════
# Rule 1 · the axis must be able to resolve running speed
# ══════════════════════════════════════════════════════════════════════════
class TestAxisResolution:
    def test_a_cpm_axis_read_as_hz_is_now_refused(self, bearings_cfg, tolerances):
        """The inflation half of the unit-error class. Before this rule the gate
        returned a warning and the analysis proceeded on a 60x-wrong axis."""
        wrong = BY_NAME["decimal_comma_cpm.txt"].recipe.model_copy(update={"x_unit": "hz"})
        case, _, _ = _run("decimal_comma_cpm.txt", wrong, bearings_cfg)
        assert "inferred_axis_resolution" in _names(case, tolerances)

    def test_both_directions_of_the_same_error_now_fail(self, bearings_cfg, tolerances):
        """Compression was already caught; inflation is the new half. A unit
        error should not depend on which way round it was made."""
        compressed = BY_NAME["crlf.txt"].recipe.model_copy(update={"x_unit": "cpm"})
        case, _, _ = _run("crlf.txt", compressed, bearings_cfg)
        assert _names(case, tolerances), "the compressing misread stopped failing"

        inflated = BY_NAME["orders_no_speed.txt"].recipe.model_copy(update={"x_unit": "hz"})
        case, _, _ = _run("orders_no_speed.txt", inflated, bearings_cfg)
        assert _names(case, tolerances)

    def test_the_reason_tells_the_analyst_what_to_check(self, bearings_cfg, tolerances):
        wrong = BY_NAME["decimal_comma_cpm.txt"].recipe.model_copy(update={"x_unit": "hz"})
        case, _, _ = _run("decimal_comma_cpm.txt", wrong, bearings_cfg)
        reason = next(c.reason for c in verify_case(case, rpm=RPM, tolerances=tolerances)
                      if c.name == "inferred_axis_resolution" and c.status == "fail")
        assert "CPM" in reason and "running speed" in reason

    @pytest.mark.parametrize("bins_per_order,should_fail", [
        (60.0, False),   # a route spectrum
        (6.0, False),    # the coarsest correctly-read file in any corpus (a waveform FFT)
        (2.0, False),    # exactly at the threshold
        (1.0, True),     # a CPM axis read as Hz
        (0.6, True),     # a decimal-convention error
    ])
    def test_the_threshold_sits_between_the_good_and_the_bad(self, bins_per_order, should_fail,
                                                             tolerances):
        shaft = RPM / 60.0
        case = _velocity_case(2.0, bin_step=shaft / bins_per_order)
        failures = _names(case, tolerances)
        assert ("inferred_axis_resolution" in failures) is should_fail

    def test_resolution_is_judged_before_speed_presence(self, bearings_cfg, tolerances):
        """A too-coarse axis makes the presence question meaningless, so the
        analyst gets ONE clear reason rather than two confusing ones."""
        wrong = BY_NAME["decimal_comma_cpm.txt"].recipe.model_copy(update={"x_unit": "hz"})
        case, _, _ = _run("decimal_comma_cpm.txt", wrong, bearings_cfg)
        names = [c.name for c in verify_case(case, rpm=RPM, tolerances=tolerances)]
        assert "inferred_speed_presence" not in names


# ══════════════════════════════════════════════════════════════════════════
# Rule 2 · a velocity claim must be physically possible
# ══════════════════════════════════════════════════════════════════════════
class TestVelocityPlausibility:
    def test_db_amplitudes_declared_as_velocity_are_refused(self, bearings_cfg, tolerances):
        """A dB spectrum's 41.6 dB noise floor reads as 41.6 mm/s. Every bin is
        individually plausible; only the overall gives it away."""
        wrong = BY_NAME["db_units.txt"].recipe.model_copy(update={"amplitude_unit": "mm_s"})
        case, _, _ = _run("db_units.txt", wrong, bearings_cfg)
        assert "inferred_velocity_plausibility" in _names(case, tolerances)

    def test_the_honest_dB_reading_still_passes(self, bearings_cfg, tolerances):
        """Declared `unknown`, the same file is analysable for fault frequencies
        and simply makes no severity claim. The rule must not touch it."""
        case, _, note = _run("db_units.txt", BY_NAME["db_units.txt"].recipe, bearings_cfg)
        assert _names(case, tolerances) == []
        assert case.sensor_data.y_velocity_mm_sec is None
        assert "severity" in note.lower()

    @pytest.mark.parametrize("overall,should_fail", [
        (2.6, False),      # a normal machine
        (11.0, False),     # the highest ISO 20816-3 zone boundary in this project
        (45.0, False),     # disintegrating, but real — must still get a diagnosis
        (150.0, False),    # exactly at the ceiling
        (940.0, True),     # a dB spectrum declared as mm/s
        (258535.0, True),  # a decimal-convention error
    ])
    def test_the_ceiling_sits_above_every_real_machine(self, overall, should_fail, tolerances):
        failures = _names(_velocity_case(overall), tolerances)
        assert ("inferred_velocity_plausibility" in failures) is should_fail

    def test_acceleration_and_displacement_are_never_judged_on_velocity(self, bearings_cfg,
                                                                        tolerances):
        """No velocity claim, no velocity check — an m/s2 reading has no overall
        to be implausible."""
        case, _, _ = _run("accel_ms2.asc", BY_NAME["accel_ms2.asc"].recipe, bearings_cfg)
        assert case.sensor_data.y_velocity_mm_sec is None
        status = {c.name: c.status for c in verify_case(case, rpm=RPM, tolerances=tolerances)}
        assert status["inferred_velocity_plausibility"] == "not_applicable"

    def test_a_trend_history_is_judged_too(self, tolerances):
        """A trend has no frequency axis, but it does carry a velocity claim."""
        import datetime as dt

        history = [HistoryPoint(ts=dt.datetime(2026, 3, day, tzinfo=dt.timezone.utc), value=900.0)
                   for day in range(1, 6)]
        case = Case(name="M",
                    machine=MachineMeta(mac="M", name="M", active=True, type="motor"),
                    sensor_data=SensorData(rpm=RPM, y_velocity_mm_sec=900.0),
                    history=history, source="upload")
        assert "inferred_velocity_plausibility" in _names(case, tolerances)


# ══════════════════════════════════════════════════════════════════════════
# Nothing that was readable stopped being readable
# ══════════════════════════════════════════════════════════════════════════
class TestNoKnownGoodFileRegressed:
    def test_the_whole_session_g_corpus_still_passes(self, bearings_cfg, tolerances):
        """These two rules are the only ones in this gate with a tunable that
        could fail-close on real data, so the entire prior corpus is re-run."""
        import tempfile
        from pathlib import Path

        from tests.inference_corpus import ALL_CORPUS
        from tests.inference_corpus import RPM as G_RPM
        from tests.inference_corpus import write_all

        with tempfile.TemporaryDirectory() as tmp:
            write_all(Path(tmp))
            for item in ALL_CORPUS:
                case, _, _ = execute_recipe(
                    Path(tmp) / item.name, item.recipe,
                    UploadForm(machine_alias="G", rpm=G_RPM, iso_group="2",
                               iso_support="rigid", machine_type="motor", bearing_model="6206"),
                    bearings_cfg=bearings_cfg)
                assert failed(verify_case(case, rpm=G_RPM, tolerances=tolerances)) == [], item.name

    def test_the_adversarial_corpus_verdicts_are_unchanged(self, bearings_cfg, tolerances):
        from tests.intake_adversarial_corpus import CORPUS

        for item in CORPUS:
            if item.outcome != "case":
                continue
            case, _, _ = _run(item.name, item.recipe, bearings_cfg)
            failures = _names(case, tolerances)
            assert bool(failures) is (item.gate == "fail"), f"{item.name}: {failures}"

    def test_both_tolerances_come_from_config_not_code(self):
        inference_cfg = load_config("webapp")["inference"]
        for key in ("min_bins_per_order", "max_overall_velocity_mm_s"):
            assert key in inference_cfg, f"{key} must be tunable in config/webapp.json"
        # ...and each carries the evidence its value was chosen from, since both
        # are thresholds an operator may want to move.
        for comment in ("_min_bins_per_order_comment", "_max_overall_velocity_comment"):
            assert inference_cfg.get(comment), f"{comment} must justify the value"

    def test_the_defaults_in_code_match_the_config(self):
        """A caller that passes a partial tolerance dict (tests/test_recipe.py
        does) must get the same verdicts as the product does."""
        inference_cfg = load_config("webapp")["inference"]
        case = _velocity_case(940.0, bin_step=(RPM / 60.0))
        assert _names(case, {}) == _names(case, inference_cfg)

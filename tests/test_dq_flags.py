"""Session DQ-FLAGS — the data-quality pathologies that gate or caveat findings.

Roadmap §S4. Each check answers one question about the MEASUREMENT, never about
the machine: a bad measurement is flagged and gated, never diagnosed. Every one
of them is WARN-only and appends no `train_reasons`, matching the existing
spectrum-check precedent — a data-quality artifact qualifies a reading, it does
not collapse it.

Two findings drove the shape of this file and are pinned here as behaviour:

1. `clipping` became `spectral_plateau`, because it never detected clipping.
   Hard-clipping a real MAFAULDA record to 94.8% rail occupancy (crest factor
   3.41 -> 1.02) leaves the statistic at 1 — a clean `pass`. Saturation raises
   the broadband floor and adds harmonics; it does not flat-top a SPECTRUM.
   `test_saturation_is_not_what_this_check_sees` pins that honestly, so nobody
   re-reads the check as clipping detection later.

2. Every spectrum check returns `not_applicable` — never `pass` — when its
   inputs cannot support the question, following the file's own precedent at
   `quality_gate.py:278-287` ("not 'pass': passing would imply a non-elevated
   zone was found").
"""

from __future__ import annotations

import math

from vib_agent.models import Check, MachineMeta, SensorData, Spectrum
from vib_agent.pdm_core.quality_gate import run_quality_gate
from vib_agent.pdm_core.iso_classify import classify, resolve_thresholds


# ── builders ────────────────────────────────────────────────────────────────

def _spectrum(pairs, *, kind="raw_acceleration", fmax=500.0) -> Spectrum:
    return Spectrum(
        freq_hz=[f for f, _ in pairs],
        amplitude=[a for _, a in pairs],
        fmax_hz=fmax,
        kind=kind,
    )


def _grid(n=2000, df=0.25, base=0.001) -> list[tuple[float, float]]:
    """A clean, uniform spectrum with a flat low-level floor."""
    return [(i * df, base) for i in range(n)]


def _with_peak(pairs, freq, amp, *, width=1):
    out = []
    for f, a in pairs:
        out.append((f, amp if abs(f - freq) <= width * (pairs[1][0] - pairs[0][0]) else a))
    return out


def _machine(**over) -> MachineMeta:
    base = dict(
        mac="DQ-TEST-01", name="DQ Test Machine", active=True, type="pump",
        iso_group="2", iso_support="rigid", machine_type="motor",
    )
    base.update(over)
    return MachineMeta(**base)


def _sensor(rpm=1800.0, **over) -> SensorData:
    base = dict(
        rpm=rpm,
        x_velocity_mm_sec=0.3, y_velocity_mm_sec=0.5, z_velocity_mm_sec=0.4,
        x_rms_ACC_G=0.05, y_rms_ACC_G=0.10, z_rms_ACC_G=0.08,
    )
    base.update(over)
    return SensorData(**base)


def _gate(spectrum, *, sensor=None, machine=None, iso_table=None, thresholds=None):
    machine = machine or _machine()
    sensor = sensor if sensor is not None else _sensor()
    resolved = resolve_thresholds(machine, iso_table)
    reading = classify(sensor, machine, resolved)
    return run_quality_gate(
        reading, sensor, machine, thresholds["quality_gate"], spectrum=spectrum
    )


def _check(result, name) -> Check:
    return next(c for c in result.checks if c.name == name)


# ── the rename, and the truth it tells ──────────────────────────────────────

class TestSpectralPlateau:
    """`clipping` -> `spectral_plateau`: the check now names what it measures."""

    def test_a_plateau_of_identical_bins_warns(self, iso_table, thresholds):
        pairs = _grid()
        for i in range(200, 208):  # 8 identical bins at the spectrum max
            pairs[i] = (pairs[i][0], 0.5)
        result = _gate(_spectrum(pairs), iso_table=iso_table, thresholds=thresholds)
        check = _check(result, "spectral_plateau")
        assert check.status == "warn"
        assert "8 bins share the identical amplitude" in check.reason
        assert "50.0-51.8 Hz" in check.reason

    def test_the_reason_no_longer_asserts_sensor_saturation(self, iso_table, thresholds):
        """The old reason said 'possible sensor clipping' and the old follow-up
        told the analyst to change the sensor range. Both were claims this
        evidence cannot support."""
        pairs = _grid()
        for i in range(200, 208):
            pairs[i] = (pairs[i][0], 0.5)
        check = _check(
            _gate(_spectrum(pairs), iso_table=iso_table, thresholds=thresholds),
            "spectral_plateau",
        )
        lowered = check.reason.lower()
        assert "clipping" not in lowered
        assert "saturat" not in lowered
        assert "gain" not in lowered and "input range" not in lowered

    def test_saturation_is_not_what_this_check_sees(self, iso_table, thresholds):
        """A hard-clipped WAVEFORM does not make a flat-topped SPECTRUM.

        Built here the way saturation actually presents — a raised broadband
        floor plus harmonics, with no repeated maximum — so the check passes.
        That is the honest answer, and it is why the check is not called
        `clipping` any more: the discriminator that does track saturation is the
        time-domain crest factor, which this gate cannot see.
        """
        pairs = [(i * 0.25, 0.02) for i in range(2000)]  # raised floor
        for k in range(1, 12):  # odd/even harmonics of a 30 Hz fundamental
            pairs = _with_peak(pairs, 30.0 * k, 0.4 / k)
        result = _gate(_spectrum(pairs), iso_table=iso_table, thresholds=thresholds)
        assert _check(result, "spectral_plateau").status == "pass"

    def test_not_applicable_without_a_spectrum(self, iso_table, thresholds):
        result = _gate(None, iso_table=iso_table, thresholds=thresholds)
        assert _check(result, "spectral_plateau").status == "not_applicable"

    def test_malformed_spectrum_is_not_applicable_not_a_crash(self, iso_table, thresholds):
        """freq_hz and amplitude disagreeing used to reach zip(strict=True) and
        raise ValueError out of the whole gate — on untrusted upload input."""
        bad = Spectrum(freq_hz=[0.0, 1.0, 2.0], amplitude=[0.1, 0.2], fmax_hz=2.0)
        check = _check(_gate(bad, iso_table=iso_table, thresholds=thresholds), "spectral_plateau")
        assert check.status == "not_applicable"
        assert "malformed" in check.reason


class TestSkiSlopeSeverity:
    """The existing trigger is untouched; a SEVERE class grades what it means."""

    def _slope(self, ratio_target_severe: bool):
        # Energy concentrated below 5 Hz. 21 bins at 0.25 Hz spacing sit in-band.
        pairs = _grid()
        level = 1.0 if ratio_target_severe else 0.02
        out = []
        for f, a in pairs:
            out.append((f, level if f <= 5.0 else a))
        return out

    def test_moderate_slope_keeps_the_original_wording(self, iso_table, thresholds):
        check = _check(
            _gate(_spectrum(self._slope(False)), iso_table=iso_table, thresholds=thresholds),
            "ski_slope",
        )
        assert check.status == "warn"
        assert "possible ski-slope artifact (loose mount/cabling)" in check.reason
        assert "SEVERE" not in check.reason

    def test_severe_slope_says_amplitudes_are_unusable(self, iso_table, thresholds):
        check = _check(
            _gate(_spectrum(self._slope(True)), iso_table=iso_table, thresholds=thresholds),
            "ski_slope",
        )
        assert check.status == "warn"
        assert "SEVERE" in check.reason
        assert "not usable" in check.reason

    def test_severe_is_withheld_on_a_slow_machine(self, iso_table, thresholds):
        """A 90 rpm machine's own 1x (1.5 Hz) lives INSIDE the 5 Hz band, so its
        energy is content, not artifact. The reading still warns — it is still a
        caveat — but it is never graded severe."""
        check = _check(
            _gate(
                _spectrum(self._slope(True)),
                sensor=_sensor(rpm=90.0),
                iso_table=iso_table,
                thresholds=thresholds,
            ),
            "ski_slope",
        )
        assert check.status == "warn"
        assert "SEVERE" not in check.reason

    def test_severity_never_changes_the_status(self, iso_table, thresholds):
        """Severe is nested inside the existing warn branch, so grading can only
        ever re-word a reading that already warned."""
        clean = _gate(_spectrum(_grid()), iso_table=iso_table, thresholds=thresholds)
        assert _check(clean, "ski_slope").status == "pass"

    def test_a_band_only_spectrum_is_not_applicable(self, iso_table, thresholds):
        """A 0-5 Hz zoom capture makes the ratio 1.0 by construction. That is a
        legitimate measurement, not a ski slope, and the check says so."""
        pairs = [(i * 0.01, 0.5) for i in range(400)]  # 0-4 Hz only
        check = _check(
            _gate(_spectrum(pairs, fmax=4.0), iso_table=iso_table, thresholds=thresholds),
            "ski_slope",
        )
        assert check.status == "not_applicable"
        assert "no out-of-band content" in check.reason

    def test_negative_frequencies_are_not_counted_as_low_frequency(self, iso_table, thresholds):
        """A two-sided export carries negative bins; they are not 'below 5 Hz'."""
        pairs = [(-f, 1.0) for f in range(200, 0, -1)] + _grid()
        result = _gate(_spectrum(pairs), iso_table=iso_table, thresholds=thresholds)
        assert _check(result, "ski_slope").status == "pass"


class TestSensorSettling:
    """Shape, not aggregate energy — the property that makes it distinct."""

    def _settling(self, n=2000, df=0.25, decay_bins=12):
        pairs = _grid(n=n, df=df)
        for i in range(decay_bins):
            pairs[i] = (i * df, 0.5 * math.exp(-i / 2.5))
        return pairs

    def test_a_steep_monotonic_decay_from_dc_warns(self, iso_table, thresholds):
        check = _check(
            _gate(_spectrum(self._settling()), iso_table=iso_table, thresholds=thresholds),
            "sensor_settling",
        )
        assert check.status == "warn"
        assert "falls monotonically over the first" in check.reason
        assert "still settling" in check.reason

    def test_a_flat_spectrum_is_not_a_settling_decay(self, iso_table, thresholds):
        """Equal bins are trivially non-increasing, so a dead sensor would read
        as a perfect decay without the depth and floor conditions."""
        check = _check(
            _gate(_spectrum(_grid()), iso_table=iso_table, thresholds=thresholds),
            "sensor_settling",
        )
        assert check.status == "pass"

    def test_a_discrete_low_frequency_tone_is_not_settling(self, iso_table, thresholds):
        """A loose-mount rattle puts humps in the low band; it does not decay
        monotonically. ski_slope is the check that speaks to that energy."""
        pairs = _grid()
        for idx, amp in ((2, 0.4), (8, 0.35), (14, 0.5)):
            pairs[idx] = (pairs[idx][0], amp)
        check = _check(
            _gate(_spectrum(pairs), iso_table=iso_table, thresholds=thresholds), "sensor_settling"
        )
        assert check.status == "pass"

    def test_not_applicable_when_the_bottom_of_the_spectrum_was_not_captured(
        self, iso_table, thresholds
    ):
        """A route collector starting at 10 Hz never measured the settling
        region, so 'maximum at the first bin' would only mean 'maximum at Fmin'."""
        pairs = [(10.0 + i * 2.5, 0.5 * math.exp(-i / 2.5)) for i in range(400)]
        check = _check(
            _gate(_spectrum(pairs), iso_table=iso_table, thresholds=thresholds), "sensor_settling"
        )
        assert check.status == "not_applicable"
        assert "not adjacent to DC" in check.reason

    def test_a_blanked_zero_tail_is_not_a_decay(self, iso_table, thresholds):
        """Vendor exports sometimes zero everything below Fmin. A run of zeros
        is not a settling transient."""
        pairs = _grid()
        for i in range(11):
            pairs[i] = (pairs[i][0], 0.0)
        check = _check(
            _gate(_spectrum(pairs), iso_table=iso_table, thresholds=thresholds), "sensor_settling"
        )
        assert check.status == "pass"


class TestAcCouplingRolloff:
    """Only warns when the missing content is content the analysis needs."""

    def test_a_normal_machine_does_not_warn(self, iso_table, thresholds):
        """The load-bearing case: accelerometers are AC-coupled BY DESIGN, so
        no sub-1 Hz content is correct, not defective. Every MAFAULDA file and
        every synthetic fixture must land here."""
        check = _check(
            _gate(_spectrum(_grid()), iso_table=iso_table, thresholds=thresholds),
            "ac_coupling_rolloff",
        )
        assert check.status == "pass"

    def test_a_very_slow_machine_warns_with_the_computed_attenuation(
        self, iso_table, thresholds
    ):
        pairs = [(i * 0.01, 0.01) for i in range(2000)]  # 0-20 Hz, fine bins
        check = _check(
            _gate(
                _spectrum(pairs), sensor=_sensor(rpm=18.0),
                iso_table=iso_table, thresholds=thresholds,
            ),
            "ac_coupling_rolloff",
        )
        assert check.status == "warn"
        assert "0.30 Hz" in check.reason
        # single-pole |H| at 0.3 Hz with a 0.5 Hz corner = 0.51
        assert "51%" in check.reason

    def test_absent_is_not_attenuated(self, iso_table, thresholds):
        """If the 1x line is below the lowest measured bin it was not attenuated
        — it was never acquired. Claiming a rolloff percentage there would state
        a measurement that was never made."""
        pairs = [(10.0 + i * 0.25, 0.01) for i in range(2000)]
        check = _check(
            _gate(
                _spectrum(pairs), sensor=_sensor(rpm=18.0),
                iso_table=iso_table, thresholds=thresholds,
            ),
            "ac_coupling_rolloff",
        )
        assert check.status == "not_applicable"
        assert "outside the acquired band" in check.reason

    def test_a_resolution_limit_is_not_blamed_on_the_sensor(self, iso_table, thresholds):
        """Coarse bins cannot resolve a slow 1x at any coupling."""
        pairs = [(i * 2.93, 0.01) for i in range(200)]
        check = _check(
            _gate(
                _spectrum(pairs), sensor=_sensor(rpm=18.0),
                iso_table=iso_table, thresholds=thresholds,
            ),
            "ac_coupling_rolloff",
        )
        assert check.status == "not_applicable"
        assert "cannot resolve" in check.reason


class TestLineFrequencyContent:
    """Descriptive by design: it reports the line, never asserts the cause."""

    def _with_line(self, freq, amp=0.08):
        pairs = _grid()
        idx = int(round(freq / 0.25))
        pairs[idx] = (freq, amp)
        return pairs

    def test_an_off_order_line_on_a_supply_frequency_warns(self, iso_table, thresholds):
        # shaft 30 Hz -> orders 30/60/90...; 50 Hz is not one of them.
        check = _check(
            _gate(_spectrum(self._with_line(50.0)), iso_table=iso_table, thresholds=thresholds),
            "line_frequency_content",
        )
        assert check.status == "warn"
        assert "discrete line at 50.00 Hz" in check.reason
        assert "not within 3.0% of any 1-12x shaft order" in check.reason

    def test_the_reason_makes_no_causal_claim(self, iso_table, thresholds):
        """The evidence cannot separate supply pickup from a neighbouring
        machine, a belt line, or a resonance — so the check must not say it can."""
        check = _check(
            _gate(_spectrum(self._with_line(50.0)), iso_table=iso_table, thresholds=thresholds),
            "line_frequency_content",
        )
        assert "Origin not established" in check.reason
        for claim in ("pickup was", "electrical fault", "is mains", "caused by"):
            assert claim not in check.reason

    def test_a_healthy_off_line_peak_does_not_warn(self, iso_table, thresholds):
        """A real 49.5 Hz machine line is NOT a supply line: grid frequency is
        regulated to about +-0.2 Hz, so the local maximum must be CENTRED on the
        nominal value. This is the case that would otherwise have fired on the
        healthy synthetic fixture."""
        check = _check(
            _gate(_spectrum(self._with_line(49.5)), iso_table=iso_table, thresholds=thresholds),
            "line_frequency_content",
        )
        assert check.status == "pass"

    def test_a_flat_topped_plateau_is_not_a_discrete_line(self, iso_table, thresholds):
        """A discrete line is a STRICT local maximum; a plateau has none, so a
        capped export cannot be read as a supply line — and the outcome does not
        depend on how ties happen to be broken."""
        pairs = _grid()
        for i in range(198, 204):  # 49.5-50.75 Hz, all identical
            pairs[i] = (pairs[i][0], 0.5)
        check = _check(
            _gate(_spectrum(pairs), iso_table=iso_table, thresholds=thresholds),
            "line_frequency_content",
        )
        assert check.status == "pass"

    def test_when_every_candidate_is_a_shaft_order_it_is_not_applicable(
        self, iso_table, thresholds
    ):
        """A 600 rpm machine puts 50 Hz at 5x and 60 Hz at 6x. Nothing could be
        assessed, so `pass` would claim an answer the evidence cannot give —
        the `iso_zone_elevated` precedent."""
        check = _check(
            _gate(
                _spectrum(self._with_line(50.0)), sensor=_sensor(rpm=600.0),
                iso_table=iso_table, thresholds=thresholds,
            ),
            "line_frequency_content",
        )
        assert check.status == "not_applicable"
        assert "not separable" in check.reason

    def test_partial_coverage_is_stated_on_a_pass(self, iso_table, thresholds):
        """At 1500 rpm, 50 Hz is 2x shaft and cannot be assessed; 60 Hz can. A
        bare `pass` would read as 'both were checked and both were clean'."""
        check = _check(
            _gate(
                _spectrum(_grid()), sensor=_sensor(rpm=1500.0),
                iso_table=iso_table, thresholds=thresholds,
            ),
            "line_frequency_content",
        )
        assert check.status == "pass"
        assert "were not assessed" in check.reason

    def test_non_warn_reasons_carry_no_figures(self, iso_table, thresholds):
        """The markdown report renders only warn/fail reasons (`_dq_notes`),
        while the HTML roster renders every check's reason — so a number in a
        pass/not_applicable reason appears in the HTML with nothing in the
        markdown to trace to, and `TestNoInventedNumbers` fails. Non-warn
        reasons therefore state the situation without quoting figures; the
        figures live in the Analysis-parameters table, where they are records.
        """
        import re as _re

        cases = [
            (_spectrum(_grid()), _sensor(rpm=1500.0)),                       # partial coverage
            (_spectrum(_grid()), _sensor(rpm=600.0)),                        # all on-order
            (_spectrum([(10.0 + i * 2.5, 0.01) for i in range(400)]), _sensor(rpm=18.0)),
            (_spectrum([(i * 2.93, 0.01) for i in range(200)]), _sensor(rpm=18.0)),
        ]
        for spectrum, sensor in cases:
            result = _gate(spectrum, sensor=sensor, iso_table=iso_table, thresholds=thresholds)
            for check in result.checks:
                if check.status in ("warn", "fail") or not check.reason:
                    continue
                assert not _re.search(r"\d", check.reason), (
                    f"{check.name} ({check.status}) quotes a figure in its reason: {check.reason!r}"
                )

    def test_the_induction_motor_case_is_silent_by_construction(self, iso_table, thresholds):
        """A line-fed induction motor turns at LF/(p/2)*(1-s), so order p/2 sits
        exactly slip% from 1x line frequency and slip is 0-3% — inside any sane
        order tolerance. This check therefore CANNOT flag supply pickup on the
        machine class the name suggests, and says not_applicable rather than
        implying coverage it does not have. Recorded as behaviour so the limit
        is visible rather than discovered later."""
        # 4-pole, 60 Hz, 1% slip -> 1782 rpm -> shaft 29.7 Hz, 2x = 59.4 Hz.
        check = _check(
            _gate(
                _spectrum(self._with_line(60.0)), sensor=_sensor(rpm=1782.0),
                iso_table=iso_table, thresholds=thresholds,
            ),
            "line_frequency_content",
        )
        assert check.status in ("pass", "not_applicable")
        assert check.status != "warn"


class TestWarnOnlyContract:
    """No new check may fail a reading or block baseline training."""

    def test_a_plateau_warns_but_does_not_fail_or_block_training(self, iso_table, thresholds):
        pairs = _grid()
        for i in range(200, 208):
            pairs[i] = (pairs[i][0], 0.5)
        result = _gate(_spectrum(pairs), iso_table=iso_table, thresholds=thresholds)
        assert result.overall == "warn"
        assert result.train_baseline is True
        assert result.train_reasons == []

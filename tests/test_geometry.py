"""Session GEOM-A — the machine-setup geometry, from the model up.

Three claims are pinned here, and the third is the one the session exists for:

  * the geometry fields round-trip through the upload boundary — every lane,
    including the four (.mat CWRU / MFPT / wind-turbine, MAFAULDA CSV) that
    build their own MachineMeta and never call `machine_from_form`;
  * the belt fundamental is DERIVED, and the derivation is arithmetic anyone
    can check by hand — no threshold, no config, no measurement;
  * geometry turns hypothesis into commitment: with `blades` supplied a
    blade-pass peak commits `elevated_blade_pass`, and with `coupled=False`
    the same axial-dominant 1x trio that commits `angular_misalignment`
    commits `bent_shaft` instead. Both detectors already existed and neither
    could be reached from the product (FAULT_COVERAGE §3, the "UNREACHABLE in
    product" rows). pdm_core is untouched by this session; these tests are the
    proof that the wiring reaches it unchanged.
"""

from __future__ import annotations

import csv
import math

import pytest

from vib_agent.adapters.uploads import parse_upload
from vib_agent.adapters.uploads.common import (
    UploadForm,
    apply_machine_geometry,
    belt_frequency_hz,
    belt_length_mm,
    belt_spec_from_form,
)
from vib_agent.config import load_config, load_thresholds
from vib_agent.models import BeltSpec, MachineMeta
from vib_agent.pipeline import run_analysis
from vib_agent.webapp import assembly as A

RPM = 1800.0
SHAFT_HZ = RPM / 60.0  # 30 Hz


@pytest.fixture(scope="module")
def cfg():
    return {
        "iso_table": load_config("iso_zones")["zones"],
        "thresholds": load_thresholds("route"),
        "rules": load_config("next_measurements"),
        "bearings": load_config("bearings"),
    }


def _spectrum_csv(path, peaks, *, n=400, fmax=200.0):
    """The tabular-spectrum shape the multi-axis suite uses, so the fixtures
    here and there are directly comparable."""
    freqs = [i * fmax / n for i in range(n)]
    amp = [0.001] * n
    for pk_hz, pk_amp in peaks:
        idx = min(range(n), key=lambda i: abs(freqs[i] - pk_hz))
        amp[idx] = pk_amp
    with open(path, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["freq_hz", "amplitude"])
        for freq, value in zip(freqs, amp):
            writer.writerow([freq, value])
    return path


def _form(**over) -> UploadForm:
    base = dict(machine_alias="GeomPump", rpm=RPM, iso_group="2", iso_support="rigid", machine_type="motor")
    base.update(over)
    return UploadForm(**base)


# ══════════════════════════════════════════════════════════════════════════
# 1 · The derivation — arithmetic, checkable by hand
# ══════════════════════════════════════════════════════════════════════════
class TestBeltDerivation:
    """D1 = 150 mm, D2 = 300 mm, C = 600 mm, N1 = 1780 rpm.

        L  = 2C + (pi/2)(D1+D2) + (D2-D1)^2/(4C)
           = 1200 + 706.858 + 9.375        = 1916.233 mm
        v  = pi * D1 * N1 / 60             = 13979.6 mm/s
        BF = v / L                         = 7.2956 Hz
    """

    D1, D2, C, N = 150.0, 300.0, 600.0, 1780.0

    def test_length_matches_the_hand_computation(self):
        hand = 2 * self.C + (math.pi / 2) * (self.D1 + self.D2) + (self.D2 - self.D1) ** 2 / (4 * self.C)
        assert belt_length_mm(self.D1, self.D2, self.C) == pytest.approx(hand, rel=1e-12)
        assert belt_length_mm(self.D1, self.D2, self.C) == pytest.approx(1916.2333, abs=1e-3)

    def test_frequency_matches_the_hand_computation(self):
        hand = (math.pi * self.D1 * self.N / 60.0) / belt_length_mm(self.D1, self.D2, self.C)
        got = belt_frequency_hz(self.D1, self.D2, self.C, self.N)
        assert got == pytest.approx(hand, rel=1e-12)
        assert got == pytest.approx(7.2956, abs=1e-3)

    def test_belt_frequency_is_a_sane_fraction_of_shaft_speed(self):
        """A belt turns slower than the shaft that drives it. Anything at or
        above 1x shaft would mean the arithmetic inverted somewhere."""
        shaft_hz = self.N / 60.0
        order = belt_frequency_hz(self.D1, self.D2, self.C, self.N) / shaft_hz
        assert 0.05 < order < 1.0, f"belt order {order:.3f} is not physically plausible"

    def test_equal_pulleys_reduce_to_the_simple_case(self):
        """D1 == D2 kills the (D2-D1)^2 term: L = 2C + pi*D."""
        assert belt_length_mm(200.0, 200.0, 500.0) == pytest.approx(1000.0 + math.pi * 200.0, rel=1e-12)

    def test_spec_carries_every_input_it_derived_from(self):
        spec = belt_spec_from_form(
            _form(drive_pulley_mm=self.D1, driven_pulley_mm=self.D2,
                  pulley_center_distance_mm=self.C),
            rpm=self.N,
        )
        assert spec is not None
        assert spec.freq_hz == pytest.approx(7.2956, abs=1e-3)
        assert (spec.drive_pulley_mm, spec.driven_pulley_mm, spec.center_distance_mm) == \
            (self.D1, self.D2, self.C)
        assert spec.belt_length_mm == pytest.approx(1916.2333, abs=1e-3)

    def test_no_pulley_geometry_is_no_belt(self):
        assert belt_spec_from_form(_form(), rpm=RPM) is None

    @pytest.mark.parametrize("partial", [
        {"drive_pulley_mm": 150.0},
        {"drive_pulley_mm": 150.0, "driven_pulley_mm": 300.0},
        {"driven_pulley_mm": 300.0, "pulley_center_distance_mm": 600.0},
    ])
    def test_partial_geometry_is_refused_not_guessed(self, partial):
        with pytest.raises(ValueError, match="ALL of"):
            belt_spec_from_form(_form(**partial), rpm=RPM)

    def test_overlapping_pulleys_are_refused(self):
        with pytest.raises(ValueError, match="overlap"):
            belt_spec_from_form(
                _form(drive_pulley_mm=300.0, driven_pulley_mm=300.0,
                      pulley_center_distance_mm=250.0),
                rpm=RPM,
            )

    def test_geometry_without_a_speed_is_refused(self):
        with pytest.raises(ValueError, match="running speed"):
            belt_spec_from_form(
                _form(drive_pulley_mm=150.0, driven_pulley_mm=300.0,
                      pulley_center_distance_mm=600.0),
                rpm=0.0,
            )


# ══════════════════════════════════════════════════════════════════════════
# 2 · The model
# ══════════════════════════════════════════════════════════════════════════
class TestMachineMetaGeometry:
    def test_defaults_are_absent_and_change_nothing(self):
        m = MachineMeta(mac="M", name="M", active=True)
        assert (m.gear_teeth_driving, m.gear_teeth_driven, m.rotor_bars, m.poles,
                m.line_freq_hz, m.drive_type) == (None,) * 6
        assert m.coupled is True and m.coupled_stated is False

    @pytest.mark.parametrize("bad", [
        {"poles": 3}, {"poles": 0}, {"poles": -2},
        {"rotor_bars": 0}, {"gear_teeth_driving": -1}, {"gear_teeth_driven": 0},
        {"line_freq_hz": 0}, {"drive_type": "diesel"},
    ])
    def test_impossible_geometry_is_refused(self, bad):
        with pytest.raises(ValueError):
            MachineMeta(mac="M", name="M", active=True, **bad)

    def test_odd_pole_counts_are_refused_because_they_do_not_exist(self):
        """A machine is wound in pole PAIRS. Recording an odd count would
        poison the pole-pass frequency SIDEBAND computes from it, silently."""
        with pytest.raises(ValueError, match="even"):
            MachineMeta(mac="M", name="M", active=True, poles=5)
        assert MachineMeta(mac="M", name="M", active=True, poles=6).poles == 6

    def test_belt_freq_alone_still_validates(self):
        """A Case JSON written before this session supplies freq_hz and nothing
        else — that must stay legal, or every stored case breaks."""
        assert BeltSpec(freq_hz=52.0).drive_pulley_mm is None


# ══════════════════════════════════════════════════════════════════════════
# 3 · Round-trip through the upload boundary — every lane
# ══════════════════════════════════════════════════════════════════════════
GEOMETRY_FORM = dict(
    measurement_location="Motor DE",
    coupling="uncoupled",
    blades=6,
    gear_teeth_driving=23,
    gear_teeth_driven=91,
    rotor_bars=44,
    poles=4,
    line_freq_hz=60.0,
    drive_type="vfd",
    drive_pulley_mm=150.0,
    driven_pulley_mm=300.0,
    pulley_center_distance_mm=600.0,
)


class TestRoundTrip:
    def test_every_declared_field_reaches_the_case(self, tmp_path, cfg):
        path = _spectrum_csv(tmp_path / "upload.csv", [(SHAFT_HZ, 0.5)])
        case, _kind, _note = parse_upload(path, _form(**GEOMETRY_FORM),
                                          bearings_cfg=cfg["bearings"])
        m = case.machine
        assert m.location == "Motor DE"
        assert m.coupled is False and m.coupled_stated is True
        assert (m.blades, m.gear_teeth_driving, m.gear_teeth_driven) == (6, 23, 91)
        assert (m.rotor_bars, m.poles, m.line_freq_hz, m.drive_type) == (44, 4, 60.0, "vfd")
        assert m.belt is not None and m.belt.freq_hz == pytest.approx(7.379, abs=1e-2)

    def test_an_untouched_form_leaves_the_machine_exactly_as_the_adapter_built_it(
        self, tmp_path, cfg
    ):
        """The byte-compat guarantee: a form with no geometry must produce the
        machine the adapter produced before this session existed."""
        path = _spectrum_csv(tmp_path / "upload.csv", [(SHAFT_HZ, 0.5)])
        case, _kind, _note = parse_upload(path, _form(), bearings_cfg=cfg["bearings"])
        m = case.machine
        assert m.location is None and m.belt is None and m.blades is None
        assert m.coupled is True and m.coupled_stated is False
        assert m.drive_type is None

    def test_declaring_coupled_is_not_the_same_as_saying_nothing(self, tmp_path, cfg):
        """`coupled` alone cannot tell an answer from a default; `coupled_stated`
        is what stops the report calling a supplied answer 'not provided'."""
        path = _spectrum_csv(tmp_path / "upload.csv", [(SHAFT_HZ, 0.5)])
        case, _kind, _note = parse_upload(path, _form(coupling="coupled"),
                                          bearings_cfg=cfg["bearings"])
        assert case.machine.coupled is True and case.machine.coupled_stated is True

    def test_blank_selects_mean_not_stated(self, tmp_path, cfg):
        """An untouched <select> posts "" — it must never become a value."""
        path = _spectrum_csv(tmp_path / "upload.csv", [(SHAFT_HZ, 0.5)])
        case, _kind, _note = parse_upload(
            path, _form(coupling="", drive_type="", measurement_location=""),
            bearings_cfg=cfg["bearings"],
        )
        m = case.machine
        assert m.coupled_stated is False and m.drive_type is None and m.location is None

    def test_belt_derives_from_the_speed_the_analysis_uses(self, cfg):
        """The .mat lanes take their shaft rate from the FILE and say so. A belt
        frequency derived from the form's RPM instead would be matched against a
        spectrum the detectors read at a different shaft speed."""
        from vib_agent.models import Case, SensorData

        case = Case(
            name="M",
            machine=MachineMeta(mac="M", name="M", active=True),
            sensor_data=SensorData(rpm=900.0),  # the file's speed, half the form's
        )
        out = apply_machine_geometry(case, _form(
            drive_pulley_mm=150.0, driven_pulley_mm=300.0, pulley_center_distance_mm=600.0))
        assert out.machine.belt is not None
        assert out.machine.belt.freq_hz == pytest.approx(
            belt_frequency_hz(150.0, 300.0, 600.0, 900.0), rel=1e-12)

    def test_geometry_survives_the_multi_axis_merge(self, tmp_path, cfg):
        """`_merge_cases` copies slot 1's machine; geometry attaches per file at
        the dispatch point, so the merged case must still carry it."""
        parsed = []
        for name, direction, peaks in (
            ("h.csv", "radial_h", [(SHAFT_HZ, 0.15)]),
            ("v.csv", "radial_v", [(SHAFT_HZ, 0.14)]),
            ("a.csv", "axial", [(SHAFT_HZ, 0.60)]),
        ):
            path = _spectrum_csv(tmp_path / name, peaks)
            case, kind, note = parse_upload(path, _form(**GEOMETRY_FORM),
                                            bearings_cfg=cfg["bearings"])
            parsed.append(A.ParsedChannel(direction, False, case, kind, note))
        out = A.merge_channels(parsed, [], iso_table=cfg["iso_table"],
                               thresholds=cfg["thresholds"], rules=cfg["rules"])
        assert out.case.machine.blades == 6
        assert out.case.machine.coupled is False
        assert out.case.machine.belt is not None


# ══════════════════════════════════════════════════════════════════════════
# 4 · Geometry upgrades hypothesis to commitment
# ══════════════════════════════════════════════════════════════════════════
def _faults(case, cfg) -> list[str]:
    result = run_analysis(case, iso_table=cfg["iso_table"], thresholds=cfg["thresholds"],
                          rules=cfg["rules"])
    return [f.fault for f in result.findings]


class TestBladePassBecomesReachable:
    """`detect_blade_pass` needs `machine.blades` and elevated ISO severity.
    The count had no form field, so the detector could never fire on an upload:
    FAULT_COVERAGE §3 records it as "committed in code, UNREACHABLE in product"."""

    BLADES = 6
    BPF_HZ = BLADES * SHAFT_HZ  # 180 Hz, >=5% clear of 150 Hz (5x)

    def _case(self, tmp_path, cfg, *, blades):
        path = _spectrum_csv(tmp_path / "upload.csv",
                             [(self.BPF_HZ, 3.5), (81.3, 0.8), (SHAFT_HZ, 0.4)])
        form = _form(blades=blades) if blades is not None else _form()
        case, _kind, _note = parse_upload(path, form, bearings_cfg=cfg["bearings"])
        return case

    def test_without_the_count_the_detector_cannot_run(self, tmp_path, cfg):
        assert "elevated_blade_pass" not in _faults(self._case(tmp_path, cfg, blades=None), cfg)

    def test_with_the_count_the_same_spectrum_commits(self, tmp_path, cfg):
        faults = _faults(self._case(tmp_path, cfg, blades=self.BLADES), cfg)
        assert "elevated_blade_pass" in faults, faults


def _misalignment_trio(tmp_path, cfg, *, coupling):
    """The Session E fixture that commits `angular_misalignment`
    (tests/test_multiaxis.py::TestConjunction) — axial 1x dominant,
    axial/radial ratio 4.0 — assembled through the real upload path.

    Session BENT-FIX lifted this out of `TestCoupledRoutesTheBentShaftBranch`
    so that class and `TestBentShaftAnswersWithTheMeasurementThatResolvesIt`
    argue from ONE spectrum. Two copies of a conjunction fixture can drift
    apart, and a conjunction proof whose two halves are not the same spectrum
    proves nothing. No assertion moved with it.
    """
    scale = 10.0  # the PDMFIX gate scale: ratios untouched, loudness lifted
    parsed = []
    for name, direction, amp in (("h.csv", "radial_h", 0.15),
                                 ("v.csv", "radial_v", 0.14),
                                 ("a.csv", "axial", 0.60)):
        path = _spectrum_csv(tmp_path / name, [(SHAFT_HZ, amp * scale)])
        form = _form(coupling=coupling) if coupling else _form()
        case, kind, note = parse_upload(path, form, bearings_cfg=cfg["bearings"])
        parsed.append(A.ParsedChannel(direction, False, case, kind, note))
    return A.merge_channels(parsed, [], iso_table=cfg["iso_table"],
                            thresholds=cfg["thresholds"], rules=cfg["rules"]).case


def _analysis(case, cfg):
    return run_analysis(case, iso_table=cfg["iso_table"], thresholds=cfg["thresholds"],
                        rules=cfg["rules"])


class TestCoupledRoutesTheBentShaftBranch:
    """The conjunction proof §S6 asks for. `coupled` is the ONLY thing that
    changes between the two assertions, and pdm_core is byte-identical across
    them: `bearing_rca.py:747` is the uncoupled alias of the very branch the
    first assertion lands on."""

    def _trio(self, tmp_path, cfg, *, coupling):
        return _misalignment_trio(tmp_path, cfg, coupling=coupling)

    def test_coupled_trio_commits_angular_misalignment(self, tmp_path, cfg):
        faults = _faults(self._trio(tmp_path, cfg, coupling=None), cfg)
        assert "angular_misalignment" in faults and "bent_shaft" not in faults

    def test_uncoupled_trio_commits_bent_shaft_instead(self, tmp_path, cfg):
        faults = _faults(self._trio(tmp_path, cfg, coupling="uncoupled"), cfg)
        assert "bent_shaft" in faults, faults
        assert "angular_misalignment" not in faults

    def test_declaring_coupled_leaves_the_misalignment_call_untouched(self, tmp_path, cfg):
        """An explicit 'coupled' must behave exactly like the default — it
        records an answer, it does not change one."""
        assert _faults(self._trio(tmp_path, cfg, coupling="coupled"), cfg) == \
            _faults(self._trio(tmp_path, cfg, coupling=None), cfg)


class TestBentShaftAnswersWithTheMeasurementThatResolvesIt:
    """Session BENT-FIX, fix 1. The same one spectrum as the class above, so
    `coupled` is again the only difference — but the assertions here are on
    what the report tells the analyst to GO AND DO.

    GEOM-B found this by reading its acceptance PDF (SESSION_GEOMB.md §7,
    finding 1): a committed bent shaft's only follow-up was "Laser
    shaft-alignment verification and thermography across the coupling", on a
    machine whose coupling the analyst had just declared absent and whose own
    finding paragraph says "Machine has no coupling, ruling out misalignment".
    The report contradicted itself between two adjacent sections.
    """

    def _recs(self, tmp_path, cfg, *, coupling):
        return _analysis(_misalignment_trio(tmp_path, cfg, coupling=coupling), cfg) \
            .recommended_measurements

    def test_uncoupled_asks_for_the_shaft_measurements(self, tmp_path, cfg):
        recs = self._recs(tmp_path, cfg, coupling="uncoupled")
        assert recs, "a committed bent shaft must not leave the analyst without a next step"
        blob = " ".join(f"{r.technique} {r.purpose} {r.trigger}" for r in recs).lower()
        assert "phase" in blob and "runout" in blob, blob

    def test_no_recommendation_names_a_coupling_on_an_uncoupled_machine(self, tmp_path, cfg):
        """Substring, not phrase: "across the coupling", "cross-coupling
        phase" and "coupling heat" are three different sentences the analyst
        must not be sent, and a future fourth would pass a phrase check."""
        recs = self._recs(tmp_path, cfg, coupling="uncoupled")
        blob = " ".join(f"{r.technique} {r.purpose} {r.trigger}" for r in recs).lower()
        assert "coupl" not in blob, blob

    def test_the_coupled_machine_still_gets_its_alignment_check(self, tmp_path, cfg):
        """The other half of the conjunction: this fix must REPLACE an answer
        on one machine, not delete it from both."""
        techniques = [r.technique.lower() for r in self._recs(tmp_path, cfg, coupling=None)]
        assert any("alignment" in t for t in techniques), techniques
        assert not any("runout" in t for t in techniques), techniques


class TestTheEvidenceRowPrintsTheFrequencyItArguedFrom:
    """Session BENT-FIX, fix 2 — the second thing GEOM-B found by reading the
    PDF (SESSION_GEOMB.md §7, finding 2).

    `detect_misalignment_family` published `expected_hz = shaft x 2` for all
    five sub-types. Four of them argue from the 2x, so that is right. The
    bent-shaft branch argues from a dominant AXIAL 1x — so its Evidence row
    printed a computed 60 Hz against an observed 30 Hz on this fixture, which
    an analyst reads as a 100 % frequency error on a finding that is correct.
    """

    def _match(self, tmp_path, cfg, *, coupling, fault):
        result = _analysis(_misalignment_trio(tmp_path, cfg, coupling=coupling), cfg)
        match = next((m for m in result.rca.primary_findings if m.fault == fault), None)
        assert match is not None, [m.fault for m in result.rca.primary_findings]
        return match, result.rca.shaft_freq_hz

    def test_bent_shaft_computes_and_observes_the_axial_1x(self, tmp_path, cfg):
        match, shaft = self._match(tmp_path, cfg, coupling="uncoupled", fault="bent_shaft")
        assert match.expected_hz == pytest.approx(shaft, abs=0.01)
        assert match.freq_hz == pytest.approx(shaft, abs=shaft * 0.05)
        # Stated as its own claim: the two columns used to name different
        # orders, and agreeing at 1x is the whole of the fix.
        assert match.expected_hz != pytest.approx(shaft * 2, abs=0.01)

    def test_the_row_is_on_the_axis_the_branch_argued_about(self, tmp_path, cfg):
        match, _ = self._match(tmp_path, cfg, coupling="uncoupled", fault="bent_shaft")
        result = _analysis(_misalignment_trio(tmp_path, cfg, coupling="uncoupled"), cfg)
        assert match.axis == result.rca.axial_axis

    def test_every_other_sub_type_keeps_the_2x_it_argues_from(self, tmp_path, cfg):
        """The guard that stops fix 2 spreading. `angular_misalignment` is the
        bent-shaft branch's own coupled twin — if the change had been made one
        level up, this is the row it would have moved, and 174 corpus rows
        with it."""
        match, shaft = self._match(tmp_path, cfg, coupling=None, fault="angular_misalignment")
        assert match.expected_hz == pytest.approx(shaft * 2, abs=0.01)


class TestBeltBecomesReachable:
    """`detect_belt_fault` needs `machine.belt.freq_hz`, which no form field set:
    FAULT_COVERAGE §3's other "UNREACHABLE in product" row.

    The geometry below derives 8.655 Hz at 1800 rpm — 0.289x shaft, so it is
    neither an integer order (the synchronous-collision guard's business) nor
    within tolerance of any shaft harmonic. The fixture seeds the DERIVED
    frequency rather than a hardcoded one, so the peak and the expectation
    cannot drift apart."""

    D1, D2, C = 120.0, 200.0, 400.0

    def _case(self, tmp_path, cfg, *, with_geometry):
        derived = belt_frequency_hz(self.D1, self.D2, self.C, RPM)
        path = _spectrum_csv(tmp_path / "upload.csv",
                             [(derived, 2.2), (derived * 2, 0.9), (SHAFT_HZ, 0.3)],
                             fmax=400.0, n=1600)  # 0.25 Hz bins: the seeded peak
        # lands well inside the +-3% match window around 8.655 Hz
        form = _form(drive_pulley_mm=self.D1, driven_pulley_mm=self.D2,
                     pulley_center_distance_mm=self.C) if with_geometry else _form()
        case, _kind, _note = parse_upload(path, form, bearings_cfg=cfg["bearings"])
        return case

    def test_without_pulley_geometry_the_detector_cannot_run(self, tmp_path, cfg):
        assert "belt_fault" not in _faults(self._case(tmp_path, cfg, with_geometry=False), cfg)

    def test_with_pulley_geometry_the_same_spectrum_commits(self, tmp_path, cfg):
        case = self._case(tmp_path, cfg, with_geometry=True)
        assert case.machine.belt is not None
        faults = _faults(case, cfg)
        assert "belt_fault" in faults, faults


# ══════════════════════════════════════════════════════════════════════════
# 5 · What making these detectors reachable EXPOSED — and what GEOM-B did
#
# GEOM-A is what put two long-standing `pdm_core` defects in front of an
# analyst: until that session no upload could reach either code path. GEOM-A was
# unlatched, so it pinned both as DOCUMENTATION tests here — tests that asserted
# the defect — and wrote them up in outputs/SESSION_GEOMA.md §4.
#
# Session GEOM-B (branch `geomb`, bearing_rca.py latched open) fixes both, one
# per slice, and rewrites each class as its fix lands — the rewrite that
# close-out demanded. Every class below states in its own docstring which
# behaviour it pins; where it pins the CORRECTED behaviour, a green run is an
# endorsement.
# ══════════════════════════════════════════════════════════════════════════
class TestBeltAnswersToTheAmplitudeDiscipline:
    """GEOM-B fix 1. `detect_belt_fault` was the only detector in its
    competition with NO amplitude floor and NO severity gate: `run_rca` called
    it with a bare `primary.extend(detect_belt_fault(ctx_flow))`, where
    imbalance and misalignment both answer to `_one_x_severity_gate` (PDMFIX
    ruling 1) and the harmonic family to `_clears_evidence_floor`.

    The conjunction below is the proof, and both halves matter. An elevated
    machine whose condition is a 1x imbalance, plus one stray line at 0.25 mm/s
    — twelve times quieter than the 1x that explains the reading — inside the
    +-3% window around the derived belt frequency: `imbalance` alone. The SAME
    geometry with a genuine belt line above the floor: `belt_fault` commits.
    Geometry still upgrades hypothesis to commitment; it no longer buys a
    commitment for a line that cannot support one.

    Withholding the geometry is the control: it was `imbalance` alone before
    GEOM-B and is `imbalance` alone after, so the change is in the discipline
    applied to a matched belt line, not in the imbalance path.
    """

    D1, D2, CENTRES = 120.0, 200.0, 400.0

    def _geometry(self):
        return dict(drive_pulley_mm=self.D1, driven_pulley_mm=self.D2,
                    pulley_center_distance_mm=self.CENTRES)

    def _stray_case(self, tmp_path, cfg, *, with_geometry):
        """The SESSION_GEOMA §4 fixture, unchanged: 1x at 3.0 mm/s, 2x at 0.6,
        and one line at 0.25 mm/s in the belt window. Measured ratios — the
        stray clears the amplitude floor (peak/axis-mean 23.6 vs floor_min
        12.73) and clears the severity gate (axis velocity 3.07 mm/s vs a
        1.4 mm/s Zone-B bar), so it is the DOMINANCE bar that refuses it:
        0.25/3.00 = 0.083, under `belt_dominance_min`."""
        belt_hz = belt_frequency_hz(self.D1, self.D2, self.CENTRES, RPM)
        path = _spectrum_csv(tmp_path / "upload.csv",
                             [(SHAFT_HZ, 3.0), (SHAFT_HZ * 2, 0.6), (belt_hz, 0.25)])
        geometry = self._geometry() if with_geometry else {}
        case, _kind, _note = parse_upload(path, _form(**geometry), bearings_cfg=cfg["bearings"])
        return case

    def test_a_stray_line_an_order_of_magnitude_down_no_longer_commits(self, tmp_path, cfg):
        faults = _faults(self._stray_case(tmp_path, cfg, with_geometry=True), cfg)
        assert faults == ["imbalance"], (
            f"{faults} — the belt line is 8% of the 1x that explains this reading; "
            "committing belt_fault on it is the GEOM-A finding this session fixed"
        )

    def test_the_same_spectrum_without_geometry_is_unchanged(self, tmp_path, cfg):
        faults = _faults(self._stray_case(tmp_path, cfg, with_geometry=False), cfg)
        assert faults == ["imbalance"], faults

    def test_a_genuine_belt_line_above_the_floor_still_commits(self, tmp_path, cfg):
        """The other half of the conjunction: same machine, same pulley
        geometry, a belt line that IS the story of the reading."""
        derived = belt_frequency_hz(self.D1, self.D2, self.CENTRES, RPM)
        path = _spectrum_csv(tmp_path / "upload.csv",
                             [(derived, 2.2), (derived * 2, 0.9), (SHAFT_HZ, 0.3)],
                             fmax=400.0, n=1600)
        case, _kind, _note = parse_upload(path, _form(**self._geometry()),
                                          bearings_cfg=cfg["bearings"])
        faults = _faults(case, cfg)
        assert "belt_fault" in faults, faults


class TestBendLocationIsNotAsserted:
    """GEOM-B fix 2. `bend_type` used to pick between "center bend likely" and
    "end bend likely" with `_top_peak_is(ctx.peaks, ctx.axial, ctx.shaft_freq,
    ctx.tolerance)` — the SAME call, with the same arguments, that computes
    `axial_1x_dominant` and guards entry to the branch. The else arm was
    unreachable, so every bent-shaft report asserted a centre bend, and the
    assertion was a restatement of the branch condition wearing a finding's
    clothes. FAULT_COVERAGE §3 had it as dead text; GEOM-A made the branch
    reachable, which is when dead text became a claim an analyst reads.

    A sub-type is asserted only where a distinct predicate supports it, and none
    is available here: a mid-span bend and a shaft-end bend both present as a
    dominant axial 1×, and 1× phase at both bearings is what separates them —
    a measurement this analysis does not make (session PHASE). So the finding
    states the limit and names what resolves it, and the coverage roster carries
    the matching caveat row (pinned in tests/test_report_na.py).
    """

    def _bent_shaft_trio(self, tmp_path, cfg):
        scale = 10.0
        parsed = []
        for name, direction, amp in (("h.csv", "radial_h", 0.15),
                                     ("v.csv", "radial_v", 0.14),
                                     ("a.csv", "axial", 0.60)):
            path = _spectrum_csv(tmp_path / name, [(SHAFT_HZ, amp * scale)])
            case, kind, note = parse_upload(path, _form(coupling="uncoupled"),
                                            bearings_cfg=cfg["bearings"])
            parsed.append(A.ParsedChannel(direction, False, case, kind, note))
        merged = A.merge_channels(parsed, [], iso_table=cfg["iso_table"],
                                  thresholds=cfg["thresholds"], rules=cfg["rules"]).case
        return run_analysis(merged, iso_table=cfg["iso_table"],
                            thresholds=cfg["thresholds"], rules=cfg["rules"])

    def test_no_bend_sub_type_is_claimed_and_the_limit_is_stated(self, tmp_path, cfg):
        result = self._bent_shaft_trio(tmp_path, cfg)
        # The prose lives on the FaultMatch (`RcaResult.primary_findings`); the
        # Finding's `evidence` dict carries the structured values only. It
        # reaches the analyst as the finding's `reason` (synthesize.py:77).
        match = next(m for m in result.rca.primary_findings if m.fault == "bent_shaft")
        assert "center bend" not in match.evidence.lower(), match.evidence
        assert "end bend" not in match.evidence.lower(), match.evidence
        assert "not determined" in match.evidence, match.evidence
        assert "phase" in match.evidence.lower(), match.evidence
        # the measured clauses the branch DOES support are untouched
        assert f"dominant on axial axis ({result.rca.axial_axis})" in match.evidence
        assert "no coupling" in match.evidence

    def test_the_finding_the_analyst_reads_carries_the_same_words(self, tmp_path, cfg):
        result = self._bent_shaft_trio(tmp_path, cfg)
        finding = next(f for f in result.findings if f.fault == "bent_shaft")
        assert "center bend" not in finding.reason.lower(), finding.reason
        assert "not determined" in finding.reason

    def test_no_predicate_in_the_branch_restates_its_own_guard(self):
        """The defect was structural, not textual: a sub-type chosen by the
        guard's own call. Pinned as a source property so a future sub-type is
        forced to bring a predicate of its own."""
        import inspect

        from vib_agent.pdm_core import bearing_rca

        # Comments are stripped first: the fix's own comment quotes the call to
        # explain what was removed, and prose about the defect is not the defect.
        source = "\n".join(
            line for line in inspect.getsource(bearing_rca.detect_misalignment_family).splitlines()
            if not line.strip().startswith("#")
        )
        call = "_top_peak_is(ctx.peaks, ctx.axial, ctx.shaft_freq, ctx.tolerance)"
        assert f"axial_1x_dominant = {call}" in source, "the branch guard moved"
        assert source.count(call) == 1, (
            "the guard's own call is used a second time inside the branch it "
            "guards — that is the tautology GEOM-B removed"
        )

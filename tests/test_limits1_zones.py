"""LIMITS-1a — a machine judged against its own limits, with ISO still on record.

Most machines a plant actually runs are alarmed against a number the site chose,
not against a row of ISO 20816-3. The classifier has been able to do that since
Phase 1 — `resolve_thresholds` tier 1 (`iso_classify.py:43-48`) reads
`MachineMeta.thresholds` and computes the zone against it — but it could not say
so in a way a report could print, and it could not say what ISO would have made
of the same reading. These pins cover both halves:

* the boundaries themselves, which are NOT new (the session ruling was to use the
  existing `MachineThresholds` slot rather than add a second spelling of the same
  three numbers), pinned here because `THRESHOLDS_AND_CONTEXT.md` §S0 makes it a
  standing rule that every threshold change asserts the EMITTED
  `threshold_source` — a value being available is not evidence it was used; and
* `zone_basis` / `iso_zone_would_be`, which are new, and which answer §6 Q3
  ("both zones, or one?") with "both".

THE FIXTURE, and why this one. `T12_bpfo_bearing_fault` on `comp_machine` is the
repo's 5.20 mm/s trio — x 0.5 / y 5.2 / z 4.8, so `severity_rms` is 5.20 on `y`.
`comp_machine` is ISO group 2 on rigid support, whose boundaries are
1.4 / 2.8 / 4.5, so **ISO says Zone D**. That headroom above the top ISO boundary
is what lets one reading be walked through C, B and D by moving only the limits,
which is the point: the zone letter is a judgement about a threshold, and the
same millimetres per second mean different things at different plants.
"""

from __future__ import annotations

import pytest

from tests.fixtures import REFERENCE_CASES
from vib_agent.models import MachineMeta, MachineThresholds, SensorData
from vib_agent.pdm_core.iso_classify import classify, mark_not_assessable, resolve_thresholds

#: The trio. severity_rms = 5.20 mm/s on y; ISO group2/rigid puts that in Zone D.
TRIO = REFERENCE_CASES["T12_bpfo_bearing_fault"]["sensor_data"]

#: Boundary sets that move that ONE reading across three zones. Each is a real
#: shape of plant limit: tighter than ISO, far looser, and tighter still.
TO_ZONE_C = MachineThresholds(ab=2.0, bc=5.0, cd=8.0)      # 5.0 <= 5.2 < 8.0
TO_ZONE_B = MachineThresholds(ab=5.0, bc=8.0, cd=12.0)     # 5.0 <= 5.2 < 8.0
TO_ZONE_D = MachineThresholds(ab=1.0, bc=2.0, cd=3.0)      # 3.0 <= 5.2


def _classify(machine: MachineMeta, iso_table: dict, **kw):
    return classify(SensorData(**TRIO), machine, resolve_thresholds(machine, iso_table, **kw))


def _with_limit(machine: MachineMeta, limit: MachineThresholds) -> MachineMeta:
    return machine.model_copy(update={"thresholds": limit})


class TestTheDefaultPathDidNotMove:
    """No limit set -> the shipped ISO path, unchanged, and saying so."""

    def test_iso_is_still_the_default(self, comp_machine, iso_table):
        r = _classify(comp_machine, iso_table)
        assert r.severity_rms == pytest.approx(5.2)
        assert r.dominant_axis == "y"
        assert r.iso_zone == "D"
        assert r.iso_severity == "danger"
        assert r.threshold_source == "iso_20816_3"
        assert (r.th_ab, r.th_bc, r.th_cd) == (1.4, 2.8, 4.5)

    def test_default_reading_declares_an_iso_basis_and_no_would_be(self, comp_machine, iso_table):
        # There is no "would be" on the ISO path: the zone IS ISO's answer, and a
        # second field repeating the same letter would invite a report to print
        # "Zone D (ISO would say D)".
        r = _classify(comp_machine, iso_table)
        assert r.zone_basis == "iso"
        assert r.iso_zone_would_be is None

    def test_new_fields_are_the_only_thing_added(self, comp_machine, iso_table):
        # The whole default-path Reading, field for field, against a hand-written
        # expectation of the two new keys. If a third field ever appears, or an
        # existing one moves, this fails rather than being absorbed silently.
        r = _classify(comp_machine, iso_table)
        assert set(r.model_dump()) - {"zone_basis", "iso_zone_would_be"} == {
            "ts", "factory_id", "factory_timezone", "machine_id", "machine_type", "mac",
            "x_vel_mms", "y_vel_mms", "z_vel_mms", "severity_rms", "dominant_axis",
            "iso_zone", "iso_severity", "iso_group", "iso_support", "threshold_source",
            "threshold_note", "th_ab", "th_bc", "th_cd", "margin_to_next_boundary",
            "not_assessable_reason",
        }


class TestOneReadingThreeZones:
    """5.20 mm/s is Zone C, B or D depending only on whose limits are applied."""

    @pytest.mark.parametrize(
        "limit,expected_zone,expected_severity",
        [
            (TO_ZONE_C, "C", "warn"),
            (TO_ZONE_B, "B", "info"),
            (TO_ZONE_D, "D", "danger"),
        ],
        ids=["tighter_than_iso_zone_c", "far_looser_zone_b", "tighter_still_zone_d"],
    )
    def test_zone_follows_the_plant_limit(
        self, comp_machine, iso_table, limit, expected_zone, expected_severity
    ):
        r = _classify(_with_limit(comp_machine, limit), iso_table)
        assert r.severity_rms == pytest.approx(5.2)  # the measurement never moves
        assert r.iso_zone == expected_zone
        assert r.iso_severity == expected_severity
        # §S0: assert the EMITTED source, not merely that a value could be built.
        assert r.threshold_source == "custom"
        assert r.zone_basis == "custom"
        assert (r.th_ab, r.th_bc, r.th_cd) == (limit.ab, limit.bc, limit.cd)

    @pytest.mark.parametrize(
        "limit,expected_zone",
        [(TO_ZONE_C, "C"), (TO_ZONE_B, "B"), (TO_ZONE_D, "D")],
        ids=["zone_c", "zone_b", "zone_d"],
    )
    def test_iso_zone_would_be_is_d_in_every_case(
        self, comp_machine, iso_table, limit, expected_zone
    ):
        # ISO's answer is a property of the reading and the machine, so it is D
        # in all three — including the third, where the plant limit AGREES. The
        # agreeing case is the one worth pinning: `iso_zone_would_be` must be a
        # computed answer, not a "disagreement" flag that happens to look right.
        r = _classify(_with_limit(comp_machine, limit), iso_table)
        assert r.iso_zone == expected_zone
        assert r.iso_zone_would_be == "D"

    def test_margin_is_measured_against_the_limit_in_force(self, comp_machine, iso_table):
        # Not against ISO. Under TO_ZONE_C the reading is in C, so the next
        # boundary is that limit's C/D at 8.0 — 2.8 mm/s away, where ISO would
        # have reported no margin at all (5.2 is already past ISO's top boundary).
        r = _classify(_with_limit(comp_machine, TO_ZONE_C), iso_table)
        assert r.margin_to_next_boundary == pytest.approx(8.0 - 5.2)
        assert _classify(comp_machine, iso_table).margin_to_next_boundary is None


class TestBadLimitsAreRefused:
    """A limit that is not a limit is rejected where it is built, by name."""

    def test_out_of_order_boundaries_raise(self):
        with pytest.raises(ValueError, match=r"ab < bc < cd"):
            MachineThresholds(ab=5.0, bc=4.0, cd=3.0)

    def test_equal_boundaries_raise(self):
        # Strictly increasing: ab == bc would make Zone B a zero-width band a
        # reading could never land in.
        with pytest.raises(ValueError, match=r"ab < bc < cd"):
            MachineThresholds(ab=2.0, bc=2.0, cd=8.0)

    @pytest.mark.parametrize("field", ["ab", "bc", "cd"])
    def test_non_positive_boundaries_raise(self, field):
        values = {"ab": 2.0, "bc": 5.0, "cd": 8.0} | {field: 0.0}
        with pytest.raises(ValueError, match=rf"thresholds\.{field} must be > 0"):
            MachineThresholds(**values)

    def test_a_refused_limit_never_reaches_a_reading(self, comp_machine, iso_table):
        # The validator fires at construction, so there is no path by which a
        # reversed trio becomes a zone letter.
        with pytest.raises(ValueError, match=r"ab < bc < cd"):
            _with_limit(comp_machine, MachineThresholds(ab=9.0, bc=8.0, cd=7.0))
        assert _classify(comp_machine, iso_table).threshold_source == "iso_20816_3"


class TestWhenIsoHasNoOpinion:
    """A limit set on a machine ISO 20816-3 does not cover still classifies."""

    def test_missing_group_and_support_gives_no_would_be_and_does_not_raise(self, iso_table):
        # This is the case the feature exists for. Tier 3 raises for a machine
        # with no iso_group/iso_support — correctly, it is the last tier — but a
        # plant sets its own limit precisely for machines the table omits, so the
        # would-be lookup must decline rather than fail the job.
        machine = MachineMeta(mac="X", name="Uncovered", active=True, thresholds=TO_ZONE_C)
        r = _classify(machine, iso_table)
        assert r.iso_zone == "C"
        assert r.zone_basis == "custom"
        assert r.iso_zone_would_be is None

    def test_unknown_iso_key_gives_no_would_be_and_does_not_raise(self, comp_machine):
        # Same, one step subtler: group and support are both set and individually
        # valid, but the table handed in has no row for the pair.
        r = _classify(_with_limit(comp_machine, TO_ZONE_C), {"1_rigid": {"ab": 1, "bc": 2, "cd": 3}})
        assert r.iso_zone == "C"
        assert r.iso_zone_would_be is None

    def test_tier_three_still_raises_on_that_same_machine(self, iso_table):
        # The decline above is scoped to the would-be lookup. With NO limit set,
        # an unresolvable machine is still a loud failure at config-load time.
        machine = MachineMeta(mac="X", name="Uncovered", active=True)
        with pytest.raises(ValueError, match="cannot resolve"):
            resolve_thresholds(machine, iso_table)


class TestWithheldMeansWithheld:
    """not_assessable withholds the ISO answer too, not just the zone."""

    def test_mark_not_assessable_clears_would_be_but_keeps_basis(self, comp_machine, iso_table):
        r = _classify(_with_limit(comp_machine, TO_ZONE_C), iso_table)
        assert r.iso_zone_would_be == "D"

        withheld = mark_not_assessable(r, "implausible velocity units")
        assert withheld.iso_zone == "not_assessable"
        assert withheld.iso_severity == "unrated"
        assert withheld.margin_to_next_boundary is None
        # The judgement is withheld through BOTH doors: printing "ISO would have
        # said D" next to a withheld zone hands back exactly what was withheld.
        assert withheld.iso_zone_would_be is None
        # Provenance survives — it was still a plant limit we were going to use.
        assert withheld.zone_basis == "custom"

    def test_absent_velocity_has_no_would_be_either(self, comp_machine, iso_table):
        machine = _with_limit(comp_machine, TO_ZONE_C)
        r = classify(SensorData(mode=0), machine, resolve_thresholds(machine, iso_table))
        assert r.iso_zone == "not_assessable"
        assert r.severity_rms is None
        assert r.iso_zone_would_be is None
        assert r.zone_basis == "custom"


class TestTheFactoryDefaultTier:
    """Tier 2 is a limit somebody chose, so its basis is custom, not iso."""

    def test_factory_default_is_a_custom_basis(self, comp_machine, iso_table):
        r = _classify(comp_machine, iso_table, factory_default=TO_ZONE_C)
        assert r.threshold_source == "factory_default"  # which tier won
        assert r.zone_basis == "custom"                 # whose authority it was
        assert r.iso_zone == "C"
        assert r.iso_zone_would_be == "D"

    def test_a_machine_limit_still_outranks_a_factory_default(self, comp_machine, iso_table):
        r = _classify(_with_limit(comp_machine, TO_ZONE_B), iso_table, factory_default=TO_ZONE_C)
        assert r.threshold_source == "custom"
        assert r.iso_zone == "B"


class TestTheIsoFallbackIsCarriedOnEveryTier:
    """resolve_thresholds resolves ISO even when ISO did not win."""

    def test_carried_on_the_custom_tier(self, comp_machine, iso_table):
        resolved = resolve_thresholds(_with_limit(comp_machine, TO_ZONE_C), iso_table)
        assert resolved.source == "custom"
        assert resolved.thresholds.bc == 5.0
        # Tier 1 returns before the ISO table is consulted at all, which is why
        # this is resolved ahead of the branch rather than inside tier 3.
        assert resolved.iso_fallback is not None
        assert (resolved.iso_fallback.ab, resolved.iso_fallback.bc, resolved.iso_fallback.cd) == (
            1.4, 2.8, 4.5,
        )

    def test_carried_on_the_iso_tier_too(self, comp_machine, iso_table):
        resolved = resolve_thresholds(comp_machine, iso_table)
        assert resolved.source == "iso_20816_3"
        assert resolved.iso_fallback is not None
        assert resolved.iso_fallback.cd == resolved.thresholds.cd

    def test_none_when_the_table_cannot_answer(self, iso_table):
        machine = MachineMeta(mac="X", name="Uncovered", active=True, thresholds=TO_ZONE_C)
        assert resolve_thresholds(machine, iso_table).iso_fallback is None

"""Session R1 — bearing damage staging.

The load-bearing property here is NEGATIVE: this module must refuse to name a
stage it cannot see. Stages 1-2 are behind enveloped acceleration (SKF #01 §2
Fig. 1), so a route-band spectrum cannot reach them, and the failure mode this
suite exists to prevent is a report that quietly rounds "I cannot tell" down to
"early damage" -- or a drafted narrative that invents a stage outright.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vib_agent.agent.consistency import check_stage_language
from vib_agent.config import load_config, load_thresholds
from vib_agent.models import (
    AnalysisResult,
    ConfidenceFactor,
    DifferentialCandidate,
    FaultMatch,
    Finding,
    QualityGateResult,
    RcaResult,
    Reading,
)
from vib_agent.pdm_core.staging import (
    EARLY_STAGE_LIMITATION,
    STAGE_LABELS,
    classify_bearing_stage,
    staging_config,
)
from vib_agent.pipeline import run_analysis
from vib_agent.report.generate import render_markdown, staging_profile
from vib_agent.synth.generator import make_case

_REPO = Path(__file__).resolve().parents[1]
ROUTE = staging_profile("route")
STREAMING = staging_profile("streaming")


# ── builders ──────────────────────────────────────────────────────────────


def _reading(zone: str = "B") -> Reading:
    return Reading(
        ts="2026-08-12T00:00:00Z", machine_id="M1", mac="AA:BB", x_vel_mms=1.0,
        y_vel_mms=1.0, z_vel_mms=1.0, severity_rms=1.0, dominant_axis="y",
        iso_zone=zone, iso_severity="ok", threshold_source="iso_20816_3",
        threshold_note="", th_ab=1.0, th_bc=2.0, th_cd=3.0,
    )


def _match(
    fault: str = "bearing_outer_race", *, harmonics: list[str] | None = None,
    sidebands: list[float] | None = None, marginal: bool = False,
    confidence: str = "high",
) -> FaultMatch:
    factors = [ConfidenceFactor(name="base", detail="", delta=1.0)]
    if marginal:
        factors.append(ConfidenceFactor(name="amplitude_marginal", detail="near floor", delta=-2.0))
    return FaultMatch(
        fault=fault, description=fault, freq_hz=107.03, expected_hz=107.03, axis="y",
        confidence=confidence, confidence_evidence=factors, evidence="",
        harmonic_present=bool(harmonics),
        harmonics_by_axis={"y": harmonics} if harmonics is not None else None,
        sidebands=sidebands,
    )


def _result(
    *matches: FaultMatch, zone: str = "B", commit: bool = True,
    differential: list[DifferentialCandidate] | None = None,
) -> AnalysisResult:
    findings = (
        [Finding(fault=m.fault, severity="warn", confidence=m.confidence, reason="") for m in matches]
        if commit
        else []
    )
    return AnalysisResult(
        machine_id="M1", mac="AA:BB", ts="2026-08-12T00:00:00Z",
        quality_gate=QualityGateResult(overall="pass", checks=[], train_baseline=False),
        iso=_reading(zone),
        rca=RcaResult(status="ok", primary_findings=list(matches),
                      differential=differential or []),
        findings=findings,
    )


# ── the honesty floor ─────────────────────────────────────────────────────


class TestHonestyFloor:
    def test_the_floor_is_stated_verbatim_in_the_module_docstring(self):
        """The brief requires this sentence in the docstring, not just in docs."""
        import vib_agent.pdm_core.staging as staging

        doc = " ".join((staging.__doc__ or "").split())
        assert "Stages 1-2 are invisible to route-band spectra" in doc
        assert EARLY_STAGE_LIMITATION in doc
        assert "not_determinable" in doc

    def test_a_lone_unresolvable_tone_gets_no_stage(self):
        """No harmonics, no sidebands, sitting in the noise: we committed to the
        fault, but we cannot tell an early defect from an incidental peak."""
        est = classify_bearing_stage(_result(_match(marginal=True)), ROUTE)
        assert est.stage == "not_determinable"
        assert est.fault == "bearing_outer_race"       # block still renders
        assert EARLY_STAGE_LIMITATION in est.limitation

    def test_below_stage_three_is_never_called_an_early_stage(self):
        """The exact failure this module exists to prevent: 'cannot tell'
        rounding down to 'stage 1' or 'early damage'."""
        est = classify_bearing_stage(_result(_match(marginal=True)), ROUTE)
        assert est.stage != "stage_3_early"
        assert "stage 1" not in est.limitation.lower()
        assert "stage 2" not in est.limitation.lower()

    def test_not_determinable_is_not_a_clean_bill(self):
        est = classify_bearing_stage(_result(_match(marginal=True)), ROUTE)
        blob = (est.label + " " + est.limitation + " " + " ".join(est.evidence)).lower()
        for phrase in ("no damage", "healthy", "no action", "normal"):
            assert phrase not in blob, f"not_determinable reads as a clean bill: {phrase!r}"

    def test_the_vocabulary_is_exactly_the_four_values(self):
        assert set(STAGE_LABELS) == {
            "stage_3_early", "stage_3_advanced", "stage_4_suspected", "not_determinable",
        }


# ── classification ────────────────────────────────────────────────────────


class TestClassification:
    def test_a_discrete_matched_tone_is_stage_three_early(self):
        est = classify_bearing_stage(_result(_match()), ROUTE)
        assert est.stage == "stage_3_early"

    def test_a_harmonic_series_is_stage_three_advanced(self):
        est = classify_bearing_stage(_result(_match(harmonics=["2x", "3x", "4x"])), ROUTE)
        assert est.stage == "stage_3_advanced"

    def test_sidebands_alone_reach_stage_three_advanced(self):
        est = classify_bearing_stage(_result(_match(sidebands=[77.0, 137.0])), ROUTE)
        assert est.stage == "stage_3_advanced"

    def test_harmonics_are_counted_per_axis_not_pooled(self):
        """One order on each of three axes is not a harmonic series."""
        m = _match(harmonics=["2x"])
        m = m.model_copy(update={"harmonics_by_axis": {"x": ["2x"], "y": ["2x"], "z": ["2x"]}})
        assert classify_bearing_stage(_result(m), ROUTE).stage == "stage_3_early"

    @pytest.mark.parametrize("zone", ["C", "D"])
    def test_elevated_zone_plus_corroboration_is_stage_four_suspected(self, zone):
        est = classify_bearing_stage(
            _result(_match(sidebands=[77.0, 137.0]), zone=zone), ROUTE
        )
        assert est.stage == "stage_4_suspected"

    def test_stage_four_is_never_asserted_only_suspected(self):
        est = classify_bearing_stage(_result(_match(marginal=True), zone="D"), ROUTE)
        assert est.stage == "stage_4_suspected"
        assert est.label == "Stage 4 (suspected)"
        assert "suspected, never confirmed" in est.limitation

    def test_an_elevated_zone_alone_is_not_stage_four(self):
        """A quiet-spectrum fault on a noisy machine must not be upgraded on
        zone alone -- the corroboration rule."""
        est = classify_bearing_stage(
            _result(_match(harmonics=["2x", "3x", "4x"]), zone="D"), ROUTE
        )
        assert est.stage == "stage_3_advanced"

    @pytest.mark.parametrize("zone", ["A", "B"])
    def test_low_zone_never_reaches_stage_four(self, zone):
        est = classify_bearing_stage(
            _result(_match(sidebands=[77.0, 137.0]), zone=zone), ROUTE
        )
        assert est.stage == "stage_3_advanced"

    def test_stage_four_is_unreachable_without_a_velocity_based_zone(self):
        """Found in the CWRU sweep and pinned deliberately. Stage 4 keys on ISO
        zone C/D, and an acceleration-only upload has no assessable zone -- so
        stage 4 can NEVER fire on CWRU/MFPT/wind-turbine data, however rich the
        spectrum. That is the safe direction (it never over-claims), but it is a
        real limit on what this feature can say about acceleration-only files,
        and it should fail loudly if someone later wires zone around it.
        """
        est = classify_bearing_stage(
            _result(_match(sidebands=[77.0, 137.0], marginal=True), zone="not_assessable"),
            ROUTE,
        )
        assert est.stage == "stage_3_advanced"

    def test_an_unassessable_zone_is_never_named_in_the_evidence(self):
        """Regression: the first cut printed 'ISO zone ... not_assessable',
        which broke Session A's contract that an unrated reading carries zero
        ISO-zone language anywhere in the report. A zone that could not be
        assessed is the absence of a severity judgement, not evidence for one.
        """
        est = classify_bearing_stage(
            _result(_match(harmonics=["2x", "3x", "4x"]), zone="not_assessable"), ROUTE
        )
        blob = " ".join(est.evidence) + est.limitation
        assert "not_assessable" not in blob
        assert "ISO zone" not in blob

    def test_no_stage_without_a_committed_bearing_fault(self):
        est = classify_bearing_stage(_result(_match("imbalance")), ROUTE)
        assert est.fault is None and est.stage == "not_determinable"

    def test_a_differential_candidate_never_gets_a_stage(self):
        """We did not commit to the fault; staging it would smuggle a suppressed
        hypothesis back into the report through a side door."""
        est = classify_bearing_stage(
            _result(
                _match(harmonics=["2x", "3x", "4x"]),
                commit=False,
                differential=[DifferentialCandidate(
                    fault="bearing_outer_race", description="", confidence="medium",
                    adjudication="suppressed")],
            ),
            ROUTE,
        )
        assert est.fault is None

    def test_the_strongest_fault_carries_the_stage_deterministically(self):
        low = _match("bearing_inner_race", confidence="low")
        high = _match("bearing_outer_race", harmonics=["2x", "3x", "4x"], confidence="high")
        for order in ([low, high], [high, low]):
            est = classify_bearing_stage(_result(*order), ROUTE)
            assert est.fault == "bearing_outer_race" and est.stage == "stage_3_advanced"

    def test_it_derives_no_numbers_of_its_own(self):
        """Every evidence line restates a computed value."""
        est = classify_bearing_stage(_result(_match(sidebands=[77.0, 137.0])), ROUTE)
        joined = " ".join(est.evidence)
        assert "107.03" in joined and "2" in joined


# ── the NCD path is untouched ─────────────────────────────────────────────


class TestStreamingProfileIsUnaffected:
    def test_streaming_has_no_staging_block(self):
        assert staging_config(STREAMING) == {}

    def test_streaming_produces_no_stage_at_all(self):
        """Gated on the configured profile, not on the shape of the data:
        `sidebands` is None both when a spectrum had none and when the source
        was an NCD triplet, so absence cannot be read either way."""
        est = classify_bearing_stage(_result(_match(harmonics=["2x", "3x", "4x"])), STREAMING)
        assert est.stage == "not_determinable" and est.fault is None

    def test_the_ncd_report_renders_no_stage_section(self):
        thresholds = load_thresholds("route")
        case = make_case("bpfo", iso_table=load_config("iso_zones")["zones"],
                         thresholds=thresholds, seed=7)
        result = run_analysis(case, iso_table=load_config("iso_zones")["zones"],
                              thresholds=thresholds, rules=load_config("next_measurements"))
        md = render_markdown(result, case.machine, case=case, thresholds=thresholds,
                             profile="streaming")
        assert "Damage Stage Estimate" not in md


# ── report integration ────────────────────────────────────────────────────


class TestReportBlock:
    @staticmethod
    def _render(name: str, profile: str = "route") -> str:
        iso_table = load_config("iso_zones")["zones"]
        thresholds = load_thresholds(profile)
        case = make_case(name, iso_table=iso_table, thresholds=thresholds, seed=7)
        result = run_analysis(case, iso_table=iso_table, thresholds=thresholds,
                              rules=load_config("next_measurements"))
        return render_markdown(result, case.machine, case=case, thresholds=thresholds,
                               profile=profile)

    def test_rendered_only_when_a_bearing_fault_is_committed(self):
        assert "## Damage Stage Estimate" in self._render("bpfo")
        for clean in ("healthy", "imbalance", "looseness"):
            assert "Damage Stage Estimate" not in self._render(clean), clean

    def test_the_block_carries_evidence_and_a_limitation(self):
        md = self._render("bpfo")
        block = md.split("## Damage Stage Estimate", 1)[1].split("\n## ", 1)[0]
        assert "Evidence:" in block
        assert "- Committed fault:" in block
        assert EARLY_STAGE_LIMITATION in block

    def test_it_uses_the_classical_four_stage_vocabulary(self):
        block = self._render("bpfo").split("## Damage Stage Estimate", 1)[1]
        assert "Stage 3" in block or "Stage 4" in block or "Not determinable" in block

    def test_deterministic_and_drafted_paths_share_one_macro(self):
        """Byte-identical by construction: both templates import the same macro
        from _evidence.md.j2 and call the same pure function."""
        tpl = _REPO / "src/vib_agent/report/templates"
        for name in ("default_survey.md.j2", "drafted_evidence.md.j2"):
            assert "ev.damage_stage(damage_stage)" in (tpl / name).read_text(), name
        assert "macro damage_stage" in (tpl / "_evidence.md.j2").read_text()


# ── the consistency contract ──────────────────────────────────────────────


class TestStageLanguageContract:
    def test_a_fabricated_stage_is_caught(self):
        """THE regression this check exists for: the narrative names a stage on
        a machine we could not stage -- an unearned severity claim."""
        result = _result(_match(marginal=True))
        draft = "## Executive Summary\nThe bearing is in stage 4 of failure."
        assert check_stage_language(draft, result, ROUTE)

    def test_a_fabricated_stage_with_no_bearing_fault_at_all_is_caught(self):
        result = _result(_match("imbalance"))
        assert check_stage_language("The bearing shows stage 3 damage.", result, ROUTE)

    def test_a_qualitative_stage_claim_is_caught_too(self):
        result = _result(_match(marginal=True))
        assert check_stage_language("This is late-stage damage.", result, ROUTE)

    def test_the_wrong_stage_number_is_caught(self):
        result = _result(_match(), zone="B")            # -> stage_3_early
        assert check_stage_language("The bearing is at stage 4.", result, ROUTE)

    def test_the_right_stage_number_passes(self):
        result = _result(_match(), zone="B")
        assert check_stage_language("Consistent with stage 3 damage.", result, ROUTE) == []

    def test_our_own_limitation_wording_is_not_read_as_a_stage_claim(self):
        """The draft is SHOWN the deterministic report, so it carries our
        'early-stage detection requires...' sentence through verbatim. Flagging
        that would hard-fail every honest bearing report."""
        result = _result(_match(marginal=True))
        draft = f"No stage assigned; {EARLY_STAGE_LIMITATION}."
        assert check_stage_language(draft, result, ROUTE) == []

    def test_silence_about_the_stage_is_always_acceptable(self):
        for result in (_result(_match()), _result(_match(marginal=True)), _result(_match("imbalance"))):
            assert check_stage_language("Bearing wear was identified.", result, ROUTE) == []

    def test_it_is_wired_into_the_draft_check_path(self):
        loop = (_REPO / "src/vib_agent/agent/loop.py").read_text()
        assert "check_stage_language" in loop and "staging_profile" in loop


# ── config provenance ─────────────────────────────────────────────────────


class TestConfigProvenance:
    CFG = json.loads((_REPO / "config/staging.json").read_text())

    def test_route_only_streaming_absent(self):
        assert "route" in self.CFG["profiles"]
        assert "streaming" not in self.CFG["profiles"]

    def test_every_constant_carries_a_rationale_and_a_source(self):
        block = self.CFG["profiles"]["route"]["staging"]
        for key in (k for k in block if not k.startswith("_")):
            assert block.get(f"_rationale_{key}"), f"{key} has no _rationale_"
            assert block.get(f"_source_{key}"), f"{key} has no _source_"
            assert "INDEX #" in block[f"_source_{key}"], f"{key} source cites no INDEX entry"

    def test_the_constants_are_marked_unvalidated(self):
        """No document in the library publishes numeric four-stage criteria, so
        these are engineering rules -- they must not read as sourced numbers."""
        block = self.CFG["profiles"]["route"]["staging"]
        assert block["_verify"] is True
        assert "_verify_note" in block

    def test_the_honesty_floor_is_recorded_in_config_too(self):
        block = self.CFG["profiles"]["route"]["staging"]
        assert EARLY_STAGE_LIMITATION in block["_rule_honesty_floor"]

    def test_docs_staging_cites_library_entries_by_index_number(self):
        """Citations resolve against references/INDEX.md by number, per the
        References convention in CLAUDE.md — not by bare URL or memory."""
        doc = (_REPO / "docs/staging.md").read_text()
        for entry, publisher in (("#01", "SKF"), ("#02", "Timken"), ("#08", "Noria")):
            assert entry in doc and publisher in doc, f"{publisher} {entry} not cited"
        assert "Fig. 1" in doc                      # the specific figure, not just the doc
        # Whitespace-normalised: the doc is hard-wrapped, so the phrase spans lines.
        assert EARLY_STAGE_LIMITATION in " ".join(doc.split())

    def test_docs_records_that_the_library_lacks_numeric_stage_criteria(self):
        """The most important line in the doc: nobody should later mistake these
        constants for values transcribed from a published table."""
        doc = (_REPO / "docs/staging.md").read_text()
        assert "#47" in doc                          # the missing DTIC source
        assert "does NOT contain" in doc or "not contain" in doc

    def test_thresholds_json_was_not_touched(self):
        """Staging constants live in their own file; the production-validated
        calibration file gains nothing."""
        for profile in load_config("thresholds")["profiles"].values():
            assert "staging" not in profile

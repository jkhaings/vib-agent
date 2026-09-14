"""Unit tests for the echo-block parse/strip + field-by-field consistency
check (agent/consistency.py). This is the mechanism that keeps the model's
drafted narrative from silently drifting off the AnalysisResult.
"""

from __future__ import annotations

import pytest

from vib_agent.agent.consistency import (
    check_baseline_claims,
    ECHO_END,
    ECHO_START,
    TITLE_TEMPLATE,
    check_echo_against_result,
    check_title_line,
    check_zone_language,
    parse_echo_block,
    strip_echo_block,
)
from vib_agent.pipeline import run_analysis
from vib_agent.synth.generator import make_case


def _not_assessable_result(iso_table, thresholds, rules):
    case = make_case("bpfo", iso_table=iso_table, thresholds=thresholds, seed=1)
    sd = case.sensor_data.model_copy(
        update={"x_velocity_mm_sec": None, "y_velocity_mm_sec": None, "z_velocity_mm_sec": None}
    )
    case = case.model_copy(update={"sensor_data": sd})
    return run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)


def _echo_payload(result) -> dict:
    faults = [
        {"fault": f.fault, "confidence": f.confidence}
        for f in result.findings
        if f.fault != "no_significant_findings"
    ]
    measurements = [m.technique for m in result.recommended_measurements]
    zone = result.iso.iso_zone if result.iso else None
    return {"zone": zone, "faults": faults, "recommended_measurements": measurements}


class TestCheckTitleLine:
    def test_exact_title_passes(self):
        title = TITLE_TEMPLATE.format(machine_name="Synthetic Compressor 01")
        text = f"{title}\n\nReport body.\n\nDRAFT -- prepared by automated analysis, pending analyst review."
        assert check_title_line(text, "Synthetic Compressor 01") is None

    def test_preamble_before_title_is_flagged(self):
        title = TITLE_TEMPLATE.format(machine_name="Synthetic Compressor 01")
        text = f"Let me draft this report now.\n\n{title}\n\nReport body."
        mismatch = check_title_line(text, "Synthetic Compressor 01")
        assert mismatch is not None
        assert mismatch.startswith("title:")

    def test_wrong_machine_name_in_title_is_flagged(self):
        title = TITLE_TEMPLATE.format(machine_name="Wrong Machine")
        text = f"{title}\n\nReport body."
        assert check_title_line(text, "Synthetic Compressor 01") is not None

    def test_empty_text_is_flagged(self):
        assert check_title_line("", "Synthetic Compressor 01") is not None

    def test_leading_blank_lines_are_tolerated(self):
        title = TITLE_TEMPLATE.format(machine_name="Synthetic Compressor 01")
        text = f"\n\n  \n{title}\n\nReport body."
        assert check_title_line(text, "Synthetic Compressor 01") is None


class TestParseAndStrip:
    def test_parse_echo_block_extracts_json(self):
        text = (
            "Some narrative.\n\n<<<ECHO_START>>>\n"
            '{"zone": "B", "faults": [], "recommended_measurements": []}\n<<<ECHO_END>>>'
        )
        assert parse_echo_block(text) == {"zone": "B", "faults": [], "recommended_measurements": []}

    def test_parse_echo_block_tolerates_json_fence(self):
        text = (
            "<<<ECHO_START>>>\n```json\n"
            '{"zone": null, "faults": [], "recommended_measurements": []}\n```\n<<<ECHO_END>>>'
        )
        assert parse_echo_block(text) == {"zone": None, "faults": [], "recommended_measurements": []}

    def test_parse_echo_block_missing_returns_none(self):
        assert parse_echo_block("no echo block here") is None

    def test_parse_echo_block_malformed_json_returns_none(self):
        assert parse_echo_block("<<<ECHO_START>>>\nnot json\n<<<ECHO_END>>>") is None

    def test_strip_echo_block_removes_marker_and_payload(self):
        text = 'Report body.\n\n<<<ECHO_START>>>\n{"zone": "B"}\n<<<ECHO_END>>>'
        assert strip_echo_block(text) == "Report body."


class TestCheckEchoAgainstResult:
    def test_consistent_echo_yields_no_mismatches(self, iso_table, thresholds, rules):
        case = make_case("bpfo", iso_table=iso_table, thresholds=thresholds, seed=1)
        result = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
        assert check_echo_against_result(_echo_payload(result), result) == []

    def test_missing_echo_is_a_mismatch(self, iso_table, thresholds, rules):
        case = make_case("bpfo", iso_table=iso_table, thresholds=thresholds, seed=1)
        result = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
        assert check_echo_against_result(None, result) == ["missing or unparseable structured echo block"]

    def test_wrong_zone_is_flagged(self, iso_table, thresholds, rules):
        case = make_case("bpfo", iso_table=iso_table, thresholds=thresholds, seed=1)
        result = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
        echo = _echo_payload(result)
        echo["zone"] = "Z"
        assert any(m.startswith("zone:") for m in check_echo_against_result(echo, result))

    def test_wrong_confidence_is_flagged(self, iso_table, thresholds, rules):
        case = make_case("bpfo", iso_table=iso_table, thresholds=thresholds, seed=1)
        result = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
        echo = _echo_payload(result)
        assert echo["faults"], "bpfo case is expected to commit at least one fault"
        current = echo["faults"][0]["confidence"]
        echo["faults"][0]["confidence"] = "low" if current != "low" else "high"
        assert any(m.startswith("confidence for") for m in check_echo_against_result(echo, result))

    def test_missing_fault_is_flagged(self, iso_table, thresholds, rules):
        case = make_case("bpfo", iso_table=iso_table, thresholds=thresholds, seed=1)
        result = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
        echo = _echo_payload(result)
        echo["faults"] = []
        assert any(m.startswith("faults:") for m in check_echo_against_result(echo, result))

    def test_invented_measurement_is_flagged(self, iso_table, thresholds, rules):
        case = make_case("bpfo", iso_table=iso_table, thresholds=thresholds, seed=1)
        result = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
        echo = _echo_payload(result)
        echo["recommended_measurements"] = [*echo["recommended_measurements"], "Run to failure"]
        assert any(
            m.startswith("recommended_measurements:") for m in check_echo_against_result(echo, result)
        )

    def test_not_assessable_echo_zone_matches(self, iso_table, thresholds, rules):
        result = _not_assessable_result(iso_table, thresholds, rules)
        assert result.iso.iso_zone == "not_assessable"
        echo = _echo_payload(result)  # derives zone="not_assessable" from the result
        assert echo["zone"] == "not_assessable"
        assert check_echo_against_result(echo, result) == []


class TestCheckZoneLanguage:
    """HARD structural assertion: a not_assessable draft must name no ISO zone."""

    def test_clean_narrative_passes(self, iso_table, thresholds, rules):
        result = _not_assessable_result(iso_table, thresholds, rules)
        narrative = "Bearing outer-race fault, high confidence. ISO severity unrated per ISO 20816."
        assert check_zone_language(narrative, result) == []

    def test_iso_zone_phrase_is_flagged(self, iso_table, thresholds, rules):
        result = _not_assessable_result(iso_table, thresholds, rules)
        assert check_zone_language("The machine is in ISO Zone A.", result)

    def test_bare_zone_letter_is_flagged(self, iso_table, thresholds, rules):
        result = _not_assessable_result(iso_table, thresholds, rules)
        assert check_zone_language("Operating in Zone C at this time.", result)

    def test_assessable_result_is_a_noop(self, iso_table, thresholds, rules):
        # For a velocity-bearing (assessable) result, zone language is legitimate
        # and must NOT be flagged.
        case = make_case("bpfo", iso_table=iso_table, thresholds=thresholds, seed=1)
        result = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
        assert result.iso.iso_zone in ("A", "B", "C", "D")
        assert check_zone_language("The machine is in ISO Zone D.", result) == []

    def test_echo_carried_zone_is_not_flagged(self, iso_table, thresholds, rules):
        # The echo block legitimately carries zone="not_assessable"; the scan runs
        # on the stripped narrative, so the echo's own text must not trip it.
        result = _not_assessable_result(iso_table, thresholds, rules)
        text = (
            "Report body with no zone language.\n\n"
            f'{ECHO_START}\n{{"zone": "not_assessable", "faults": [], "recommended_measurements": []}}\n{ECHO_END}'
        )
        assert check_zone_language(text, result) == []


# ─────────────────────────────────────────────────────────────────────────
# Session D: narrative-level fault-term + confidence checks.
#
# RUN v5 regenerated a report for the MFPT healthy rig baseline whose echo block
# was honest (faults: []) while the published prose asserted "The committed
# diagnosis for MFPT-rig is Rotor imbalance, assessed at medium confidence" --
# a fabricated fault on a machine with none. Every check in place at the time
# passed it, because only the echo block and the zone wording were inspected.
# ─────────────────────────────────────────────────────────────────────────

from vib_agent.agent.consistency import (  # noqa: E402
    check_confidence_binding,
    check_fault_term_language,
)

# The fabrication, verbatim from outputs/field_validation/mfpt/baseline_1.pdf.
BASELINE_1_FABRICATION = """# Vibration Survey Report — MFPT-rig

## Executive Summary

The committed diagnosis for MFPT-rig is Rotor imbalance, assessed at medium
confidence. ISO 20816 severity is unrated — a velocity measurement is required to
establish it (see Severity & Coverage below).

## Diagnosis

### Rotor Imbalance — severity: unrated — ISO severity requires velocity data — Confidence: medium

1× shaft frequency (25.0 Hz) dominant on radial axis y; axial axis (x) quiet at
shaft frequency. This is the characteristic spectral signature of rotor imbalance.

DRAFT -- prepared by automated analysis, pending analyst review."""


def _healthy_result(iso_table, thresholds, rules):
    """A real no-findings AnalysisResult (the shape baseline_1 produces)."""
    case = make_case("healthy", iso_table=iso_table, thresholds=thresholds, seed=1)
    return run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)


class TestCheckFaultTermLanguage:
    def test_baseline_1_fabrication_is_caught(self, iso_table, thresholds, rules):
        """The RUN v5 regression, reproduced verbatim: honest echo, invented prose."""
        result = _healthy_result(iso_table, thresholds, rules)
        assert [f.fault for f in result.findings] == ["no_significant_findings"]
        mismatches = check_fault_term_language(BASELINE_1_FABRICATION, result)
        assert mismatches, "the fabricated Rotor imbalance diagnosis must be caught"
        assert "imbalance" in mismatches[0]
        assert "committed NO findings" in mismatches[0]

    def test_honest_no_findings_report_passes(self, iso_table, thresholds, rules):
        result = _healthy_result(iso_table, thresholds, rules)
        honest = (
            f"{TITLE_TEMPLATE.format(machine_name='X')}\n\n"
            "## Diagnosis\n\n**Committed diagnosis: none — parameters within normal range.**\n\n"
            "No fault signature matched in the spectral evidence. Bearing fault frequencies "
            "(BPFO, BPFI, BSF, FTF) were computed and checked; no peak aligned with them.\n"
        )
        assert check_fault_term_language(honest, result) == []

    def test_committed_fault_may_be_asserted(self, iso_table, thresholds, rules):
        case = make_case("bpfo", iso_table=iso_table, thresholds=thresholds, seed=1)
        result = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
        text = "The committed diagnosis is a bearing outer-race fault (BPFO).\n"
        assert check_fault_term_language(text, result) == []

    def test_asserting_a_fault_the_result_does_not_carry_is_caught(self, iso_table, thresholds, rules):
        case = make_case("bpfo", iso_table=iso_table, thresholds=thresholds, seed=1)
        result = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
        text = "The committed diagnosis is mechanical looseness.\n"
        mismatches = check_fault_term_language(text, result)
        assert mismatches and "mechanical_looseness" in mismatches[0]

    def test_reference_table_mentions_are_not_assertions(self, iso_table, thresholds, rules):
        """Every bearing report lists BPFO/BPFI/BSF/FTF as computed reference
        frequencies. Naming them there is not a diagnosis and must not trip."""
        result = _healthy_result(iso_table, thresholds, rules)
        text = (
            "## Bearing Fault Frequencies\n\n"
            "| Family | Hz |\n|---|---|\n| BPFO | 81.1 |\n| BPFI | 118.9 |\n"
            "| BSF | 35.7 |\n| FTF | 7.9 |\n\n"
            "The observed peaks do not correspond to bearing fault frequencies.\n"
        )
        assert check_fault_term_language(text, result) == []


class TestCheckConfidenceBinding:
    def test_upgraded_confidence_is_caught(self, iso_table, thresholds, rules):
        case = make_case("bpfo", iso_table=iso_table, thresholds=thresholds, seed=1)
        result = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
        committed = {f.fault: f.confidence for f in result.findings}
        assert committed.get("bearing_outer_race") == "high"
        text = "The bearing outer-race fault (BPFO) is reported at low confidence.\n"
        mismatches = check_confidence_binding(text, result)
        assert mismatches and "bearing_outer_race" in mismatches[0]

    def test_matching_confidence_passes(self, iso_table, thresholds, rules):
        case = make_case("bpfo", iso_table=iso_table, thresholds=thresholds, seed=1)
        result = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
        text = "The bearing outer-race fault (BPFO) is reported at high confidence.\n"
        assert check_confidence_binding(text, result) == []

    def test_technical_prose_is_not_a_confidence_claim(self, iso_table, thresholds, rules):
        """'low-frequency' / 'high-pass' must never read as a confidence level."""
        case = make_case("bpfo", iso_table=iso_table, thresholds=thresholds, seed=1)
        result = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
        text = (
            "A low-frequency peak near the outer-race family was removed by the high-pass "
            "filter; the low-speed shaft was unaffected.\n"
        )
        assert check_confidence_binding(text, result) == []


# ─────────────────────────────────────────────────────────────────────────
# Session F2: the differential-qualified headline.
#
# "Possible <X> with evidence of <Y> — further validation recommended" names two
# faults in two different roles. The contract is EXTENDED to read that shape, not
# loosened: X is still held to the committed bar, and Y must be a candidate the
# AnalysisResult actually raised. The baseline_1 fabrication regression above
# must stay green — a no-findings result cannot buy an assertion by qualifying it.
# ─────────────────────────────────────────────────────────────────────────

import csv  # noqa: E402

from vib_agent.adapters.uploads import parse_upload  # noqa: E402
from vib_agent.adapters.uploads.common import UploadForm  # noqa: E402
from vib_agent.config import load_config  # noqa: E402

_HEADLINE = ("Possible Bearing outer-race fault (BPFO) with evidence of Rotor imbalance "
             "— further validation recommended.")


def _conflicted_result(tmp_path, iso_table, thresholds, rules):
    """A REAL analysis (not a hand-built model) where the evidence points two
    ways: a dominant BPFO tone committed at high confidence, plus a strong 1×
    radial that pdm_core suppresses into the differential as imbalance."""
    fmax, n, shaft, bpfo = 400.0, 801, 30.0, 107.03
    freqs = [round(i * fmax / (n - 1), 2) for i in range(n)]
    amp = [0.006] * n
    for pk, a in ((shaft, 2.5), (2 * shaft, 0.45), (bpfo, 2.3), (2 * bpfo, 1.15),
                  (bpfo - shaft, 0.46), (bpfo + shaft, 0.46)):
        amp[min(range(n), key=lambda i: abs(freqs[i] - pk))] = a
    path = tmp_path / "conflicted.csv"
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["freq_hz", "amplitude"])
        for fr, a in zip(freqs, amp):
            w.writerow([fr, a])
    form = UploadForm(machine_alias="M", rpm=1800.0, iso_group="2", iso_support="rigid", machine_type="motor",
                      bearing_model="6206")
    case, _, _ = parse_upload(path, form, bearings_cfg=load_config("bearings"))
    return run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)


class TestQualifiedHeadline:
    def test_the_case_really_is_conflicted(self, tmp_path, iso_table, thresholds, rules):
        result = _conflicted_result(tmp_path, iso_table, thresholds, rules)
        assert {f.fault: f.confidence for f in result.findings} == {"bearing_outer_race": "high"}
        assert {d.fault: d.confidence for d in result.rca.differential} == {"imbalance": "medium"}

    def test_headline_with_committed_primary_and_raised_secondary_passes(
        self, tmp_path, iso_table, thresholds, rules
    ):
        result = _conflicted_result(tmp_path, iso_table, thresholds, rules)
        text = f"{TITLE_TEMPLATE.format(machine_name='M')}\n\n{_HEADLINE}\n"
        assert check_fault_term_language(text, result) == []

    def test_headline_inside_an_assertive_frame_resolves_to_the_committed_call(
        self, tmp_path, iso_table, thresholds, rules
    ):
        """The frame span holds BOTH fault terms. Before the qualified rule, the
        scan resolved the first lexicon hit and could read the corroborating
        candidate as the committed call — rejecting a correct report."""
        result = _conflicted_result(tmp_path, iso_table, thresholds, rules)
        text = (f"{TITLE_TEMPLATE.format(machine_name='M')}\n\n"
                f"The committed diagnosis is {_HEADLINE[len('Possible '):]}\n")
        assert "with evidence of" in text
        assert check_fault_term_language(text, result) == []

    def test_secondary_must_be_a_candidate_the_analysis_raised(
        self, tmp_path, iso_table, thresholds, rules
    ):
        result = _conflicted_result(tmp_path, iso_table, thresholds, rules)
        text = ("Possible Bearing outer-race fault (BPFO) with evidence of mechanical looseness "
                "— further validation recommended.\n")
        mismatches = check_fault_term_language(text, result)
        assert mismatches and "qualified headline" in mismatches[0]
        assert "mechanical_looseness" in mismatches[0]

    def test_primary_must_be_the_committed_call_not_the_candidate(
        self, tmp_path, iso_table, thresholds, rules
    ):
        """Swapping the roles is an upgrade of the differential, and is rejected."""
        result = _conflicted_result(tmp_path, iso_table, thresholds, rules)
        text = ("Possible Rotor imbalance with evidence of a bearing outer-race fault (BPFO) "
                "— further validation recommended.\n")
        mismatches = check_fault_term_language(text, result)
        assert mismatches and "DIFFERENTIAL candidate" in mismatches[0]
        assert "imbalance" in mismatches[0]

    def test_qualifying_a_fabrication_does_not_launder_it(self, iso_table, thresholds, rules):
        """The baseline_1 shape, dressed in the new wording: a no-findings result
        cannot assert a fault by calling it 'possible'."""
        result = _healthy_result(iso_table, thresholds, rules)
        text = ("Possible Rotor imbalance with evidence of a bearing outer-race fault "
                "— further validation recommended.\n")
        mismatches = check_fault_term_language(text, result)
        assert mismatches and "committed NO findings" in mismatches[0]

    def test_the_deterministic_report_emits_a_headline_the_checker_accepts(
        self, tmp_path, iso_table, thresholds, rules
    ):
        """End to end: the reference report the model drafts against carries the
        headline, and that exact text passes every narrative check."""
        from vib_agent.models import MachineMeta
        from vib_agent.report.generate import render_markdown

        result = _conflicted_result(tmp_path, iso_table, thresholds, rules)
        machine = MachineMeta(mac="UPLOAD-SPEC-M", name="M", active=True, type="motor",
                              iso_group="2", iso_support="rigid")
        md = render_markdown(result, machine)
        assert _HEADLINE in md
        assert check_fault_term_language(md, result) == []
        assert check_confidence_binding(md, result) == []
        assert check_zone_language(md, result) == []


class TestQualifiedHeadlineConfidence:
    def test_each_role_binds_its_own_confidence(self, tmp_path, iso_table, thresholds, rules):
        result = _conflicted_result(tmp_path, iso_table, thresholds, rules)
        text = ("Possible Bearing outer-race fault (BPFO) (high confidence) with evidence of "
                "Rotor imbalance (medium confidence) — further validation recommended.\n")
        assert check_confidence_binding(text, result) == []

    def test_an_upgraded_candidate_confidence_is_still_caught(
        self, tmp_path, iso_table, thresholds, rules
    ):
        result = _conflicted_result(tmp_path, iso_table, thresholds, rules)
        text = ("Possible Bearing outer-race fault (BPFO) (high confidence) with evidence of "
                "Rotor imbalance (high confidence) — further validation recommended.\n")
        mismatches = check_confidence_binding(text, result)
        assert mismatches and "imbalance" in mismatches[0]


# ── Session TFIX: the baseline-training decision ─────────────────────────


class TestBaselineClaims:
    """A shipped drafted sample said:

        "This reading was not used to update the machine's baseline, as ISO
         Zone D readings are excluded from baseline training."

    It is worth being exact about what was wrong with it, because the obvious
    reading is the wrong one. **The model did not hallucinate.** It was handed
    `quality_gate.train_baseline = false` and `train_reasons = ["iso_zone_D"]`
    in its ground-truth JSON, and `run_quality_gate` really does append
    `iso_zone_{zone}` for zones C and D — its docstring says it decides "whether
    Layer 2 may train its baseline on this reading". Every field in that
    sentence was read correctly.

    What is wrong is the SUBJECT. On a reading with no z-score layer there is no
    baseline: nothing was trained, nothing was updated, nothing was excluded,
    and no report on any path publishes that decision. So the guard forbids
    NARRATING it — not because the model invented it, but because it is an
    internal signal for a layer that did not run.
    """

    class _NoBaseline:
        zscore = None

    class _HasBaseline:
        zscore = object()

    SHIPPED = ("This reading was **not** used to update the machine's baseline, as ISO Zone D "
               "readings are excluded from baseline training.")

    def test_it_refuses_the_sentence_that_shipped(self):
        mismatches = check_baseline_claims(self.SHIPPED, self._NoBaseline())
        assert mismatches
        assert all("baseline claim" in m for m in mismatches)

    @pytest.mark.parametrize("phrasing", [
        "The baseline was updated with this reading.",
        "This measurement will be included in the baseline.",
        "Zone D readings are excluded from baseline training.",
        "The machine's baseline is trained on readings that pass the gate.",
        "This reading fed the baseline.",
    ])
    def test_it_refuses_the_paraphrases_too(self, phrasing):
        assert check_baseline_claims(phrasing, self._NoBaseline())

    @pytest.mark.parametrize("legitimate", [
        # the product's OWN limitation — a statement that there is NO baseline
        "Statistical (z-score) anomaly detection was not run: it requires a streaming baseline, "
        "which a single-file analysis does not carry.",
        # the cause library's field-measurement sense: the analyst's own records
        "Compare the first post-maintenance vibration reading with the pre-removal baseline.",
    ])
    def test_it_allows_the_two_legitimate_uses_in_the_same_report(self, legitimate):
        """Both of these appear in the very report this guard was written for.
        A blanket ban on the word would have refused the product's own prose."""
        assert check_baseline_claims(legitimate, self._NoBaseline()) == []

    def test_it_is_a_no_op_where_a_baseline_actually_exists(self):
        """The Welford z-score layer IS the baseline. On a streaming reading
        that carries one, the sentence is both true and meaningful."""
        assert check_baseline_claims(self.SHIPPED, self._HasBaseline()) == []

    def test_the_echo_block_is_not_scanned(self):
        """Same rule as every other prose check: the published narrative is what
        an analyst reads."""
        text = f"A clean narrative.\n\n{ECHO_START}\n{{\"note\": \"baseline updated\"}}\n{ECHO_END}"
        assert check_baseline_claims(text, self._NoBaseline()) == []

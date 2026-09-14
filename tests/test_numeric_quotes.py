"""Session H item 3 — the numeric-quote check.

The LLM never does math, but it does transcribe numbers, and a decimal-place
slip in the executive summary is invisible to every other consistency check:
the echo block stays honest, the fault name is right, the confidence matches.
That is the field-report typo class this check exists for.

The bar is two-sided. It must catch a 10x-off headline number, and it must not
fire on a correct report -- a false positive costs a retry and then a degrade
on work that was right.
"""

from __future__ import annotations

import pytest

from vib_agent.agent.consistency import (
    check_confidence_binding,
    check_fault_term_language,
    check_numeric_quotes,
    computed_numbers,
)
from vib_agent.pipeline import run_analysis
from vib_agent.synth.generator import make_case


@pytest.fixture
def bpfo(iso_table, thresholds, rules):
    case = make_case("bpfo", iso_table=iso_table, thresholds=thresholds, seed=1)
    return run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)


def _draft(summary: str) -> str:
    return (
        "# Vibration Survey Report — Synthetic Compressor 01\n\n"
        "## Executive Summary\n\n"
        f"{summary}\n\n"
        "## Diagnosis\n\n"
        "### Bearing outer-race fault (BPFO) — severity: danger, confidence: high\n\n"
        "Peak at 107.2 Hz on y matches the computed BPFO.\n"
    )


# ── the defect this check exists for ─────────────────────────────────────


class TestDecimalPlaceTypo:
    def test_ten_times_off_overall_is_caught(self, bpfo):
        """5.20 mm/s written as 52.0 mm/s: a Zone D reading reported as an
        order of magnitude worse than it is."""
        overall = bpfo.iso.severity_rms
        assert overall is not None
        typo = f"{overall * 10:.2f}"
        mismatches = check_numeric_quotes(
            _draft(f"Overall vibration measured {typo} mm/s on the y-axis."), bpfo
        )
        assert len(mismatches) == 1
        assert typo in mismatches[0]
        assert "not a value this analysis computed" in mismatches[0]

    def test_ten_times_off_the_other_way_is_caught(self, bpfo):
        overall = bpfo.iso.severity_rms
        mismatches = check_numeric_quotes(
            _draft(f"Overall vibration measured {overall / 10:.3f} mm/s."), bpfo
        )
        assert mismatches

    def test_a_glued_unit_does_not_hide_the_typo(self, bpfo):
        """"45.2mm/s" with no space is still a quoted measurement."""
        overall = bpfo.iso.severity_rms
        assert check_numeric_quotes(_draft(f"Overall {overall * 10:.2f}mm/s."), bpfo)
        assert check_numeric_quotes(_draft("A peak at 170.2Hz was matched."), bpfo)

    def test_transposed_fault_frequency_is_caught(self, bpfo):
        # 107.2 Hz written as 170.2 Hz -- a real frequency, just not this one.
        mismatches = check_numeric_quotes(
            _draft("A peak at 170.2 Hz matches the computed outer-race frequency."), bpfo
        )
        assert mismatches

    def test_every_bad_quote_is_reported_once(self, bpfo):
        mismatches = check_numeric_quotes(
            _draft("Overall 52.00 mm/s, peak 170.2 Hz, and again 52.00 mm/s."), bpfo
        )
        assert len(mismatches) == 2

    def test_metadata_numerals_do_not_whitelist_the_typo(self, bpfo):
        """Session NQ-TS: the whitelist is harvested from the whole result tree,
        and ts/mac/machine_id are plain strings -- so an analysis stamped at
        07:52 UTC put 52.0 in the allowed set (the minute is preceded by ":",
        which the number regex accepts) and THIS EXACT typo passed for one
        minute in every hour. Force every metadata field to carry the typo's
        digits: the typo must still be flagged, at any wall-clock time."""
        overall = bpfo.iso.severity_rms
        typo = f"{overall * 10:.2f}"  # 5.20 mm/s -> "52.00"
        stamp = "2026-08-25T07:52:11.520000+00:00"  # the minute that shipped the bug
        metadata = {
            "ts": stamp,
            "mac": f"SYN-{typo}-01",
            "machine_id": f"Compressor {typo}",
            "factory_id": f"FAC-{typo}",
            "factory_timezone": f"UTC+{typo}",
            "machine_type": f"compressor {typo}",
        }
        planted = bpfo.model_copy(
            update={
                "ts": stamp,
                "mac": metadata["mac"],
                "machine_id": metadata["machine_id"],
                "iso": bpfo.iso.model_copy(update=metadata),
            }
        )
        assert float(typo) not in computed_numbers(planted)
        mismatches = check_numeric_quotes(
            _draft(f"Overall vibration measured {typo} mm/s on the y-axis."), planted
        )
        assert len(mismatches) == 1
        assert typo in mismatches[0]


# ── correct reports stay green ───────────────────────────────────────────


class TestCorrectQuotesPass:
    def test_the_computed_overall_and_frequency_pass(self, bpfo):
        overall = bpfo.iso.severity_rms
        match = bpfo.rca.primary_findings[0]
        summary = (
            f"Overall vibration is {overall:.2f} mm/s. A peak at {match.freq_hz:.1f} Hz "
            f"matches the computed BPFO of {match.expected_hz:.2f} Hz."
        )
        assert check_numeric_quotes(_draft(summary), bpfo) == []

    def test_rounding_to_fewer_places_passes(self, bpfo):
        overall = bpfo.iso.severity_rms
        for digits in (0, 1, 2, 3):
            summary = f"Overall vibration is {overall:.{digits}f} mm/s."
            assert check_numeric_quotes(_draft(summary), bpfo) == [], f"{digits} dp rejected"

    def test_truncation_passes(self, bpfo):
        freq = bpfo.rca.primary_findings[0].freq_hz
        truncated = f"{int(freq * 10) / 10:.1f}"
        assert check_numeric_quotes(_draft(f"Peak at {truncated} Hz."), bpfo) == []

    def test_harmonics_of_computed_frequencies_pass(self, bpfo):
        """"2x BPFO at 214.1 Hz" is a multiple of a computed value, not new math."""
        base = bpfo.rca.bearing_freqs.BPFO
        summary = f"The 2× BPFO harmonic at {base * 2:.1f} Hz is also present."
        assert check_numeric_quotes(_draft(summary), bpfo) == []

    def test_shaft_orders_pass(self, bpfo):
        shaft = bpfo.rca.shaft_freq_hz
        summary = f"Shaft rate is {shaft:.2f} Hz; 3× sits at {shaft * 3:.2f} Hz."
        assert check_numeric_quotes(_draft(summary), bpfo) == []

    def test_iso_thresholds_pass(self, bpfo):
        summary = f"The C/D boundary for this machine is {bpfo.iso.th_cd:.1f} mm/s."
        assert check_numeric_quotes(_draft(summary), bpfo) == []

    def test_numbers_from_pdm_core_evidence_prose_pass(self, bpfo):
        """pdm_core writes its own sentences ("within 0.20% of computed BPFO");
        a report quoting one of those numbers is quoting a computed value."""
        allowed = computed_numbers(bpfo)
        assert 0.2 in allowed or any(abs(v - 0.2) < 1e-9 for v in allowed)

    def test_the_deterministic_report_summary_passes_its_own_check(
        self, iso_table, thresholds, rules
    ):
        """The template's executive summary is the reference the model is shown.
        If the check rejected that, it would reject every correct draft."""
        from vib_agent.report.generate import render_markdown

        for name in ("bpfo", "imbalance", "looseness", "machine_off"):
            case = make_case(name, iso_table=iso_table, thresholds=thresholds, seed=1)
            result = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
            md = render_markdown(result, case.machine, case=case, thresholds=thresholds)
            assert check_numeric_quotes(md, result) == [], name


# ── precision: what must NOT trip the check ──────────────────────────────


class TestNoFalsePositives:
    def test_standard_numbers_and_counts_are_not_measurements(self, bpfo):
        summary = (
            "Assessed per ISO 20816-3 with 2 additional findings over 30 days. "
            "See ISO 10816 and section 6.5.2 for the alarm rule."
        )
        assert check_numeric_quotes(_draft(summary), bpfo) == []

    def test_only_the_executive_summary_is_scanned(self, bpfo):
        """Later sections quote raw peak tables and drill-down detail; scoping
        the check keeps its false-positive cost at zero there."""
        draft = _draft("Overall vibration is within the reported range.")
        draft += "\n## Evidence\n\nA sideband at 999.9 Hz was noted.\n"
        assert check_numeric_quotes(draft, bpfo) == []

    def test_no_executive_summary_means_no_check(self, bpfo):
        assert check_numeric_quotes("# Title\n\nSome prose with 42.42 in it.\n", bpfo) == []

    def test_echo_block_is_not_scanned(self, bpfo):
        draft = _draft("Overall vibration is nominal.")
        draft += '\n<<<ECHO_START>>>\n```json\n{"zone": "D", "score": 99.99}\n```\n<<<ECHO_END>>>\n'
        assert check_numeric_quotes(draft, bpfo) == []


# ── the other consistency checks are untouched ───────────────────────────


class TestFabricationRegressionsStayGreen:
    def test_fabricated_fault_is_still_caught(self, iso_table, thresholds, rules):
        """The RUN v5 shape: a healthy machine, prose committing to a fault."""
        case = make_case("healthy", iso_table=iso_table, thresholds=thresholds, seed=1)
        result = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
        draft = _draft("The committed diagnosis is Rotor imbalance.")
        assert check_fault_term_language(draft, result)

    def test_confidence_upgrade_is_still_caught(self, bpfo):
        draft = _draft(
            "The committed diagnosis is a bearing outer-race fault, reported with low confidence."
        )
        assert check_confidence_binding(draft, bpfo)

    def test_a_correct_draft_trips_none_of_the_checks(self, bpfo):
        overall = bpfo.iso.severity_rms
        draft = _draft(
            f"Synthetic Compressor 01 is in ISO Zone D at {overall:.2f} mm/s. The committed "
            "diagnosis is Bearing outer-race fault (BPFO) (high confidence)."
        )
        assert check_numeric_quotes(draft, bpfo) == []
        assert check_fault_term_language(draft, bpfo) == []
        assert check_confidence_binding(draft, bpfo) == []

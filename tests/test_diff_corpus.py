"""DQ-0 — unit tests for scripts/eval/diff_corpus.py, the corpus zero-diff gate.

Synthetic JSONLs only: identical corpora must report TOTAL 0 and exit 0; a
single moved field must be named on a DIFF line and exit non-zero. Structural
breakage (mismatched keys, duplicates, dataset-error rows, bad JSON) must
refuse to compare rather than report a misleading total.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

# scripts/ is not a package, so the differ is loaded from its path (the
# test_beta_digest.py pattern; sys.modules registration before exec_module).
_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "eval" / "diff_corpus.py"
_spec = importlib.util.spec_from_file_location("diff_corpus", _SCRIPT)
diff_corpus = importlib.util.module_from_spec(_spec)
sys.modules["diff_corpus"] = diff_corpus
_spec.loader.exec_module(diff_corpus)


def _row(file: str, **overrides) -> dict:
    """A corpus row shaped like run_corpus.py's real output, scored fields
    populated, run-variant fields (ts, elapsed_s) included deliberately."""
    row = {
        "dataset": file.split("/")[0],
        "file": file,
        "stem": Path(file).stem,
        "ts": "2026-09-01T00:00:00+00:00",
        "gt_family": "outer",
        "gate": "warn",
        "gate_failed_checks": [],
        "gate_warn_checks": ["units_plausibility"],
        "iso_zone": "not_assessable",
        "iso_severity": "unrated",
        "severity_rms": None,
        "committed": ["bearing_outer_race"],
        "committed_bearing": ["bearing_outer_race"],
        "top_fault": "bearing_outer_race",
        "top_confidence": "high",
        "rca_status": "ok",
        "rca_primary": [
            {
                "fault": "bearing_outer_race",
                "confidence": "high",
                "freq_hz": 107.16,
                "expected_hz": 107.03,
                "axis": "y",
            }
        ],
        "differential": [],
        "shaft_hz": 29.93,
        "stage": "stage_3_advanced",
        "recommendations": ["Velocity measurement per ISO 20816 (10-1000 Hz broadband, mm/s RMS)"],
        "n_recommendations": 1,
        "status": "ok",
        "elapsed_s": 0.031,
    }
    row.update(overrides)
    return row


def _write(path: Path, rows: list[dict]) -> Path:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return path


def _corpus() -> list[dict]:
    return [
        _row("cwru/B007_0.mat"),
        _row("mfpt/1 - Three Baseline Conditions.mat", gate="pass", committed=[]),
        _row("wind_turbine/data-20130307T015746Z.mat", stage="none"),
    ]


class TestCleanComparison:
    def test_identical_corpora_report_total_zero_and_exit_zero(self, tmp_path, capsys):
        a = _write(tmp_path / "a.jsonl", _corpus())
        b = _write(tmp_path / "b.jsonl", _corpus())
        assert diff_corpus.main([str(a), str(b)]) == 0
        out = capsys.readouterr().out
        assert "TOTAL FIELD DIFFERENCES: 0" in out
        assert "same keys: True" in out
        assert "    DIFF " not in out

    def test_run_variant_fields_are_ignored(self, tmp_path, capsys):
        # ts and elapsed_s differ between any two runs; they must not count.
        a = _write(tmp_path / "a.jsonl", _corpus())
        moved = [
            _row_ | {"ts": "2026-09-02T12:34:56+00:00", "elapsed_s": 9.999}
            for _row_ in _corpus()
        ]
        b = _write(tmp_path / "b.jsonl", moved)
        assert diff_corpus.main([str(a), str(b)]) == 0
        assert "TOTAL FIELD DIFFERENCES: 0" in capsys.readouterr().out

    def test_every_scored_field_gets_a_summary_line(self, tmp_path, capsys):
        a = _write(tmp_path / "a.jsonl", _corpus())
        b = _write(tmp_path / "b.jsonl", _corpus())
        diff_corpus.main([str(a), str(b)])
        out = capsys.readouterr().out
        for field in diff_corpus.SCORED_FIELDS:
            assert f"{field:20s} 0 files differ" in out


class TestFieldDifferences:
    def test_one_moved_field_is_named_and_exits_nonzero(self, tmp_path, capsys):
        a = _write(tmp_path / "a.jsonl", _corpus())
        rows = _corpus()
        rows[0]["iso_zone"] = "D"
        b = _write(tmp_path / "b.jsonl", rows)
        assert diff_corpus.main([str(a), str(b)]) == 1
        out = capsys.readouterr().out
        assert "TOTAL FIELD DIFFERENCES: 1" in out
        assert "DIFF iso_zone cwru/B007_0.mat" in out
        assert '"not_assessable"' in out and '"D"' in out
        assert f"{'iso_zone':20s} 1 files differ" in out

    def test_multiple_diffs_sum_into_the_total(self, tmp_path, capsys):
        a = _write(tmp_path / "a.jsonl", _corpus())
        rows = _corpus()
        rows[0]["gate"] = "fail"
        rows[0]["committed"] = []
        rows[2]["stage"] = "stage_4_severe"
        b = _write(tmp_path / "b.jsonl", rows)
        assert diff_corpus.main([str(a), str(b)]) == 1
        assert "TOTAL FIELD DIFFERENCES: 3" in capsys.readouterr().out

    def test_nested_scored_fields_compare_deep(self, tmp_path, capsys):
        # rca_primary is a list of dicts; a float moving inside it must count.
        rows = _corpus()
        rows[0]["rca_primary"][0]["freq_hz"] = 107.17
        a = _write(tmp_path / "a.jsonl", _corpus())
        b = _write(tmp_path / "b.jsonl", rows)
        assert diff_corpus.main([str(a), str(b)]) == 1
        out = capsys.readouterr().out
        assert "DIFF rca_primary cwru/B007_0.mat" in out
        assert "TOTAL FIELD DIFFERENCES: 1" in out


class TestStructuralRefusal:
    def test_mismatched_key_sets_refuse_to_compare(self, tmp_path, capsys):
        a = _write(tmp_path / "a.jsonl", _corpus())
        b = _write(tmp_path / "b.jsonl", _corpus() + [_row("cwru/extra.mat")])
        assert diff_corpus.main([str(a), str(b)]) == 2
        out = capsys.readouterr().out
        assert "same keys: False" in out
        assert "ONLY IN NEW:      cwru/extra.mat" in out
        assert "TOTAL FIELD DIFFERENCES" not in out

    def test_duplicate_file_key_is_structural(self, tmp_path, capsys):
        a = _write(tmp_path / "a.jsonl", _corpus() + [_row("cwru/B007_0.mat")])
        b = _write(tmp_path / "b.jsonl", _corpus())
        assert diff_corpus.main([str(a), str(b)]) == 2
        assert "duplicate file key" in capsys.readouterr().out

    def test_dataset_error_row_is_structural(self, tmp_path, capsys):
        # run_corpus.py emits {"dataset": ds, "status": "dataset_error", ...}
        # with no "file" key on a dataset-level failure; such a run is not a
        # baseline and must not be silently compared.
        bad = _corpus() + [{"dataset": "mfpt", "status": "dataset_error", "error": "boom"}]
        a = _write(tmp_path / "a.jsonl", bad)
        b = _write(tmp_path / "b.jsonl", _corpus())
        assert diff_corpus.main([str(a), str(b)]) == 2
        assert "no 'file' key" in capsys.readouterr().out

    def test_bad_json_is_structural_with_line_number(self, tmp_path, capsys):
        a = tmp_path / "a.jsonl"
        a.write_text(json.dumps(_row("cwru/B007_0.mat")) + "\n{not json\n")
        b = _write(tmp_path / "b.jsonl", _corpus())
        assert diff_corpus.main([str(a), str(b)]) == 2
        assert "a.jsonl:2: bad JSON" in capsys.readouterr().out

    def test_missing_input_file_is_structural(self, tmp_path, capsys):
        b = _write(tmp_path / "b.jsonl", _corpus())
        assert diff_corpus.main([str(tmp_path / "nope.jsonl"), str(b)]) == 2
        assert "STRUCTURAL" in capsys.readouterr().out


class TestScoredFieldSet:
    def test_the_fourteen_pdmfix_fields_exactly(self):
        # The set is the SESSION_PDMFIX.md §"Corpus" comparison, verbatim —
        # a field added or dropped here changes what "zero-diff" certifies,
        # so the whole set is pinned.
        assert diff_corpus.SCORED_FIELDS == (
            "gate",
            "iso_zone",
            "iso_severity",
            "rca_status",
            "committed",
            "severity_rms",
            "n_recommendations",
            "committed_bearing",
            "rca_primary",
            "top_fault",
            "top_confidence",
            "stage",
            "differential",
            "recommendations",
        )

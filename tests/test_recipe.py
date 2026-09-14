"""Session G — the recipe schema, the deterministic executor, the bounded
sample, and the verification gate. No webapp, no LLM, no network anywhere in
this file: these are the pure pieces the inference lane is built on.
"""

from __future__ import annotations

import typing

import pytest
from pydantic import ValidationError

from tests.inference_corpus import ALL_CORPUS, CORPUS, G2_CORPUS, RPM, write_all, write_corpus
from vib_agent.adapters.uploads import parse_upload
from vib_agent.adapters.uploads.common import UploadForm
from vib_agent.adapters.uploads.recipe import (
    INFERRED_EXTENSIONS,
    ParseRecipe,
    RecipeExecutionError,
    execute_recipe,
)
from vib_agent.adapters.uploads.sample import (
    MAX_SAMPLE_BYTES,
    MAX_SAMPLE_LINES,
    NotTextError,
    read_text_sample,
    structure_fingerprint,
)
from vib_agent.adapters.uploads.verify import failed, verify_case
from vib_agent.config import load_config


@pytest.fixture(scope="module")
def bearings_cfg():
    return load_config("bearings")


def _form(**over):
    base = dict(machine_alias="Text Export", rpm=RPM, iso_group="2", iso_support="rigid", machine_type="motor",
                bearing_model="6206")
    base.update(over)
    return UploadForm(**base)


# ══════════════════════════════════════════════════════════════════════════
# 1 · The schema is a boundary, not a convenience type
# ══════════════════════════════════════════════════════════════════════════
class TestRecipeSchemaIsClosed:
    def test_no_field_accepts_free_text(self):
        """The property that makes an inferred recipe safe to execute: there is
        nowhere in it to put a path, a URL, a format string or an expression.
        Checked by introspection, so a future field cannot reopen the hole
        without failing here."""
        for name, field in ParseRecipe.model_fields.items():
            annotation = field.annotation
            args = typing.get_args(annotation)
            candidates = [a for a in (args or (annotation,)) if a is not type(None)]
            for candidate in candidates:
                if candidate is str:
                    pytest.fail(f"ParseRecipe.{name} accepts free text")
                if typing.get_origin(candidate) is list:
                    inner = typing.get_args(candidate)[0]
                    assert typing.get_origin(inner) is typing.Literal, (
                        f"ParseRecipe.{name} holds a list of unconstrained values")

    def test_unknown_fields_are_rejected_not_ignored(self):
        with pytest.raises(ValidationError):
            ParseRecipe(kind="spectrum", delimiter="comma", columns=["x", "amplitude"],
                        command="rm -rf /")  # type: ignore[call-arg]

    def test_enumerated_values_only(self):
        for bad in ({"delimiter": "../../etc/passwd"}, {"kind": "exec"},
                    {"amplitude_unit": "https://evil.example"}, {"x_unit": "$(whoami)"}):
            kwargs = {"kind": "spectrum", "delimiter": "comma", "columns": ["x", "amplitude"]}
            kwargs.update(bad)
            with pytest.raises(ValidationError):
                ParseRecipe(**kwargs)  # type: ignore[arg-type]

    def test_numeric_fields_are_bounded(self):
        for bad in ({"skip_rows": -1}, {"skip_rows": 10_000}, {"rpm_value": 0.0},
                    {"rpm_value": 1e9}, {"fs_value": -5.0}):
            kwargs = {"kind": "spectrum", "delimiter": "comma", "columns": ["x", "amplitude"],
                      "rpm_source": "file_header", "rpm_value": 1800.0}
            kwargs.update(bad)
            with pytest.raises(ValidationError):
                ParseRecipe(**kwargs)  # type: ignore[arg-type]

    def test_column_count_is_bounded(self):
        with pytest.raises(ValidationError):
            ParseRecipe(kind="spectrum", delimiter="comma", columns=["ignore"] * 64)

    def test_incoherent_recipes_are_rejected(self):
        with pytest.raises(ValidationError):  # spectrum with no frequency column
            ParseRecipe(kind="spectrum", delimiter="comma", columns=["amplitude"])
        with pytest.raises(ValidationError):  # two amplitude columns
            ParseRecipe(kind="spectrum", delimiter="comma", columns=["x", "amplitude", "amplitude"])
        with pytest.raises(ValidationError):  # spectrum whose x axis is time
            ParseRecipe(kind="spectrum", delimiter="comma", columns=["x", "amplitude"], x_unit="seconds")
        with pytest.raises(ValidationError):  # header rpm claimed, none given
            ParseRecipe(kind="spectrum", delimiter="comma", columns=["x", "amplitude"],
                        rpm_source="file_header")
        with pytest.raises(ValidationError):  # bare waveform with no sample rate
            ParseRecipe(kind="waveform", delimiter="comma", columns=["amplitude"])

    def test_describe_is_built_from_our_words_only(self):
        recipe = ParseRecipe(kind="spectrum", delimiter="comma", columns=["x", "amplitude"],
                             amplitude_unit="in_s", detection="peak")
        described = recipe.describe(form_rpm=1780.0)
        assert described["headline"] == "in/s peak spectrum"
        assert described["rpm"] == 1780.0 and described["rpm_from"] == "the form"
        assert described["severity_available"] is True


# ══════════════════════════════════════════════════════════════════════════
# 2 · The bounded sample
# ══════════════════════════════════════════════════════════════════════════
class TestBoundedSample:
    def test_sample_is_capped_in_bytes_and_lines(self, tmp_path):
        path = tmp_path / "big.txt"
        path.write_text("\n".join(f"{i}.0,{i}.500000000" for i in range(200_000)))
        sample = read_text_sample(path)
        assert len(sample.encode()) <= MAX_SAMPLE_BYTES
        assert len(sample.splitlines()) <= MAX_SAMPLE_LINES
        assert path.stat().st_size > 100 * MAX_SAMPLE_BYTES  # the file itself is far bigger

    def test_binary_is_refused(self, tmp_path):
        path = tmp_path / "thing.dat"
        path.write_bytes(bytes(range(256)) * 40)
        with pytest.raises(NotTextError):
            read_text_sample(path)

    def test_empty_is_refused(self, tmp_path):
        path = tmp_path / "empty.txt"
        path.write_text("   \n\n")
        with pytest.raises(NotTextError):
            read_text_sample(path)

    def test_windows_codepage_export_is_readable(self, tmp_path):
        path = tmp_path / "cp1252.txt"
        path.write_bytes("Amplitude µm ±2°\n1,0;2,0\n".encode("cp1252"))
        assert "µm" in read_text_sample(path)

    def test_fingerprint_is_structure_only(self, tmp_path):
        a = "Speed: 1800 RPM\nHz,Amp\n10.0,0.5\n20.0,0.7\n"
        b = "Speed: 3600 RPM\nHz,Amp\n11.5,0.9\n25.0,1.2\n"
        c = "Speed: 1800 RPM\nHz;Amp\n10.0;0.5\n20.0;0.7\n"
        assert structure_fingerprint(a) == structure_fingerprint(b)  # values differ, shape same
        assert structure_fingerprint(a) != structure_fingerprint(c)  # delimiter differs

    def test_fingerprint_carries_no_readings(self, tmp_path):
        sample = "Machine: BOILER FEED PUMP 2A\nSpeed: 1785 RPM\n10.0,0.512\n"
        fingerprint = structure_fingerprint(sample)
        assert len(fingerprint) == 32 and all(ch in "0123456789abcdef" for ch in fingerprint)
        for leak in ("1785", "0.512", "BOILER"):
            assert leak not in fingerprint


# ══════════════════════════════════════════════════════════════════════════
# 3 · Deterministic execution over the whole corpus
# ══════════════════════════════════════════════════════════════════════════
class TestExecuteCorpus:
    @pytest.fixture(scope="class")
    def corpus_dir(self, tmp_path_factory):
        directory = tmp_path_factory.mktemp("corpus")
        write_all(directory)
        return directory

    def test_corpus_covers_at_least_ten_shapes(self):
        assert len(CORPUS) >= 10
        assert len(G2_CORPUS) >= 8
        # txt, dat, asc, and (Session G2) the tabular fallback formats
        assert {item.name.rsplit(".", 1)[1] for item in ALL_CORPUS} == {
            "txt", "dat", "asc", "xlsx", "csv"}

    @pytest.mark.parametrize("item", ALL_CORPUS, ids=[item.name for item in ALL_CORPUS])
    def test_each_file_executes_and_lands_on_the_right_physics(self, item, corpus_dir, bearings_cfg):
        path = corpus_dir / item.name
        case, kind, note = execute_recipe(path, item.recipe, _form(), bearings_cfg=bearings_cfg)

        assert kind == f"inferred_{item.recipe.kind}"
        if item.recipe.kind == "trend":
            assert case.history and len(case.history) >= 3
            return

        spectrum = case.spectra["y"]
        assert len(spectrum.freq_hz) > 100
        # every x axis (Hz, CPM, orders) must land back on real frequencies
        peak_hz = max(zip(spectrum.freq_hz, spectrum.amplitude), key=lambda p: p[1])[0]
        assert peak_hz > 0

        if item.severity_expected:
            assert case.sensor_data.y_velocity_mm_sec is not None
            assert "severity" in (case.validation_scope or [])
        else:
            # honest refusal: acceleration/displacement/unknown units never carry
            # an ISO severity claim
            assert case.sensor_data.y_velocity_mm_sec is None
            assert case.validation_scope == ["rca"]
            assert "severity" in note.lower()

    @pytest.mark.parametrize("item", [i for i in ALL_CORPUS if i.recipe.kind == "spectrum"],
                             ids=[i.name for i in ALL_CORPUS if i.recipe.kind == "spectrum"])
    def test_spectra_recover_the_running_speed_line(self, item, corpus_dir, bearings_cfg):
        """The proof that unit handling is right: whatever the x axis was
        written in, the 1x line must come back at 30 Hz."""
        case, _, _ = execute_recipe(corpus_dir / item.name, item.recipe, _form(),
                                    bearings_cfg=bearings_cfg)
        spectrum = case.spectra["y"]
        low = [(f, a) for f, a in zip(spectrum.freq_hz, spectrum.amplitude) if 0 < f <= 45]
        dominant = max(low, key=lambda p: p[1])[0]
        assert abs(dominant - RPM / 60.0) < 1.0, f"{item.name}: 1x landed at {dominant} Hz"

    def test_dispatch_routes_the_new_extensions_through_the_recipe(self, corpus_dir, bearings_cfg):
        item = CORPUS[0]
        case, kind, _ = parse_upload(corpus_dir / item.name, _form(), bearings_cfg=bearings_cfg,
                                     recipe=item.recipe)
        assert kind.startswith("inferred_") and case.spectra

    def test_dispatch_refuses_an_inferred_extension_with_no_recipe(self, corpus_dir, bearings_cfg):
        from vib_agent.adapters.uploads import UnsupportedFormatError

        with pytest.raises(UnsupportedFormatError):
            parse_upload(corpus_dir / CORPUS[0].name, _form(), bearings_cfg=bearings_cfg)

    def test_a_recipe_that_does_not_fit_fails_loudly(self, corpus_dir, bearings_cfg):
        wrong = ParseRecipe(kind="spectrum", delimiter="semicolon", columns=["x", "amplitude"],
                            x_unit="hz", amplitude_unit="mm_s")
        with pytest.raises(RecipeExecutionError):
            execute_recipe(corpus_dir / "bare_pairs.txt", wrong, _form(), bearings_cfg=bearings_cfg)


# ══════════════════════════════════════════════════════════════════════════
# 4 · The verification gate
# ══════════════════════════════════════════════════════════════════════════
TOLERANCES = {"speed_tolerance_pct": 5.0, "max_bin_ratio": 8.0, "max_amplitude": 1.0e6}


class TestVerificationGate:
    @pytest.fixture(scope="class")
    def corpus_dir(self, tmp_path_factory):
        directory = tmp_path_factory.mktemp("corpus_verify")
        write_all(directory)
        return directory

    @pytest.mark.parametrize("item", ALL_CORPUS, ids=[item.name for item in ALL_CORPUS])
    def test_every_correctly_read_file_passes(self, item, corpus_dir, bearings_cfg):
        case, _, _ = execute_recipe(corpus_dir / item.name, item.recipe, _form(),
                                    bearings_cfg=bearings_cfg)
        assert failed(verify_case(case, rpm=RPM, tolerances=TOLERANCES)) == []

    def test_a_hz_axis_misread_as_cpm_is_caught(self, corpus_dir, bearings_cfg):
        """The exact failure the gate exists for: a 60x unit error produces a
        plausible-looking spectrum with the 1x line in the wrong place."""
        item = next(i for i in CORPUS if i.name == "bare_pairs.txt")
        misread = item.recipe.model_copy(update={"x_unit": "cpm"})
        case, _, _ = execute_recipe(corpus_dir / item.name, misread, _form(),
                                    bearings_cfg=bearings_cfg)
        names = [c.name for c in failed(verify_case(case, rpm=RPM, tolerances=TOLERANCES))]
        assert "inferred_speed_presence" in names

    def test_an_amplitude_column_read_as_frequency_is_caught(self, corpus_dir, bearings_cfg):
        item = next(i for i in CORPUS if i.name == "bare_pairs.txt")
        swapped = item.recipe.model_copy(update={"columns": ["amplitude", "x"]})
        case, _, _ = execute_recipe(corpus_dir / item.name, swapped, _form(),
                                    bearings_cfg=bearings_cfg)
        assert failed(verify_case(case, rpm=RPM, tolerances=TOLERANCES))

    def test_absurd_amplitudes_are_caught(self, bearings_cfg):
        from vib_agent.models import Case, MachineMeta, SensorData, Spectrum

        spectrum = Spectrum(freq_hz=[i * 0.5 for i in range(1, 200)],
                            amplitude=[1.0e9] * 199, fmax_hz=100.0, kind="velocity")
        case = Case(name="M", machine=MachineMeta(mac="M", name="M", active=True, type="motor"),
                    sensor_data=SensorData(rpm=RPM), spectra={"y": spectrum}, source="upload")
        names = [c.name for c in failed(verify_case(case, rpm=RPM, tolerances=TOLERANCES))]
        assert "inferred_amplitude_range" in names

    def test_tolerances_come_from_config_not_code(self):
        inference_cfg = load_config("webapp").get("inference")
        assert inference_cfg is not None, "config/webapp.json must carry the inference tolerances"
        for key in ("speed_tolerance_pct", "max_bin_ratio", "max_amplitude"):
            assert key in inference_cfg


def test_inferred_extensions_are_exactly_the_three_text_formats():
    assert INFERRED_EXTENSIONS == (".txt", ".dat", ".asc")


# ══════════════════════════════════════════════════════════════════════════
# Session G2 — the weird-but-real shapes, each asserted for what makes it hard
# ══════════════════════════════════════════════════════════════════════════
class TestG2Shapes:
    @pytest.fixture(scope="class")
    def corpus_dir(self, tmp_path_factory):
        directory = tmp_path_factory.mktemp("corpus_g2")
        write_all(directory)
        return directory

    def _run(self, corpus_dir, name, bearings_cfg, recipe=None):
        item = next(i for i in ALL_CORPUS if i.name == name)
        case, kind, note = execute_recipe(corpus_dir / name, recipe or item.recipe, _form(),
                                          bearings_cfg=bearings_cfg)
        return item, case, kind, note

    def test_transposed_rows_are_turned_upright(self, corpus_dir, bearings_cfg):
        _, case, _, _ = self._run(corpus_dir, "transposed_rows.txt", bearings_cfg)
        spectrum = case.spectra["y"]
        assert len(spectrum.freq_hz) > 700          # every point, not just a few
        assert spectrum.freq_hz == sorted(spectrum.freq_hz)
        # the label cell that begins each row is dropped, not read as a value
        assert all(f >= 0 for f in spectrum.freq_hz)

    def test_a_transposed_trend_is_refused_by_the_schema(self):
        with pytest.raises(ValidationError):
            ParseRecipe(kind="trend", delimiter="tab", orientation="rows",
                        columns=["timestamp", "value"])

    def test_thousands_separators_do_not_become_decimal_points(self, corpus_dir, bearings_cfg):
        """1,800.00 CPM is 30 Hz. Read without the separator rule it becomes
        1.8 CPM, and the whole axis collapses — which the gate would catch, but
        the point is to read it correctly."""
        _, case, _, _ = self._run(corpus_dir, "thousands.txt", bearings_cfg)
        spectrum = case.spectra["y"]
        low = [(f, a) for f, a in zip(spectrum.freq_hz, spectrum.amplitude) if 0 < f <= 45]
        assert abs(max(low, key=lambda p: p[1])[0] - RPM / 60.0) < 1.0

    def test_units_row_under_the_header_is_skipped(self, corpus_dir, bearings_cfg):
        _, case, _, _ = self._run(corpus_dir, "units_row.dat", bearings_cfg)
        assert len(case.spectra["y"].freq_hz) > 700  # no row lost to the [Hz] line

    def test_a_second_data_column_is_declared_not_silently_dropped(self, corpus_dir, bearings_cfg):
        """One recipe reads ONE channel. The other column is marked `ignore`, and
        the confirm card is told how many were ignored so the analyst can decide
        to upload it as a second channel instead."""
        item, case, _, _ = self._run(corpus_dir, "two_channels.txt", bearings_cfg)
        assert item.recipe.describe(form_rpm=RPM)["ignored_columns"] == 1
        assert len(case.spectra["y"].freq_hz) > 700

    def test_xlsx_is_read_from_stored_cell_values(self, corpus_dir, bearings_cfg):
        _, case, kind, _ = self._run(corpus_dir, "multi_header.xlsx", bearings_cfg)
        assert kind == "inferred_spectrum"
        assert len(case.spectra["y"].freq_hz) > 700

    def test_xlsx_order_axis_and_inch_units_both_convert(self, corpus_dir, bearings_cfg):
        _, case, _, _ = self._run(corpus_dir, "orders.xlsx", bearings_cfg)
        spectrum = case.spectra["y"]
        low = [(f, a) for f, a in zip(spectrum.freq_hz, spectrum.amplitude) if 0 < f <= 45]
        assert abs(max(low, key=lambda p: p[1])[0] - RPM / 60.0) < 1.0
        assert case.sensor_data.y_velocity_mm_sec is not None   # in/s peak -> mm/s RMS

    def test_a_spreadsheet_formula_is_never_evaluated(self, tmp_path, bearings_cfg):
        """openpyxl is opened with data_only=True: what is read is the value the
        spreadsheet stored, never the formula text and never a recomputation."""
        import openpyxl

        from vib_agent.adapters.uploads.sample import read_xlsx_sample

        path = tmp_path / "formula.xlsx"
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.append(["Hz", "Amp"])
        sheet.append([10.0, "=1+1"])       # stored as a formula string, no cached value
        workbook.save(path)
        sample = read_xlsx_sample(path)
        assert "=1+1" not in sample  # data_only -> the uncached formula reads as empty

    def test_a_csv_the_template_cannot_read_still_executes_here(self, corpus_dir, bearings_cfg):
        """The fallback lane's reason to exist: a real CSV our template rejects."""
        from vib_agent.adapters.uploads.tabular import parse_spectrum

        # The template genuinely cannot read it. The exception type varies with
        # HOW it fails (missing columns, unparseable values, a header that
        # collapses to one field), which is exactly why the webapp's fallback
        # triggers on any parse failure rather than on one error class.
        with pytest.raises(Exception):
            parse_spectrum(corpus_dir / "german_headers.csv", _form(), bearings_cfg=bearings_cfg)
        _, case, _, _ = self._run(corpus_dir, "german_headers.csv", bearings_cfg)
        assert case.sensor_data.y_velocity_mm_sec is not None

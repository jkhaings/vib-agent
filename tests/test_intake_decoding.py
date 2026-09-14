"""INTAKE-HARDEN, fix 1 — an upload is decoded by what it declares, not by
hoping it is UTF-8.

Two defects, one root cause: the sample stage guessed from a three-codec list
that had no UTF-16 in it, and the executor did not consult the list at all
(a bare `read_text(encoding="utf-8", errors="replace")`).

  * a UTF-16 file — which is exactly what Excel's "Unicode Text (*.txt)" Save As
    produces — decoded as cp1252 into NUL-laden text, was correctly classed
    binary, and was turned away as corrupt. An ordinary export, refused.
  * a UTF-8 BOM survived into the executor's first field, so 'BOM+10.0' failed
    float() and row one vanished. No error, no note; just 500 rows where the
    file had 501. Invisible unless you counted.

Both are decided by BYTES, so the fixtures are committed bytes, not strings
built at test time.
"""

from __future__ import annotations

import codecs

import pytest

from tests.intake_adversarial_corpus import BY_NAME, FIXTURE_DIR, RPM, SHAFT_HZ
from vib_agent.adapters.uploads.common import UploadForm
from vib_agent.adapters.uploads.recipe import execute_recipe
from vib_agent.adapters.uploads.sample import (
    NotTextError,
    decode_upload_bytes,
    read_sample,
    read_text_sample,
)
from vib_agent.config import load_config


@pytest.fixture(scope="module")
def bearings_cfg():
    return load_config("bearings")


def _form():
    return UploadForm(machine_alias="Decode", rpm=RPM, iso_group="2", iso_support="rigid", machine_type="motor",
                      bearing_model="6206")


def _run(name, bearings_cfg):
    return execute_recipe(FIXTURE_DIR / name, BY_NAME[name].recipe, _form(),
                          bearings_cfg=bearings_cfg)


# ══════════════════════════════════════════════════════════════════════════
# The decoder itself
# ══════════════════════════════════════════════════════════════════════════
class TestDecoder:
    @pytest.mark.parametrize("codec,bom", [
        ("utf-8", codecs.BOM_UTF8),
        ("utf-16-le", codecs.BOM_UTF16_LE),
        ("utf-16-be", codecs.BOM_UTF16_BE),
        ("utf-32-le", codecs.BOM_UTF32_LE),
        ("utf-32-be", codecs.BOM_UTF32_BE),
    ])
    def test_a_bom_is_believed_and_stripped(self, codec, bom):
        text = "Hz,Amp\n30.0,1.1\n"
        assert decode_upload_bytes(bom + text.encode(codec)) == text

    def test_utf32_le_is_not_mistaken_for_utf16_le(self):
        """FF FE 00 00 starts with FF FE. Checked in the wrong order, a UTF-32
        file decodes as UTF-16 and every character comes back followed by a NUL
        — which then reads as binary and gets the file refused."""
        raw = codecs.BOM_UTF32_LE + "30.0,1.1\n".encode("utf-32-le")
        assert decode_upload_bytes(raw) == "30.0,1.1\n"
        assert "\x00" not in decode_upload_bytes(raw)

    def test_cp1252_high_bytes_still_decode(self):
        raw = "Bearing temp: 45\xb0C \xb1 2\xb0, \xb5m\n".encode("cp1252")
        assert "45\xb0C" in decode_upload_bytes(raw)

    def test_a_partial_final_character_is_dropped_not_fatal(self):
        """The sample is a bounded read, so it can cut a multi-byte character in
        half. That costs the partial character, never the file."""
        full = "temperature 45°C measured\n".encode("utf-8")
        cut = full[:15]                      # slices the middle of the 2-byte °
        assert decode_upload_bytes(cut).startswith("temperature 45")

    def test_a_mismatch_in_the_body_is_not_forgiven(self):
        """The truncation allowance is for the END of a bounded read only — it
        must not become a general 'ignore bad bytes' path. 0x81 is a UTF-8
        continuation byte with nothing to continue and is undefined in cp1252,
        so a file carrying one 300 bytes in is not text in any of our codecs."""
        raw = b"a" * 300 + b"\x81" + b"a" * 300
        with pytest.raises(NotTextError):
            decode_upload_bytes(raw)

    def test_lenient_mode_never_raises(self):
        """The executor has already been told the file is readable; a junk tail
        must cost the rows it occupies, not the upload."""
        raw = b"30.0,1.1\n" + bytes(range(256)) * 4
        text = decode_upload_bytes(raw, lenient=True)
        assert text.startswith("30.0,1.1")

    def test_bomless_utf16_is_refused_rather_than_guessed(self, tmp_path):
        """Without a mark, UTF-16 is genuinely ambiguous, and this is where that
        refusal actually happens: ASCII encoded as UTF-16 LE is *valid UTF-8*
        (a NUL is a legal UTF-8 character), so the decoder cannot object — the
        binary check downstream of it is what catches the NULs. Asserted at the
        sample boundary, because that is the layer the product relies on."""
        path = tmp_path / "nobom.txt"
        path.write_bytes("Hz,Amp\n30.0,1.1\n".encode("utf-16-le"))     # no BOM
        assert "\x00" in decode_upload_bytes(path.read_bytes())
        with pytest.raises(NotTextError):
            read_text_sample(path)


# ══════════════════════════════════════════════════════════════════════════
# The two defects, at the product boundary
# ══════════════════════════════════════════════════════════════════════════
class TestUtf16IsAnOrdinaryExport:
    @pytest.mark.parametrize("name", ["utf16le_bom.txt", "utf16be_bom.txt"])
    def test_the_sample_stage_accepts_it(self, name):
        sample = read_sample(FIXTURE_DIR / name)
        assert "30.0000" in sample, "the model would never see a readable excerpt"
        assert "\x00" not in sample

    @pytest.mark.parametrize("name", ["utf16le_bom.txt", "utf16be_bom.txt"])
    def test_the_executor_reads_every_row(self, name, bearings_cfg):
        case, kind, _ = _run(name, bearings_cfg)
        spectrum = case.spectra["y"]
        assert kind == "inferred_spectrum"
        assert len(spectrum.freq_hz) == 501
        low = [(f, a) for f, a in zip(spectrum.freq_hz, spectrum.amplitude) if 0 < f <= 45]
        assert abs(max(low, key=lambda p: p[1])[0] - SHAFT_HZ) < 1.0


class TestABomDoesNotEatTheFirstReading:
    def test_every_row_of_a_headerless_bom_file_survives(self, bearings_cfg):
        """The regression this is here for: 500 rows out of 501, silently.
        The count is the assertion — nothing else about the report looked wrong."""
        case, _, _ = _run("utf8_bom_nohdr.txt", bearings_cfg)
        assert len(case.spectra["y"].freq_hz) == 501
        assert case.spectra["y"].freq_hz[0] == 0.0     # the very first datum, not the second

    def test_the_sample_and_the_executor_agree_on_row_one(self, bearings_cfg):
        """The split this product is built on is that a model describes the
        LAYOUT and only code reads the NUMBERS. That only holds if both stages
        are looking at the same characters."""
        sample = read_text_sample(FIXTURE_DIR / "utf8_bom_nohdr.txt")
        first_sampled = sample.splitlines()[0].split(",")[0]
        case, _, _ = _run("utf8_bom_nohdr.txt", bearings_cfg)
        assert float(first_sampled) == case.spectra["y"].freq_hz[0]


class TestNothingElseMoved:
    """The decode change touches every text upload, so the shapes that already
    worked are re-checked here, not assumed."""

    @pytest.mark.parametrize("name", [
        "cp1252_degrees.txt", "crlf.txt", "bare_cr.dat", "binary_tail.txt",
        "comments_blanks.dat", "scientific.txt",
    ])
    def test_previously_readable_files_still_read(self, name, bearings_cfg):
        case, _, _ = _run(name, bearings_cfg)
        assert len(case.spectra["y"].freq_hz) == 501

    def test_a_binary_file_is_still_refused_before_inference(self):
        with pytest.raises(NotTextError):
            read_sample(FIXTURE_DIR / "binary_head.txt")

    def test_an_empty_file_is_still_refused(self, tmp_path):
        path = tmp_path / "empty.txt"
        path.write_text("   \n\n")
        with pytest.raises(NotTextError):
            read_text_sample(path)

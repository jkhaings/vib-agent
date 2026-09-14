"""Session UX-2 — the in-browser spectrum preview.

The behaviour runs in node against the shipped `app.js` and the 26-file
adversarial corpus (`tests/js/spectrum_preview_tests.js`). This file runs that
suite and adds the claims that are about SOURCE — the ones a passing browser
test would leave true while the promise underneath went stale.

The promise: **no analysis number is born in the browser.** The preview draws
the file the analyst chose so they can see they picked the right one. Every
number the product commits to is computed by tested Python on the server, and a
browser-drawn figure that reads like a verdict is the failure this feature could
most easily become.

This file never skips, except the node runner when node is absent.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import vib_agent.webapp as webapp_pkg

_ROOT = Path(__file__).resolve().parents[1]
_STATIC = Path(webapp_pkg.__file__).parent / "static"
_CORPUS = _ROOT / "tests" / "fixtures" / "intake_adversarial"


def _app_js() -> str:
    return (_STATIC / "app.js").read_text()


def _preview_block() -> str:
    """The preview section, from its banner to the next one."""
    body = _app_js().partition("// ── the preview: the file, drawn here")[2]
    assert body, "the preview block is gone"
    return body.partition("\n// ── card renderers")[0]


def _preview_code() -> str:
    """The same block with its COMMENTS removed.

    The word bans below have to read code, not prose: this block's comment
    opens by saying "no FFT, no severity, no zone, no ISO anything", which is
    the promise the bans exist to enforce and would fail every one of them.
    A test that cannot tell a promise from its violation is worse than no test.
    """
    out = []
    for line in _preview_block().splitlines():
        stripped = line.strip()
        if stripped.startswith(("//", "*", "/**", "/*")):
            continue
        out.append(line.partition("  // ")[0])
    return "\n".join(out)


# ══════════════════════════════════════════════════════════════════════════
# 1 · The behaviour, against the corpus, in a real JS runtime
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_preview_against_the_adversarial_corpus():
    result = subprocess.run(
        [shutil.which("node"), str(_ROOT / "tests" / "js" / "spectrum_preview_tests.js")],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, f"\n{result.stdout}\n{result.stderr}"
    match = re.search(r"\n(\d+)/(\d+) passed", result.stdout)
    assert match, result.stdout
    passed, total = int(match.group(1)), int(match.group(2))
    # 26 corpus files plus the figure, decimation and File-seam checks.
    assert passed == total and total >= 40, result.stdout


def test_the_corpus_the_browser_reader_answers_to_is_the_committed_one():
    """The node suite reads these off disk. If the corpus were generated at
    test time the browser reader would be proved against bytes that had never
    survived a checkout — which is the reason these files are committed."""
    files = {p.name for p in _CORPUS.iterdir()} - {"README.md", ".gitattributes"}
    assert len(files) == 26, sorted(files)


# ══════════════════════════════════════════════════════════════════════════
# 2 · No analysis number is born in the browser
# ══════════════════════════════════════════════════════════════════════════


class TestThePreviewMakesNoClaim:

    def test_the_block_contains_no_analysis_vocabulary(self):
        """Belt and braces on the node suite's check of the RENDERED figure:
        the words are not in the source either, so they cannot arrive through a
        branch the corpus does not happen to reach."""
        code = _preview_code()
        for word in ("iso_zone", "severity", "mm/s RMS", "Zone A", "zoneTag",
                     "BPFO", "BPFI", "trendBand", "trendRise"):
            assert word not in code, f"the preview must not know about {word!r}"

    def test_it_computes_no_spectrum_of_its_own(self):
        """No FFT, no windowing, no RMS. It draws columns that were already in
        the file; anything else would be a second implementation of something
        `pdm_core` already does correctly, and a second implementation that
        disagrees is worse than no picture."""
        code = _preview_code()
        # Word boundaries, because "hann" is inside "dead channel" and a ban
        # that fires on a substring of ordinary prose gets deleted rather than
        # fixed the first time it cries wolf.
        for word in ("fft", "hann", "hanning", "hamming", "rms", "envelope"):
            assert not re.search(rf"\b{word}\b", code, re.I), word
        for token in ("Math.sqrt", "Math.cos", "Math.sin"):
            assert token not in code, token

    def test_the_figure_is_inline_svg_and_asks_for_nothing(self):
        """CSP is `default-src 'self'` — but more to the point, a preview that
        fetched anything would be a preview that told a server what the analyst
        is about to upload."""
        block = _preview_block()
        assert "<svg" in block
        assert "fetch(" not in _preview_code()
        assert "<img" not in block
        assert 'role="img"' in block and "aria-label=" in block

    def test_the_word_preview_is_on_the_figure_itself(self):
        assert ">PREVIEW</b>" in _preview_block()


# ══════════════════════════════════════════════════════════════════════════
# 3 · It reads only what it can read honestly
# ══════════════════════════════════════════════════════════════════════════


class TestTheFormatsItClaims:

    def test_it_claims_only_text_spectra(self):
        block = _preview_block()
        exts = re.findall(r"'([a-z]+)'",
                          block.partition("const PREVIEW_EXTS = [")[2].partition("]")[0])
        assert exts == ["csv", "txt", "dat", "asc"], exts

    def test_every_format_it_reads_is_one_the_form_accepts(self):
        """The reverse is deliberately NOT true: xlsx, uff, unv, wav and mat
        are accepted by the form and not read here, and each gets a panel that
        says the file is still analysed."""
        allowed = json.loads((_ROOT / "config" / "webapp.json").read_text())["allowed_extensions"]
        block = _preview_block()
        exts = re.findall(r"'([a-z]+)'",
                          block.partition("const PREVIEW_EXTS = [")[2].partition("]")[0])
        assert {f".{e}" for e in exts} <= set(allowed), (exts, allowed)

    def test_the_unreadable_formats_are_not_called_an_error(self):
        """An .xlsx the browser cannot draw is a perfectly good upload. The
        panel says so, in the same breath as saying there is no picture."""
        block = _preview_block()
        assert "uploaded and analysed exactly as normal" in block
        assert "NO PREVIEW" in block

    def test_the_read_is_bounded(self):
        """`max_upload_bytes` is 25 MB. Reading all of it into a string on the
        main thread to draw 880 pixels is work nobody asked for."""
        block = _preview_block()
        assert "const PREVIEW_HEAD_BYTES = 4 * 1024 * 1024;" in block
        assert "file.slice(0, PREVIEW_HEAD_BYTES)" in block


class TestItRefusesRatherThanGuesses:

    def test_every_refusal_says_which_file_it_is_about_and_stays_calm(self):
        block = _preview_block()
        refusals = block.partition("const PREVIEW_REFUSALS = {")[2].partition("\n};")[0]
        for key in ("binary", "thousands", "nonmono", "toofew", "shape"):
            assert f"{key}:" in refusals, key
        # A refusal here is never a verdict on the file: the server reads
        # several of these perfectly well.
        assert "not a verdict on the file" in refusals

    def test_decimation_cannot_hide_a_peak(self):
        """A stride sample drops a one-bin line, and a one-bin line is the
        entire content of a spectrum. The node suite proves the behaviour on a
        10,000-bin fixture; this pins the intent at the source."""
        block = _preview_block()
        body = block.partition("function decimate(bins, n) {")[2].partition("\n}")[0]
        assert "if (bins[j][1] > pick[1]) pick = bins[j];" in body, body

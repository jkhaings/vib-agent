"""Unit tests for invite-code gating, rate limiting, and upload validation
(webapp/security.py). No FastAPI app involved -- pure logic.
"""

from __future__ import annotations

import pytest

from vib_agent.webapp.security import (
    InviteCodeError,
    RateLimitError,
    RateLimiter,
    parse_invite_codes,
    resolve_code_label,
    validate_upload,
)


class TestParseInviteCodes:
    def test_parses_pairs(self):
        codes = parse_invite_codes("demo-code:engineer-1,demo-code-2:analyst-1")
        assert codes == {"demo-code": "engineer-1", "demo-code-2": "analyst-1"}

    def test_empty_or_none_returns_empty_dict(self):
        assert parse_invite_codes(None) == {}
        assert parse_invite_codes("") == {}

    def test_malformed_entry_raises(self):
        with pytest.raises(ValueError, match="malformed"):
            parse_invite_codes("no-colon-here")

    def test_tolerates_whitespace(self):
        codes = parse_invite_codes(" demo-code : engineer-1 , demo-code-2:analyst-1 ")
        assert codes == {"demo-code": "engineer-1", "demo-code-2": "analyst-1"}


class TestResolveCodeLabel:
    def test_known_code_returns_label(self):
        assert resolve_code_label({"abc": "label1"}, "abc") == "label1"

    def test_unknown_code_raises(self):
        with pytest.raises(InviteCodeError):
            resolve_code_label({"abc": "label1"}, "xyz")

    def test_missing_code_raises(self):
        with pytest.raises(InviteCodeError):
            resolve_code_label({"abc": "label1"}, None)
        with pytest.raises(InviteCodeError):
            resolve_code_label({"abc": "label1"}, "")


class TestRateLimiter:
    def test_allows_up_to_per_code_cap(self):
        limiter = RateLimiter(per_code_daily_jobs=3, global_daily_jobs=100)
        for _ in range(3):
            limiter.check_and_record("engineer-1")
        with pytest.raises(RateLimitError, match="this invite code"):
            limiter.check_and_record("engineer-1")

    def test_different_codes_have_independent_counters(self):
        limiter = RateLimiter(per_code_daily_jobs=1, global_daily_jobs=100)
        limiter.check_and_record("engineer-1")
        limiter.check_and_record("analyst-1")  # different code -- should not raise

    def test_global_cap_applies_across_codes(self):
        limiter = RateLimiter(per_code_daily_jobs=100, global_daily_jobs=2)
        limiter.check_and_record("a")
        limiter.check_and_record("b")
        with pytest.raises(RateLimitError, match="global"):
            limiter.check_and_record("c")


class TestValidateUpload:
    def test_valid_csv_passes(self):
        ext = validate_upload("data.csv", b"freq_hz,amplitude\n1,2\n", allowed_extensions=(".csv",), max_bytes=1000)
        assert ext == ".csv"

    def test_oversize_raises(self):
        with pytest.raises(ValueError, match="exceeds max size"):
            validate_upload("data.csv", b"x" * 100, allowed_extensions=(".csv",), max_bytes=10)

    def test_unsupported_extension_raises(self):
        with pytest.raises(ValueError, match="unsupported extension"):
            validate_upload("data.exe", b"\x00\x01", allowed_extensions=(".csv", ".xlsx"), max_bytes=1000)

    def test_xlsx_magic_mismatch_raises(self):
        with pytest.raises(ValueError, match="does not look like"):
            validate_upload("data.xlsx", b"not a zip file at all", allowed_extensions=(".xlsx",), max_bytes=1000)

    def test_xlsx_magic_match_passes(self):
        ext = validate_upload("data.xlsx", b"PK\x03\x04restofzip", allowed_extensions=(".xlsx",), max_bytes=1000)
        assert ext == ".xlsx"

    def test_wav_magic_mismatch_raises(self):
        with pytest.raises(ValueError, match="does not look like"):
            validate_upload("data.wav", b"not a wav file", allowed_extensions=(".wav",), max_bytes=1000)

    def test_mat_magic_match_passes(self):
        ext = validate_upload(
            "data.mat", b"MATLAB 5.0 MAT-file, header padding" + b"\x00" * 50,
            allowed_extensions=(".mat",), max_bytes=1000,
        )
        assert ext == ".mat"

    def test_csv_has_no_magic_check(self):
        # CSV/UFF/UNV are plain text -- extension alone is the gate here;
        # content-level validation happens in the parser.
        ext = validate_upload("data.csv", b"anything at all", allowed_extensions=(".csv",), max_bytes=1000)
        assert ext == ".csv"


class TestXlsxXmlHardening:
    def test_openpyxl_uses_defusedxml(self):
        # An .xlsx upload is untrusted XML-in-a-zip. openpyxl parses with defusedxml
        # (blocking XXE and entity-expansion bombs) IFF defusedxml is importable, and
        # openpyxl does not depend on it itself -- so `defusedxml` is declared in the
        # [web] extra. Before that it was pulled in only transitively by pip-audit's
        # py-serializable, so this protection would have silently vanished from a
        # runtime-only install. This asserts the declaration is actually in effect.
        import openpyxl.xml

        assert openpyxl.xml.DEFUSEDXML is True

"""Session G: the bounded sample handed to the inference pass.

This is the ONLY part of an uploaded file a model ever sees, so its size and
shape are fixed here rather than at the call site: the first 8 KB, at most 100
lines, and only if the bytes are text at all. A binary file never reaches the
inference lane — it falls back to the existing extension whitelist behaviour.

The sample is data, not instruction. Nothing here interprets it; the executor
(recipe.py) reads the real file, and the recipe schema is closed, so the worst
a hostile sample can do is make inference fail.
"""

from __future__ import annotations

import codecs
from pathlib import Path

MAX_SAMPLE_BYTES = 8192
MAX_SAMPLE_LINES = 100

# A byte-order mark is the one UNAMBIGUOUS statement a file makes about its own
# encoding, so it is believed before anything is guessed. Order matters: the
# UTF-32 LE mark (FF FE 00 00) starts with the UTF-16 LE mark (FF FE), so a
# UTF-32 file checked second would be read as UTF-16 and come out as NULs.
#
# UTF-16 belongs here because Excel's "Unicode Text (*.txt)" export IS UTF-16 LE
# with a BOM — not an exotic format, just what Save As gives a Windows analyst.
# Decoded as cp1252 it becomes NUL-laden text, which _looks_binary correctly
# classes as binary, so before this the product turned away a perfectly ordinary
# export as if it were corrupt. (INTAKE-HARDEN, fixture utf16le_bom.txt.)
_BOMS: tuple[tuple[bytes, str], ...] = (
    (codecs.BOM_UTF32_LE, "utf-32-le"),
    (codecs.BOM_UTF32_BE, "utf-32-be"),
    (codecs.BOM_UTF8, "utf-8"),
    (codecs.BOM_UTF16_LE, "utf-16-le"),
    (codecs.BOM_UTF16_BE, "utf-16-be"),
)

# Codecs tried in order when there is no BOM: UTF-8 (with and without a mark)
# covers modern exports, cp1252 covers the Windows-authored instrument reports
# that carry µ, °, ±. BOM-less UTF-16 is deliberately NOT guessed at — without a
# mark it is genuinely ambiguous, and a wrong guess is worse than a refusal.
_CODECS = ("utf-8-sig", "utf-8", "cp1252")

# A text export may carry the odd control character; a binary carries many.
_MAX_CONTROL_FRACTION = 0.02


class NotTextError(ValueError):
    """The bytes are not a text file — the caller must not start an inference
    pass on them."""


def _looks_binary(text: str) -> bool:
    if "\x00" in text:
        return True
    control = sum(1 for ch in text if ord(ch) < 32 and ch not in "\t\r\n")
    return bool(text) and control / len(text) > _MAX_CONTROL_FRACTION


def _decode_strict(raw: bytes, codec: str) -> str | None:
    """`raw` as `codec`, or None if it genuinely is not that encoding.

    One allowance: a BOUNDED read (the 8 KB sample) can cut a multi-byte
    character in half, so a failure within the last few bytes drops the partial
    tail rather than condemning the file. A failure anywhere earlier is a real
    encoding mismatch and returns None.
    """
    try:
        return raw.decode(codec)
    except UnicodeDecodeError as exc:
        if exc.start > 0 and exc.start >= len(raw) - 4:
            try:
                return raw[: exc.start].decode(codec)
            except UnicodeDecodeError:
                return None
        return None


def decode_upload_bytes(raw: bytes, *, lenient: bool = False) -> str:
    """Decode an uploaded text export: believe a BOM, otherwise try the codec
    list in order.

    `lenient=False` (the SAMPLE) refuses what it cannot decode, so a binary file
    never reaches an inference pass. `lenient=True` (the EXECUTOR, reading the
    whole file) falls back to a replacing UTF-8 decode instead, because by then
    the file has already been judged readable and a junk tail — a text header
    over packed binary — should cost the rows it occupies, not the whole upload.
    """
    for bom, codec in _BOMS:
        if raw.startswith(bom):
            body = raw[len(bom) :]
            text = _decode_strict(body, codec)
            if text is not None:
                return text
            if lenient:
                return body.decode(codec, errors="replace")
            raise NotTextError("this file declares an encoding its contents do not match")

    for codec in _CODECS:
        text = _decode_strict(raw, codec)
        if text is not None:
            return text

    if lenient:
        return raw.decode("utf-8", errors="replace")
    raise NotTextError("this file is not readable as text")


def read_text_sample(path: Path, *, max_bytes: int = MAX_SAMPLE_BYTES,
                     max_lines: int = MAX_SAMPLE_LINES) -> str:
    """The bounded, text-decodable head of `path`.

    Raises NotTextError if the file is empty, undecodable, or looks binary.
    Reads at most `max_bytes` from disk — a 25 MB upload costs one 8 KB read.
    """
    with path.open("rb") as handle:
        raw = handle.read(max_bytes)
    if not raw.strip():
        raise NotTextError("this file appears to be empty")

    text = decode_upload_bytes(raw)
    if _looks_binary(text):
        raise NotTextError("this file looks like binary data, not a text export")

    lines = text.splitlines()
    # A truncated final line (we cut mid-file at max_bytes) is dropped rather
    # than shown as a real row.
    if len(raw) == max_bytes and len(lines) > 1:
        lines = lines[:-1]
    return "\n".join(lines[:max_lines])


def read_xlsx_sample(path: Path, *, max_rows: int = MAX_SAMPLE_LINES,
                     max_bytes: int = MAX_SAMPLE_BYTES) -> str:
    """The first sheet's first rows as tab-joined CELL TEXT.

    `data_only=True` reads each cell's STORED VALUE: a formula is never
    evaluated and never seen — what reaches the sample is what the spreadsheet
    displays. Cells are already split, so joining them with tabs gives the
    inference pass a sample whose column positions match exactly what the
    executor will read back out of the sheet.

    Untrusted input, so this is meant to be run inside the parse sandbox
    (webapp/parsing.py::sample_in_subprocess), never in the request process.
    """
    import openpyxl

    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = workbook.worksheets[0]
        lines: list[str] = []
        size = 0
        for row in sheet.iter_rows(values_only=True, max_row=max_rows):
            cells = ["" if cell is None else str(cell) for cell in (row or ())]
            line = "\t".join(cells).rstrip()
            size += len(line) + 1
            if size > max_bytes:
                break
            lines.append(line)
    finally:
        workbook.close()
    if not any(line.strip() for line in lines):
        raise NotTextError("this spreadsheet has no readable cells in its first sheet")
    return "\n".join(lines)


def read_sample(path: Path) -> str:
    """The bounded sample for whatever kind of file this is: cell text for a
    spreadsheet, decoded head for everything else."""
    if path.suffix.lower() == ".xlsx":
        return read_xlsx_sample(path)
    return read_text_sample(path)


def structure_fingerprint(sample: str, *, max_lines: int = 40) -> str:
    """A hash of the sample's SHAPE, with every value removed.

    Digits collapse to '9', letters to 'a', so two exports from the same
    instrument in the same layout hash identically while carrying no reading,
    no machine name, and no timestamp into the cache key or an opt-in ping.
    """
    import hashlib

    skeleton_lines: list[str] = []
    for line in sample.splitlines()[:max_lines]:
        out: list[str] = []
        previous = ""
        for ch in line:
            if ch.isdigit():
                token = "9"
            elif ch.isalpha():
                token = "a"
            else:
                token = ch
            # collapse runs, so 1785.0 and 3600 share a skeleton
            if token in ("9", "a") and token == previous:
                continue
            out.append(token)
            previous = token
        skeleton_lines.append("".join(out))
    skeleton = "\n".join(skeleton_lines)
    return hashlib.sha256(skeleton.encode("utf-8")).hexdigest()[:32]

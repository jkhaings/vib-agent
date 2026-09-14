"""Session UX-4 (STRANGER U7) -- the two document pages stop being `cat` output.

`/validation` and `/field-validation-results.md` served their source files
inside `<pre class="doc">`, which is why a stranger's report said this of the
best sales asset on the site:

    Both are raw monospace dumps: "single source of truth is
    (outputs/validation_summary.md)", "eval/runner.py rewrites
    cwru_results.md", "(Blind Test A disposition)", a Markdown pipe table
    rendered as text and cut off on the right. -- A formatted page. The
    content is your best sales asset; right now it looks like `cat` output
    pasted into a `<pre>`.

THE TWO FILES ARE NOT THE SAME KIND OF DOCUMENT, and that is the thing to get
right rather than to average over. Measured before writing a line of this:

  * `outputs/field_validation_results.md` IS markdown -- 1 h1, 5 h2, 4 h3, a
    22-row pipe table, 20 list items, bold, links, a blockquote.
  * `outputs/validation_summary.md` IS NOT. It has no `#` heading and no pipe
    table anywhere. It is a plain-text report with setext-style `-----`
    underlines, hard-wrapped prose indented two spaces, and eleven ALIGNED
    stat lines whose columns only line up in a monospace font:

        Committed inner/outer-race diagnosis correct:  20 / 24
        False bearing calls on healthy files:           0 / 4  (clean)

    Reflowing those into prose would destroy the one thing they are for. They
    become a real two-column stat row instead, which is what the alignment was
    imitating.

No dependency: `markdown` is an optional extra used by the PDF path
(`report/generate.py`), and reaching for it here would make a documentation
page depend on a package the deployed install may not have. This handles the
constructs those two files actually contain and nothing else.

SAFETY. Everything is escaped FIRST and marked up afterwards, so no character
from the source can become part of a tag or an attribute. Link targets are
allowlisted to site-relative and https. The result therefore contains no
`<script>`, no `on*=` handler and no `javascript:` URL --
`tests/test_hardening.py::test_no_served_page_carries_inline_script` already
sweeps both of these routes and would fail if that stopped being true.
"""

from __future__ import annotations

import re

__all__ = ["render_doc"]

_RULE = re.compile(r"^\s*[-=]{4,}\s*$")
_ATX = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_ORDERED = re.compile(r"^(\s*)\d+[.)]\s+(.*)$")
_TABLE_SEP = re.compile(r"^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$")
#: Below this, a line is taken to have ENDED rather than wrapped. The source
#: documents are hard-wrapped at about 90 columns for a terminal.
_SHORT_LINE = 56
#: `Label:   value` -- two or more spaces between the colon and the value is
#: what makes it a column rather than a sentence with a colon in it.
_STAT = re.compile(r"^\s{1,8}([A-Z][^:]{3,70}):\s{2,}(\S.*)$")
def _is_title(line: str) -> bool:
    """Is this line, which is followed by a `-----` rule, a section title?

    The RULE is the author's signal and does almost all the work; this only
    refuses a wrapped sentence that happens to land above one. Measured
    against the real file rather than guessed: a first draft required the
    line to be all-caps and silently demoted four of the five section titles
    to `<hr>`, because every one of them carries a proper noun --
    "BEARING DIAGNOSIS -- CWRU (Case Western Reserve University)". The tag
    census is what caught it: 2 headings where the source has 5.
    """
    stripped = line.strip()
    return (bool(stripped)
            and line == stripped            # a title is not indented
            and len(stripped) <= 100
            and not stripped.endswith("."))


def _escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _safe_href(url: str) -> str | None:
    """Site-relative or https, and nothing else.

    An allowlist rather than a `javascript:` blocklist: the blocklist form has
    to anticipate every scheme (`data:`, `vbscript:`, a tab inside the word),
    and the allowlist has to anticipate none.
    """
    url = url.strip()
    if url.startswith("/") or url.startswith("#"):
        return url if not url.startswith("//") else None
    if url.startswith("https://"):
        return url
    return None


def _inline(text: str) -> str:
    """Escaped text -> escaped text with inline markup. Never the reverse
    order: marking up first would let a `<` in the source open a tag."""
    out = _escape(text)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)

    def link(m: re.Match[str]) -> str:
        href = _safe_href(m.group(2))
        # A refused target keeps its text and loses its link, rather than
        # vanishing: the sentence still reads.
        return f'<a href="{href}">{m.group(1)}</a>' if href else m.group(1)

    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", link, out)


def _flush_para(buf: list[str], out: list[str]) -> None:
    """Hard-wrapped source lines become ONE paragraph.

    This is the half of U7 that matters most: the source is wrapped at about
    90 characters for a terminal, and a page that keeps those breaks is a
    page that reads like a terminal. Joined, it reflows to whatever column
    the reader's window gives it.
    """
    if not buf:
        return
    out.append("<p>" + _inline(" ".join(s.strip() for s in buf)) + "</p>")
    buf.clear()


def _flush_stats(rows: list[tuple[str, str]], out: list[str]) -> None:
    if not rows:
        return
    out.append('<dl class="docstat">')
    for label, value in rows:
        out.append(f"<div><dt>{_inline(label)}</dt><dd>{_inline(value)}</dd></div>")
    out.append("</dl>")
    rows.clear()


def _flush_list(items: list[str], ordered: bool, out: list[str]) -> None:
    if not items:
        return
    tag = "ol" if ordered else "ul"
    out.append(f'<{tag} class="doclist">')
    out.extend(f"<li>{_inline(i)}</li>" for i in items)
    out.append(f"</{tag}>")
    items.clear()


def _cells(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def render_doc(text: str) -> str:
    """One document's source text -> the body of a formatted page.

    Returns HTML with no wrapper element of its own; the caller places it.
    """
    lines = text.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    para: list[str] = []
    stats: list[tuple[str, str]] = []
    items: list[str] = []
    ordered = False
    item_indent = 0
    stat_indent = 0
    fenced = False
    fence: list[str] = []
    i = 0

    def flush_all() -> None:
        _flush_para(para, out)
        _flush_stats(stats, out)
        _flush_list(items, ordered, out)

    while i < len(lines):
        line = lines[i]

        if line.startswith("```"):
            if fenced:
                out.append('<pre class="doccode">' + _escape("\n".join(fence)) + "</pre>")
                fence.clear()
            else:
                flush_all()
            fenced = not fenced
            i += 1
            continue
        if fenced:
            fence.append(line)
            i += 1
            continue

        if not line.strip():
            flush_all()
            i += 1
            continue

        # A setext rule turns the line ABOVE it into a heading. This is the
        # only heading `validation_summary.md` has.
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if _RULE.match(nxt) and _is_title(line):
            flush_all()
            out.append(f"<h2>{_inline(line.strip())}</h2>")
            i += 2
            continue
        if _RULE.match(line):
            # A rule with nothing above it to title: a horizontal divider.
            flush_all()
            out.append("<hr>")
            i += 1
            continue

        atx = _ATX.match(line)
        if atx:
            flush_all()
            level = min(len(atx.group(1)) + 1, 6)   # the page owns <h1>
            out.append(f"<h{level}>{_inline(atx.group(2).strip())}</h{level}>")
            i += 1
            continue

        # A pipe table: a header row whose NEXT line is a separator. Checked
        # that way rather than by counting pipes, so a sentence containing a
        # pipe is still a sentence.
        if "|" in line and _TABLE_SEP.match(lines[i + 1] if i + 1 < len(lines) else ""):
            flush_all()
            head = _cells(line)
            i += 2
            body: list[list[str]] = []
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                body.append(_cells(lines[i]))
                i += 1
            out.append('<div class="tw"><table class="mtable">')
            out.append("<thead><tr>"
                       + "".join(f'<th scope="col">{_inline(c)}</th>' for c in head)
                       + "</tr></thead><tbody>")
            for row in body:
                out.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in row) + "</tr>")
            out.append("</tbody></table></div>")
            continue

        if line.lstrip().startswith("&gt;") or line.lstrip().startswith(">"):
            flush_all()
            out.append("<blockquote><p>"
                       + _inline(line.lstrip().lstrip(">").strip()) + "</p></blockquote>")
            i += 1
            continue

        bullet = _BULLET.match(line)
        order = _ORDERED.match(line)
        if bullet or order:
            _flush_para(para, out)
            _flush_stats(stats, out)
            want_ordered = bool(order)
            if items and want_ordered != ordered:
                _flush_list(items, ordered, out)
            ordered = want_ordered
            marker = order or bullet
            item_indent = len(marker.group(1))
            items.append(marker.group(2).strip())
            i += 1
            continue

        # A line indented DEEPER than the bullet that opened the list is that
        # bullet's continuation, not a new paragraph. Every list item in
        # `validation_summary.md` is hard-wrapped this way, and a first draft
        # that missed it produced eleven single-item lists with the second
        # half of each sentence stranded in a paragraph between them.
        if items and line.startswith(" " * (item_indent + 1)) and not _STAT.match(line):
            items[-1] = items[-1] + " " + line.strip()
            i += 1
            continue

        stat = _STAT.match(line)
        if stat:
            _flush_para(para, out)
            _flush_list(items, ordered, out)
            stat_indent = len(line) - len(line.lstrip())
            stats.append((stat.group(1).strip(), stat.group(2).strip()))
            i += 1
            continue

        # A stat's VALUE can wrap too, and the continuation is indented to the
        # value column rather than to the label -- so it is indented much
        # deeper than the stat line itself:
        #
        #     False positives on healthy baselines:  0  (the last one cleared by the
        #                                               amplitude floor)
        #
        # Without this the second half becomes its own paragraph BELOW the
        # row, which is what it looked like in the first rendered page: a
        # figure that read "0 (the last one cleared by the" with "amplitude
        # floor)" stranded underneath it.
        if stats and not items and line.startswith(" " * (stat_indent + 8)):
            label, value = stats[-1]
            stats[-1] = (label, value + " " + line.strip())
            i += 1
            continue

        _flush_stats(stats, out)
        _flush_list(items, ordered, out)
        para.append(line)
        # A line MUCH shorter than the wrap width was not wrapped -- it ended.
        # The source is hard-wrapped at about 90 characters, so a mid-paragraph
        # line is always near that; a short one is the end of a paragraph or a
        # standalone line. Without this the document's title and its date
        # become one sentence: "VALIDATION SUMMARY - vib-agent Last updated:
        # 2026-07-23 (Blind Test A disposition)".
        if len(line.rstrip()) < _SHORT_LINE:
            _flush_para(para, out)
        i += 1

    if fenced and fence:                       # an unclosed fence still renders
        out.append('<pre class="doccode">' + _escape("\n".join(fence)) + "</pre>")
    flush_all()
    return "\n".join(out)

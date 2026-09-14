"""Small helpers the HTML survey template needs — and nothing else.

Session V2-WIRE. `report/generate.py` renders TWO documents from ONE
`build_context()`: the markdown report (`render_markdown`, unchanged) and the
v2 HTML page the PDF is produced from (`render_html`). This module holds the
three things the HTML side needs that the markdown side does not.

Nothing here derives a number, and nothing here authors a sentence.
"""

from __future__ import annotations

import re
from functools import lru_cache
from html import escape
from pathlib import Path

from markupsafe import Markup

_HERE = Path(__file__).parent
_CSS_PATH = _HERE / "report.css"

#: The product's own self-hosted IBM Plex faces. The site self-hosts them so
#: that no visitor IP ever reaches a font CDN; the report references the SAME
#: files rather than carrying a second copy, so the document and the shell it is
#: served from cannot drift apart. This is a path to a data file, not a code
#: dependency on the webapp.
_FONT_DIR = _HERE.parent / "webapp" / "static" / "fonts"

_FACES: tuple[tuple[str, int, str], ...] = (
    ("IBM Plex Sans", 400, "IBMPlexSans-Regular"),
    ("IBM Plex Sans", 500, "IBMPlexSans-Medium"),
    ("IBM Plex Sans", 600, "IBMPlexSans-SemiBold"),
    ("IBM Plex Mono", 400, "IBMPlexMono-Regular"),
    ("IBM Plex Mono", 500, "IBMPlexMono-Medium"),
    ("IBM Plex Sans Condensed", 600, "IBMPlexSansCondensed-SemiBold"),
)


def font_faces() -> str:
    """`@font-face` rules pointing at the product's own `.woff2` files by
    ABSOLUTE path.

    Absolute because the PDF renderer resolves relative URLs against the report
    directory (that is what makes `charts/*.png` work), not against this
    package. A face whose file is missing is simply skipped: the stylesheet's
    fallback stack is real, so the page stays legible rather than failing.
    """
    out = []
    for family, weight, stem in _FACES:
        path = _FONT_DIR / f"{stem}.woff2"
        if not path.exists():
            continue
        out.append(
            f'@font-face{{font-family:"{family}";font-style:normal;'
            f"font-weight:{weight};src:url('{path.as_uri()}') format('woff2')}}"
        )
    return "\n".join(out)


@lru_cache(maxsize=1)
def _css_text() -> str:
    return _CSS_PATH.read_text()


def stylesheet() -> str:
    """The whole stylesheet, inlined into the page so the document is one file
    — the same reason the SVGs are inlined rather than linked."""
    return font_faces() + "\n" + _css_text()


_BOLD = re.compile(r"\*\*(.+?)\*\*", re.S)
# Single-asterisk emphasis. Runs AFTER the `**` pass, so bold is already
# consumed; the lookarounds require non-space on both inner edges so a lone
# `*` in prose is left alone.
_EM_STAR = re.compile(r"(?<!\*)\*(?!\s)([^*\n]+?)(?<!\s)\*(?!\*)")
_CODE = re.compile(r"`([^`]+)`")
# Signature rules like `______________` must survive untouched, so the body
# must be non-empty and both sides must be non-word characters.
_EM_UNDER = re.compile(r"(?<![A-Za-z0-9_])_([^_\n]+)_(?![A-Za-z0-9_])")


def md_inline(text: object) -> Markup:
    """`**bold**`, `*italic*`, `_italic_` and `` `code` `` — the report's whole
    inline markdown set, and nothing beyond it.

    Every string on the page goes through here, so escaping happens FIRST and
    exactly once: the report carries analyst-facing prose and reference
    locators, and a `<` in either must render as a `<`.

    The underscore pass runs in two steps because the report wraps whole
    PARAGRAPHS in underscores — the cause-section legend and the damage-stage
    hedge both do — and those span newlines. A single newline-free pattern left
    the legend's underscores on the page as literal characters.
    """
    s = escape(str(text), quote=False)
    s = _BOLD.sub(r"<strong>\1</strong>", s)
    s = _EM_STAR.sub(r"<em>\1</em>", s)
    s = _CODE.sub(r"<code>\1</code>", s)
    body = s.strip()
    if len(body) > 2 and body.startswith("_") and body.endswith("_") and "_" not in body[1:-1]:
        return Markup(f"<em>{body[1:-1]}</em>")
    return Markup(_EM_UNDER.sub(r"<em>\1</em>", s))

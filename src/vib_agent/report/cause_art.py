"""The cause-illustration register, and the inline SVG it hands back.

Session V2-WIRE (WIRING.md slice W6c). The mapping lives in
`assets/causes/INDEX.md` and this module PARSES it rather than carrying a second
copy: two hand-maintained copies of the same table is how a picture ends up
beside the wrong cause.

`asset_for` returns None for a cause with no mapped asset, and the template then
emits no image element at all. **There is no fallback drawing, on purpose.** A
picture of the wrong mechanism beside the right words is worse than no picture,
because a reader takes it as evidence — which is what the section's own heading
("hypotheses for analyst confirmation") exists to prevent.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from markupsafe import Markup

ASSETS = Path(__file__).parent / "assets" / "causes"
INDEX = ASSETS / "INDEX.md"

#: The one caption every illustration carries, in ONE place.
#:
#: Three modules have to agree on this string — the generator that draws the
#: assets, the renderer that captions them, and the check that counts them.
#: `tests/test_cause_art.py` asserts this copy equals the generator's own
#: `make_cause_art.DISCLAIMER`, so drift breaks the build rather than quietly
#: shipping two different disclaimers.
DISCLAIMER = "Schematic illustration — not derived from your data."

#: ``| `cause_id` | `stem.svg` |`` — the register's own row shape. A row whose
#: asset cell is not a backticked filename does not match, which is the
#: behaviour we want: the "drawn but NOT shipped" table at the end of the
#: register names files, but never beside a `cause_id`.
_ROW = re.compile(r"^\|\s*`([a-z0-9_]+)`\s*\|\s*`([a-z0-9-]+\.svg)`\s*\|", re.M)


@lru_cache(maxsize=1)
def mapping() -> dict[str, str]:
    """`cause_id` → asset filename, read from the register.

    An absent register is not an error: the package may have been installed
    without its data files, and a report with no illustrations is a legitimate
    report. A DANGLING row is an error — see `asset_for`.
    """
    if not INDEX.exists():
        return {}
    return dict(_ROW.findall(INDEX.read_text()))


@lru_cache(maxsize=64)
def asset_for(cause_id: str | None) -> str | None:
    """The inline `<svg>` for `cause_id`, or None if it has no mapped asset.

    Raises if the register names a file that is not there. A dangling row is a
    silently missing figure otherwise, and the whole point of a register is that
    it cannot drift from the directory it describes.
    """
    if not cause_id:
        return None
    name = mapping().get(cause_id)
    if not name:
        return None
    path = ASSETS / name
    if not path.exists():
        raise FileNotFoundError(
            f"INDEX.md maps {cause_id} -> {name}, which does not exist in {ASSETS}")
    svg = path.read_text()
    # Inline SVG in HTML needs no xmlns, and leaving it only makes the page's
    # markup noisier.
    svg = svg.replace(' xmlns="http://www.w3.org/2000/svg"', "", 1).strip()
    return _namespace_ids(svg, path.stem)


_ID_ATTR = re.compile(r'\bid="([A-Za-z0-9_-]+)"')


def _namespace_ids(svg: str, prefix: str) -> str:
    """Prefix every `id` in one asset, and every reference to it.

    Eleven causes inline eleven SVGs into ONE document, and every asset ships
    the same `id="t"` / `id="d"` — the `<title>`/`<desc>` pair its
    `aria-labelledby` points at — plus its own `clipPath` ids. Duplicated across
    one page, `aria-labelledby="t d"` resolves to the FIRST asset on the page
    for all eleven, so every illustration announces the first one's title to a
    screen reader, and `url(#…)` clips resolve to the wrong rectangle.
    Invisible on screen; wrong underneath.

    Any renderer that inlines more than one SVG per document has to do this.
    """
    ids = set(_ID_ATTR.findall(svg))
    for ident in sorted(ids, key=len, reverse=True):
        new = f"{prefix}-{ident}"
        svg = svg.replace(f'id="{ident}"', f'id="{new}"')
        svg = svg.replace(f"url(#{ident})", f"url(#{new})")
        svg = re.sub(rf'(aria-labelledby="[^"]*)\b{re.escape(ident)}\b', rf"\1{new}", svg)
    return svg


def illustration(cause: object) -> Markup:
    """The `<figure>` for one cause — or an empty string, which is a legitimate
    and deliberate outcome.

    The caption is part of the figure, not an option: an uncaptioned schematic
    beside a measured spectrum is read as more measurement.
    """
    cause_id = cause.get("cause_id") if isinstance(cause, dict) else getattr(cause, "cause_id", None)
    svg = asset_for(cause_id)
    if svg is None:
        return Markup("")
    return Markup(
        f'<figure class="cause-art" data-cause-art="{cause_id}">'
        f'<div class="plot">{svg}</div>'
        f"<figcaption>{DISCLAIMER}</figcaption></figure>"
    )

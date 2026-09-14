"""Upload-format adapters (Phase 6): normalize a webapp-uploaded file into a
Case, the same contract adapters/cwru.py already established. `parse_upload`
is the single dispatch point the sandboxed parser subprocess calls — it
never touches pdm_core or agent/ directly, only the Case it hands back does.

Supported today: CSV/XLSX (spectrum or trend schema), UFF/UNV (dataset 58),
WAV (scaled or unscaled), and .mat (CWRU or MFPT format, told apart by
_sniff_mat_kind() before parsing -- see adapters/mfpt.py and
adapters/uploads/mfpt.py for the Phase 7 addition). Anything else is the
parser backlog funnel (see webapp/static/index.html).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from vib_agent.adapters.uploads.common import (
    UploadForm,
    acquisition_from_form,
    apply_machine_geometry,
)
from vib_agent.adapters.uploads.cwru import parse_cwru_mat
from vib_agent.adapters.uploads.mfpt import parse_mfpt_mat
from vib_agent.adapters.uploads.tabular import parse_spectrum, parse_trend
from vib_agent.adapters.uploads.uff import parse_uff
from vib_agent.adapters.uploads.mafaulda import parse_mafaulda_csv
from vib_agent.adapters.uploads.recipe import INFERRED_EXTENSIONS, ParseRecipe, execute_recipe
from vib_agent.adapters.uploads.wav import parse_wav
from vib_agent.adapters.uploads.wind_turbine import parse_wind_turbine_mat
from vib_agent.models import Case

# Extensions this package can parse. ".csv"/".xlsx" additionally need
# `form.mode` (spectrum vs trend) since the extension alone doesn't say
# which schema it is.
TABULAR_EXTENSIONS = (".csv", ".xlsx")
UFF_EXTENSIONS = (".uff", ".unv")
WAV_EXTENSIONS = (".wav",)
MAT_EXTENSIONS = (".mat",)
# Session G: text exports with no adapter of their own (.txt/.dat/.asc) take the
# schema-inference lane -- a recipe is inferred from a bounded sample, then
# executed here. This is a FALLBACK lane: every extension above keeps its own
# adapter, unchanged, and is never routed through inference.
ALL_SUPPORTED_EXTENSIONS = (
    TABULAR_EXTENSIONS + UFF_EXTENSIONS + WAV_EXTENSIONS + MAT_EXTENSIONS + INFERRED_EXTENSIONS
)


class UnsupportedFormatError(ValueError):
    """Raised for an extension outside ALL_SUPPORTED_EXTENSIONS -- the caller
    (webapp/security.py) turns this into the funnel message, never a crash."""


def _sniff_mat_kind(path: Path) -> str:
    """Peek at a .mat file's top-level variable names (no array data loaded)
    to tell a CWRU-format file from an MFPT- or wind-turbine-format one
    before picking a parser. Returns 'cwru', 'mfpt', 'wind_turbine', or
    'unknown' -- 'unknown' covers both an unrecognized struct shape and a
    malformed/corrupt file (untrusted upload), falling through to the
    existing CWRU path's own error reporting rather than leaking a raw scipy
    exception here.

    The three shapes are mutually exclusive by construction: CWRU names its
    channels '*_DE_time', MFPT wraps everything in one 'bearing' struct, and
    the wind-turbine set (Phase 7B) carries exactly {tach, vibration} at top
    level. Requiring BOTH wind-turbine names (not either) keeps a stray
    'vibration' variable in some other vendor's file from being claimed here.
    """
    from scipy.io import whosmat

    try:
        names = {entry[0] for entry in whosmat(str(path))}
    except Exception:  # noqa: BLE001 -- untrusted input, any parse failure just means "unknown"
        return "unknown"
    if any(name.endswith("_DE_time") for name in names):
        return "cwru"
    if "bearing" in names:
        return "mfpt"
    if {"tach", "vibration"} <= names:
        return "wind_turbine"
    return "unknown"


def parse_upload(
    path: Path,
    form: UploadForm,
    *,
    bearings_cfg: dict[str, Any],
    cwru_cfg: dict[str, Any] | None = None,
    mfpt_cfg: dict[str, Any] | None = None,
    wt_cfg: dict[str, Any] | None = None,
    mafaulda_cfg: dict[str, Any] | None = None,
    recipe: ParseRecipe | None = None,
) -> tuple[Case, str, str]:
    """Dispatch by extension (+ `form.mode` for tabular files) to the right
    parser. Returns (Case, kind, conversion_note) where `kind` is a
    log-friendly label (never file contents or the machine alias) and
    `conversion_note` is '' when no unit conversion applies.

    `recipe` is required for, and only used by, the Session G inference lane
    (.txt/.dat/.asc). Every other extension ignores it entirely, so the
    existing adapters' behaviour is byte-identical with or without it.
    """
    case, kind, conversion_note = _dispatch(
        path,
        form,
        bearings_cfg=bearings_cfg,
        cwru_cfg=cwru_cfg,
        mfpt_cfg=mfpt_cfg,
        wt_cfg=wt_cfg,
        mafaulda_cfg=mafaulda_cfg,
        recipe=recipe,
    )
    # Session INTAKE-HONEST: declared acquisition settings attach HERE, at the
    # single dispatch point, so every lane (template, recipe, WAV, UFF, .mat,
    # MAFAULDA) carries them uniformly and no individual adapter needs to know
    # they exist. None when the analyst declared nothing. The multi-axis
    # assembly's merge/identity paths both start from a parsed Case, so the
    # attachment survives merging without assembly.py knowing about it either.
    case.acquisition = acquisition_from_form(form)
    # Session GEOM-A: the declared machine geometry attaches at the SAME single
    # point and for the same reason — the .mat and MAFAULDA lanes build their
    # own MachineMeta and never call machine_from_form, so anything wired only
    # into that helper would reach four lanes of nine. A form that declared no
    # geometry leaves case.machine exactly as the adapter built it.
    case = apply_machine_geometry(case, form)
    return case, kind, conversion_note


def _dispatch(
    path: Path,
    form: UploadForm,
    *,
    bearings_cfg: dict[str, Any],
    cwru_cfg: dict[str, Any] | None = None,
    mfpt_cfg: dict[str, Any] | None = None,
    wt_cfg: dict[str, Any] | None = None,
    mafaulda_cfg: dict[str, Any] | None = None,
    recipe: ParseRecipe | None = None,
) -> tuple[Case, str, str]:
    ext = path.suffix.lower()

    # A recipe is supplied ONLY when the caller has already decided this file is
    # being read by schema inference -- either because its extension has no
    # adapter (.txt/.dat/.asc) or because its adapter failed and the analyst
    # confirmed an interpretation (Session G2's .csv/.xlsx fallback). So it wins
    # over extension dispatch: routing a confirmed recipe back into the template
    # parser would re-run the parse that already failed.
    if recipe is not None:
        return execute_recipe(path, recipe, form, bearings_cfg=bearings_cfg)

    if ext in INFERRED_EXTENSIONS:
        raise UnsupportedFormatError(
            f"{ext} uploads are read through schema inference, which needs a recipe"
        )

    if ext in TABULAR_EXTENSIONS:
        if form.mode == "mafaulda":
            # 8-column raw ABVT time series — a distinct CSV schema from the
            # spectrum/trend tables, disambiguated by form.mode exactly as those
            # two already are (Phase 8). .xlsx is not a MAFAULDA container, so
            # this mode only applies to .csv in practice.
            if mafaulda_cfg is None:
                raise UnsupportedFormatError("MAFAULDA upload requires mafaulda_cfg")
            return parse_mafaulda_csv(path, form, bearings_cfg=bearings_cfg, mafaulda_cfg=mafaulda_cfg)
        if form.mode == "trend":
            case, note = parse_trend(path, form, bearings_cfg=bearings_cfg)
            return case, "tabular_trend", note
        case, note = parse_spectrum(path, form, bearings_cfg=bearings_cfg)
        return case, "tabular_spectrum", note

    if ext in UFF_EXTENSIONS:
        return parse_uff(path, form, bearings_cfg=bearings_cfg)

    if ext in WAV_EXTENSIONS:
        return parse_wav(path, form, bearings_cfg=bearings_cfg)

    if ext in MAT_EXTENSIONS:
        mat_kind = _sniff_mat_kind(path)

        if mat_kind == "mfpt":
            if mfpt_cfg is None:
                raise UnsupportedFormatError(".mat upload (MFPT format) requires mfpt_cfg")
            return parse_mfpt_mat(path, form, bearings_cfg=bearings_cfg, mfpt_cfg=mfpt_cfg)

        if mat_kind == "wind_turbine":
            if wt_cfg is None:
                raise UnsupportedFormatError(".mat upload (wind-turbine format) requires wt_cfg")
            return parse_wind_turbine_mat(path, form, bearings_cfg=bearings_cfg, wt_cfg=wt_cfg)

        if cwru_cfg is None:
            raise UnsupportedFormatError(".mat upload requires cwru_cfg")
        # Product path: machine context from the form, signal from the *_DE_time
        # channel, no filename requirements. NOT adapters/cwru.py::to_case, which
        # parses the CWRU starter-set filename to build an eval-only answer key.
        return parse_cwru_mat(path, form, bearings_cfg=bearings_cfg, cwru_cfg=cwru_cfg)

    raise UnsupportedFormatError(f"unsupported file extension: {ext!r}")

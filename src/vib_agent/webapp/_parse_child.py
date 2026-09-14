"""Child-process entrypoint for sandboxed upload parsing (Phase 6).

Run only via `python -m vib_agent.webapp._parse_child` (see
webapp/parsing.py) -- never imported directly. Sets a best-effort memory
cap before doing anything else, then parses the upload and writes the
resulting Case (+ a {kind, conversion_note} sidecar) as JSON. Any failure
prints a single `PARSE_ERROR: <safe message>` line to stderr and exits
nonzero -- the parent process never sees a raw traceback from untrusted
input, and no filename is ever passed through a shell (argv only).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _cap_memory(memory_mb: int) -> None:
    try:
        import resource

        limit_bytes = memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit_bytes, limit_bytes))
    except (ImportError, ValueError, OSError):
        pass  # not available on this platform (e.g. Windows) -- best effort only


def main(argv: list[str]) -> int:
    if len(argv) != 10:
        print("PARSE_ERROR: internal error (bad child invocation)", file=sys.stderr)
        return 1

    (
        upload_path,
        form_json,
        bearings_cfg_path,
        cwru_cfg_path,
        mfpt_cfg_path,
        wt_cfg_path,
        mafaulda_cfg_path,
        out_path,
        memory_mb,
        recipe_json,
    ) = argv
    _cap_memory(int(memory_mb))

    try:
        from vib_agent.adapters.uploads import parse_upload
        from vib_agent.adapters.uploads.common import UploadForm

        from vib_agent.adapters.uploads.recipe import ParseRecipe

        form = UploadForm(**json.loads(form_json))
        # Re-validated HERE, inside the sandbox: the executor is never handed a
        # recipe that has not passed the closed schema in this process.
        recipe = ParseRecipe(**json.loads(recipe_json)) if recipe_json else None
        bearings_cfg = json.loads(Path(bearings_cfg_path).read_text())
        cwru_cfg_file = Path(cwru_cfg_path)
        cwru_cfg = json.loads(cwru_cfg_file.read_text()) if cwru_cfg_file.exists() else None
        mfpt_cfg_file = Path(mfpt_cfg_path)
        mfpt_cfg = json.loads(mfpt_cfg_file.read_text()) if mfpt_cfg_file.exists() else None
        wt_cfg_file = Path(wt_cfg_path)
        wt_cfg = json.loads(wt_cfg_file.read_text()) if wt_cfg_file.exists() else None
        mafaulda_cfg_file = Path(mafaulda_cfg_path)
        mafaulda_cfg = json.loads(mafaulda_cfg_file.read_text()) if mafaulda_cfg_file.exists() else None

        case, kind, conversion_note = parse_upload(
            Path(upload_path),
            form,
            bearings_cfg=bearings_cfg,
            cwru_cfg=cwru_cfg,
            mfpt_cfg=mfpt_cfg,
            wt_cfg=wt_cfg,
            mafaulda_cfg=mafaulda_cfg,
            recipe=recipe,
        )
    except MemoryError:
        print("PARSE_ERROR: file is too large or complex to parse within the memory limit", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 -- untrusted input must never crash the parent
        print(f"PARSE_ERROR: {exc}", file=sys.stderr)
        return 1

    Path(out_path).write_text(
        json.dumps({"case": json.loads(case.model_dump_json()), "kind": kind, "conversion_note": conversion_note})
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

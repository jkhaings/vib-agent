"""Child-process entrypoint for sampling an untrusted upload (Session G2).

Run only via `python -m vib_agent.webapp._sample_child` (see
webapp/parsing.py::sample_in_subprocess). A spreadsheet sample means opening
the workbook, which is parsing untrusted input — so it happens here, behind the
same memory cap and wall-clock timeout as a full parse, and never in the
request process. Any failure prints one `SAMPLE_ERROR: <safe message>` line to
stderr and exits nonzero.
"""

from __future__ import annotations

import sys
from pathlib import Path


def _cap_memory(memory_mb: int) -> None:
    try:
        import resource

        limit_bytes = memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit_bytes, limit_bytes))
    except (ImportError, ValueError, OSError):
        pass  # best effort only


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("SAMPLE_ERROR: internal error (bad child invocation)", file=sys.stderr)
        return 1
    upload_path, out_path, memory_mb = argv
    _cap_memory(int(memory_mb))
    try:
        from vib_agent.adapters.uploads.sample import read_sample

        sample = read_sample(Path(upload_path))
    except MemoryError:
        print("SAMPLE_ERROR: this file is too large or complex to read", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 -- untrusted input must not crash the parent
        print(f"SAMPLE_ERROR: {exc}", file=sys.stderr)
        return 1
    Path(out_path).write_text(sample)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

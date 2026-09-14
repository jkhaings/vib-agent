"""Regenerate tests/fixtures/intake_adversarial/ from its committed generator.

    python scripts/gen_intake_corpus.py

The generator (tests/intake_adversarial_corpus.py) is the source of truth; these
files are its output, committed because half of them exist to test BYTES — a
BOM, a bare CR, a cp1252 degree sign, a NUL — and bytes that only ever live
inside a test run have never been proved to survive a checkout, a merge, or an
editor that helpfully normalises line endings.

tests/test_intake_adversarial.py::test_committed_fixtures_match_the_generator
fails if the two ever drift, so running this is the ONLY way to change them.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

from tests.intake_adversarial_corpus import CORPUS, FIXTURE_DIR, write_corpus  # noqa: E402


def main() -> int:
    paths = write_corpus(FIXTURE_DIR)
    total = 0
    for item in CORPUS:
        size = paths[item.name].stat().st_size
        total += size
        print(f"  {item.name:26} {size:>7} bytes  {item.description}")
    print(f"\n{len(CORPUS)} files, {total / 1024:.0f} KB -> {FIXTURE_DIR.relative_to(_REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

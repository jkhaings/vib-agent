"""Phase 6 hosted webapp: upload a supported data file, get a drafted PDF
back. Single-purpose service — no accounts, no dashboards.
See CLAUDE.md's Phase 6 section for the full scope and privacy model.

Session DB-1 added `db/`, an account schema behind `STORE_BACKEND`. It is inert
on the default (`browser`) backend, which is what production runs: nothing here
imports it, no request reaches it, and the analysis path holds no state beyond
the in-memory job registry on either backend.
"""

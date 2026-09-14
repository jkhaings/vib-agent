"""Deterministic analytics core — THE product. Pure functions, no I/O, no LLM.

Layers:
  quality_gate  — data quality checks (always runs first)
  iso_classify  — Layer 1: ISO 20816-3 zone classification
  anomaly       — Layer 2: Welford z-score, Layer 3: Isolation Forest
  trend         — Layer 4: OLS trend regression + zone-boundary projection
  bearing_rca   — Layer 5: bearing fault frequencies + spectrum peak matching
"""

"""Adapters that normalize a real-world third-party data source into a Case.

Distinct from pdm_core/*.py's PeakSet adapters (peaks_from_ncd,
peaks_from_spectrum), which normalize a *reading* into the Layer-5 detector
boundary. Modules here (e.g. cwru.py) normalize a *whole external dataset*
into the pipeline's own Case/SensorData/Spectrum models — one layer up.
"""

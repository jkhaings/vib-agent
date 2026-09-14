"""Per-format upload adapter tests (Phase 6). Every fixture is generated
in-repo, deterministically, at test time -- no binary fixtures committed.
All fixtures seed the same BPFO signature already validated elsewhere in
this project (6206 bearing @ 1800 rpm -> BPFO ~= 107.03 Hz), so a correct
adapter should always land on `bearing_outer_race`.
"""

from __future__ import annotations

import csv
import io

import numpy as np
import openpyxl
import pytest
from scipy.io import wavfile

from vib_agent.adapters.uploads import UnsupportedFormatError, parse_upload
from vib_agent.adapters.uploads.common import UploadForm
from vib_agent.adapters.uploads.units import to_mms_rms
from vib_agent.config import load_config
from vib_agent.pipeline import run_analysis

BPFO_HZ = 107.03
RPM = 1800.0


@pytest.fixture
def bearings_cfg():
    return load_config("bearings")


@pytest.fixture
def cwru_cfg():
    return load_config("cwru")


def _spectrum_rows(peak_hz: float = BPFO_HZ, *, n: int = 400, fmax: float = 200.0) -> list[tuple[float, float]]:
    freqs = [i * fmax / n for i in range(n)]
    amps = [0.001] * n
    idx = min(range(n), key=lambda i: abs(freqs[i] - peak_hz))
    amps[idx] = 0.5
    return list(zip(freqs, amps))


def _write_csv(path, rows: list[tuple[float, float]], header: tuple[str, str]) -> None:
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for a, b in rows:
            w.writerow([a, b])


def _write_xlsx(path, rows: list[tuple[float, float]], header: tuple[str, str]) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(list(header))
    for a, b in rows:
        ws.append([a, b])
    wb.save(path)


def _bpfo_waveform(fs: float = 12000.0, duration: float = 1.0, carrier_hz: float = 3000.0) -> np.ndarray:
    n = int(fs * duration)
    t = np.arange(n) / fs
    signal = np.sin(2 * np.pi * carrier_hz * t) * (1 + 0.5 * np.sin(2 * np.pi * BPFO_HZ * t))
    signal += 0.01 * np.random.default_rng(1).standard_normal(n)
    return signal


class TestUnitsConversion:
    def test_no_conversion_when_already_mms_rms(self):
        values, note = to_mms_rms([1.0, 2.0])
        assert values == [1.0, 2.0]
        assert "no conversion" in note.lower()

    def test_in_s_to_mms(self):
        values, note = to_mms_rms([1.0], velocity_unit="in_s")
        assert values == pytest.approx([25.4])
        assert "×25.4" in note

    def test_peak_to_rms(self):
        values, note = to_mms_rms([1.0], detection_type="peak")
        assert values == pytest.approx([1.0 / 2**0.5])
        assert "÷√2" in note
        assert "sinusoidal" in note.lower()

    def test_peak_to_peak_to_rms(self):
        values, note = to_mms_rms([1.0], detection_type="peak_to_peak")
        assert values == pytest.approx([1.0 / (2 * 2**0.5)])
        assert "÷2√2" in note

    def test_combined_conversion(self):
        values, note = to_mms_rms([1.0], velocity_unit="in_s", detection_type="peak")
        assert values == pytest.approx([25.4 / 2**0.5])
        assert "×25.4" in note and "÷√2" in note


class TestTabularSpectrum:
    def test_csv_and_xlsx_parse_to_equivalent_cases(self, tmp_path, bearings_cfg):
        rows = _spectrum_rows()
        csv_path = tmp_path / "spec.csv"
        xlsx_path = tmp_path / "spec.xlsx"
        _write_csv(csv_path, rows, ("freq_hz", "amplitude"))
        _write_xlsx(xlsx_path, rows, ("freq_hz", "amplitude"))

        form = UploadForm(machine_alias="Pump", rpm=RPM, iso_group="2", iso_support="rigid", machine_type="motor", bearing_model="6206")
        case_csv, kind_csv, _ = parse_upload(csv_path, form, bearings_cfg=bearings_cfg)
        case_xlsx, kind_xlsx, _ = parse_upload(xlsx_path, form, bearings_cfg=bearings_cfg)

        assert kind_csv == kind_xlsx == "tabular_spectrum"
        assert case_csv.sensor_data.y_velocity_mm_sec == pytest.approx(case_xlsx.sensor_data.y_velocity_mm_sec)
        assert case_csv.spectra["y"].amplitude == pytest.approx(case_xlsx.spectra["y"].amplitude)

    def test_full_pipeline_identifies_bearing_outer_race(self, tmp_path, bearings_cfg):
        csv_path = tmp_path / "spec.csv"
        _write_csv(csv_path, _spectrum_rows(), ("freq_hz", "amplitude"))
        form = UploadForm(machine_alias="Pump", rpm=RPM, iso_group="2", iso_support="rigid", machine_type="motor", bearing_model="6206")
        case, kind, note = parse_upload(csv_path, form, bearings_cfg=bearings_cfg)

        iso_table = load_config("iso_zones")["zones"]
        from vib_agent.config import load_thresholds

        result = run_analysis(case, iso_table=iso_table, thresholds=load_thresholds())
        assert result.quality_gate.overall != "fail"
        assert result.iso is not None  # full ISO classification available (unlike CWRU)
        assert result.rca is not None
        assert any(m.fault == "bearing_outer_race" for m in result.rca.primary_findings)

    def test_missing_column_raises(self, tmp_path, bearings_cfg):
        csv_path = tmp_path / "bad.csv"
        _write_csv(csv_path, [(1.0, 2.0)], ("wrong", "columns"))
        form = UploadForm(machine_alias="Pump", rpm=RPM, iso_group="2", iso_support="rigid", machine_type="motor")
        with pytest.raises(ValueError, match="missing required column"):
            parse_upload(csv_path, form, bearings_cfg=bearings_cfg)

    def test_unit_conversion_note_present_when_non_default(self, tmp_path, bearings_cfg):
        csv_path = tmp_path / "spec.csv"
        _write_csv(csv_path, _spectrum_rows(), ("freq_hz", "amplitude"))
        form = UploadForm(
            machine_alias="Pump", rpm=RPM, iso_group="2", iso_support="rigid", machine_type="motor",
            velocity_unit="in_s", detection_type="peak",
        )
        _, _, note = parse_upload(csv_path, form, bearings_cfg=bearings_cfg)
        assert "×25.4" in note and "÷√2" in note


class TestTabularTrend:
    def test_trend_history_and_rising_severity(self, tmp_path, bearings_cfg):
        from datetime import datetime, timedelta, timezone

        csv_path = tmp_path / "trend.csv"
        now = datetime.now(timezone.utc)
        rows = []
        with open(csv_path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["timestamp", "overall_rms"])
            for i in range(30):
                ts = (now - timedelta(days=(30 - i))).isoformat()
                val = 2.0 + (3.5 - 2.0) * i / 29
                w.writerow([ts, val])

        form = UploadForm(machine_alias="TrendPump", rpm=RPM, iso_group="2", iso_support="rigid", machine_type="motor", mode="trend")
        case, kind, note = parse_upload(csv_path, form, bearings_cfg=bearings_cfg)
        assert kind == "tabular_trend"
        assert len(case.history) == 30

        from vib_agent.config import load_thresholds

        iso_table = load_config("iso_zones")["zones"]
        result = run_analysis(case, iso_table=iso_table, thresholds=load_thresholds())
        assert result.trend is not None
        assert result.trend.status == "ok"
        assert result.trend.severity in ("warn", "danger")

    def test_sparse_monthly_cadence_hedges(self, tmp_path, bearings_cfg):
        from datetime import datetime, timedelta, timezone

        csv_path = tmp_path / "sparse.csv"
        now = datetime.now(timezone.utc)
        with open(csv_path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["timestamp", "overall_rms"])
            for i in range(4):
                ts = (now - timedelta(days=30.44 * (4 - i))).isoformat()
                w.writerow([ts, 2.0 + 0.1 * i])

        form = UploadForm(machine_alias="SparsePump", rpm=RPM, iso_group="2", iso_support="rigid", machine_type="motor", mode="trend")
        case, kind, note = parse_upload(csv_path, form, bearings_cfg=bearings_cfg)

        from vib_agent.config import load_thresholds

        iso_table = load_config("iso_zones")["zones"]
        result = run_analysis(case, iso_table=iso_table, thresholds=load_thresholds())
        # Too few points / too little movement for a reliable trend -- hedged, not a crash.
        assert result.trend is not None


class TestUff:
    def _write_uff(self, path, data, x, func_type=1):
        import pyuff

        dset = pyuff.prepare_58(
            func_type=func_type,
            rsp_node=1, rsp_dir=1, ref_node=1, ref_dir=1,
            data=data.tolist(), x=x.tolist(), abscissa_spacing=1,
            orddenom_spec_data_type=0, ordinate_spec_data_type=0,
            abscissa_spec_data_type=0, z_axis_spec_data_type=0,
        )
        u = pyuff.UFF(str(path))
        u.write_sets([dset], mode="add")

    def test_waveform_recovers_bpfo_and_is_rca_only(self, tmp_path, bearings_cfg):
        fs = 12000.0
        signal = _bpfo_waveform(fs=fs)
        t = np.arange(len(signal)) / fs
        uff_path = tmp_path / "wave.uff"
        self._write_uff(uff_path, signal, t, func_type=1)

        form = UploadForm(machine_alias="UffPump", rpm=RPM, iso_group="2", iso_support="rigid", machine_type="motor", bearing_model="6206")
        case, kind, note = parse_upload(uff_path, form, bearings_cfg=bearings_cfg)
        assert kind == "uff_waveform"
        assert case.validation_scope == ["rca"]
        assert case.sensor_data.y_velocity_mm_sec is None

        from vib_agent.config import load_thresholds

        iso_table = load_config("iso_zones")["zones"]
        result = run_analysis(case, iso_table=iso_table, thresholds=load_thresholds())
        assert any(m.fault == "bearing_outer_race" for m in result.rca.primary_findings)

    def test_frequency_domain_record_applies_units_and_full_scope(self, tmp_path, bearings_cfg):
        rows = _spectrum_rows()
        freqs = np.array([r[0] for r in rows])
        amps = np.array([r[1] for r in rows])
        uff_path = tmp_path / "spec.uff"
        self._write_uff(uff_path, amps, freqs, func_type=4)

        form = UploadForm(
            machine_alias="UffSpecPump", rpm=RPM, iso_group="2", iso_support="rigid", machine_type="motor",
            bearing_model="6206", velocity_unit="in_s",
        )
        case, kind, note = parse_upload(uff_path, form, bearings_cfg=bearings_cfg)
        assert kind == "uff_spectrum"
        assert "×25.4" in note
        assert case.sensor_data.y_velocity_mm_sec is not None

    def test_wrong_dataset_type_raises(self, tmp_path, bearings_cfg):
        import pyuff

        # dataset 15 (nodes) instead of 58 -- something we don't support
        uff_path = tmp_path / "nodes.uff"
        dset = pyuff.prepare_15(node_nums=[1, 2], x=[0.0, 1.0], y=[0.0, 0.0], z=[0.0, 0.0])
        u = pyuff.UFF(str(uff_path))
        u.write_sets([dset], mode="add")

        form = UploadForm(machine_alias="Pump", rpm=RPM, iso_group="2", iso_support="rigid", machine_type="motor")
        with pytest.raises(ValueError, match="no dataset-58"):
            parse_upload(uff_path, form, bearings_cfg=bearings_cfg)


class TestWav:
    def _write_wav(self, path, signal, fs=12000):
        norm = signal / np.max(np.abs(signal)) * 0.8
        wavfile.write(str(path), fs, (norm * 32767).astype(np.int16))

    def test_unscaled_wav_is_rca_only_and_notes_unavailable(self, tmp_path, bearings_cfg):
        signal = _bpfo_waveform()
        wav_path = tmp_path / "wave.wav"
        self._write_wav(wav_path, signal)

        form = UploadForm(machine_alias="WavPump", rpm=RPM, iso_group="2", iso_support="rigid", machine_type="motor", bearing_model="6206")
        case, kind, note = parse_upload(wav_path, form, bearings_cfg=bearings_cfg)
        assert kind == "wav"
        assert case.validation_scope == ["rca"]
        assert "severity" in note.lower() and "not" in note.lower()

        from vib_agent.config import load_thresholds

        iso_table = load_config("iso_zones")["zones"]
        result = run_analysis(case, iso_table=iso_table, thresholds=load_thresholds())
        assert any(m.fault == "bearing_outer_race" for m in result.rca.primary_findings)

    def test_scaled_wav_gets_full_scope(self, tmp_path, bearings_cfg):
        signal = _bpfo_waveform()
        wav_path = tmp_path / "wave.wav"
        self._write_wav(wav_path, signal)

        form = UploadForm(
            machine_alias="WavPump2", rpm=RPM, iso_group="2", iso_support="rigid", machine_type="motor",
            bearing_model="6206", wav_sensitivity=0.05,
        )
        case, kind, note = parse_upload(wav_path, form, bearings_cfg=bearings_cfg)
        assert case.validation_scope == ["zone", "severity", "rca", "trend"]
        assert "sensitivity" in note.lower()

    def test_empty_wav_raises(self, tmp_path, bearings_cfg):
        wav_path = tmp_path / "empty.wav"
        wavfile.write(str(wav_path), 12000, np.array([], dtype=np.int16))
        form = UploadForm(machine_alias="Pump", rpm=RPM, iso_group="2", iso_support="rigid", machine_type="motor")
        with pytest.raises(ValueError, match="no audio samples"):
            parse_upload(wav_path, form, bearings_cfg=bearings_cfg)


class TestDispatchAndCwru:
    def test_unsupported_extension_raises(self, tmp_path, bearings_cfg):
        bad_path = tmp_path / "file.exe"
        bad_path.write_bytes(b"\x00\x01")
        form = UploadForm(machine_alias="Pump", rpm=RPM, iso_group="2", iso_support="rigid", machine_type="motor")
        with pytest.raises(UnsupportedFormatError):
            parse_upload(bad_path, form, bearings_cfg=bearings_cfg)

    def test_mat_requires_cwru_cfg(self, tmp_path, bearings_cfg):
        mat_path = tmp_path / "file.mat"
        mat_path.write_bytes(b"MATLAB fake header")
        form = UploadForm(machine_alias="Pump", rpm=RPM, iso_group="2", iso_support="rigid", machine_type="motor")
        with pytest.raises(UnsupportedFormatError, match="cwru_cfg"):
            parse_upload(mat_path, form, bearings_cfg=bearings_cfg)

    def test_mat_sniffs_cwru_shape_even_with_mfpt_cfg_present(self, tmp_path, bearings_cfg, cwru_cfg):
        from scipy.io import savemat

        mat_path = tmp_path / "file.mat"
        savemat(str(mat_path), {"X100_DE_time": np.zeros((100, 1))})
        form = UploadForm(machine_alias="Pump", rpm=RPM, iso_group="2", iso_support="rigid", machine_type="motor")
        # No mfpt_cfg supplied -- if the sniff mis-detected this as MFPT-shaped
        # it would raise "requires mfpt_cfg" instead of a CWRU-path error.
        with pytest.raises(UnsupportedFormatError, match="cwru_cfg"):
            parse_upload(mat_path, form, bearings_cfg=bearings_cfg)


class TestMfptDispatch:
    """MFPT-format .mat upload dispatch (Phase 7 webapp surface). Fixtures
    are synthetic, written with scipy.io.savemat -- same recipe as
    tests/test_mfpt_adapter.py's eval-path fixtures."""

    @pytest.fixture
    def mfpt_cfg(self):
        return load_config("mfpt")

    @staticmethod
    def _bpfo_modulated_waveform(*, fs: float, duration: float, carrier_hz: float, mod_hz: float) -> np.ndarray:
        t = np.arange(int(fs * duration)) / fs
        signal = np.sin(2 * np.pi * carrier_hz * t) * (1 + 0.5 * np.sin(2 * np.pi * mod_hz * t))
        signal += 0.01 * np.random.default_rng(1).standard_normal(signal.shape)
        return signal

    def test_mat_sniffs_mfpt_shape_and_requires_mfpt_cfg(self, tmp_path, bearings_cfg):
        from scipy.io import savemat

        mat_path = tmp_path / "field_upload.mat"
        signal = self._bpfo_modulated_waveform(fs=6000.0, duration=0.5, carrier_hz=1500.0, mod_hz=6.0)
        savemat(str(mat_path), {"bearing": {"sr": 6000.0, "gs": signal.reshape(-1, 1), "load": "", "rate": 1.0}})
        form = UploadForm(machine_alias="Pump", rpm=RPM, iso_group="2", iso_support="rigid", machine_type="motor")
        with pytest.raises(UnsupportedFormatError, match="mfpt_cfg"):
            parse_upload(mat_path, form, bearings_cfg=bearings_cfg)

    def test_real_world_shaped_upload_uses_embedded_orders(self, tmp_path, bearings_cfg, mfpt_cfg):
        from scipy.io import savemat

        mat_path = tmp_path / "field_upload.mat"
        signal = self._bpfo_modulated_waveform(fs=6000.0, duration=1.0, carrier_hz=1500.0, mod_hz=6.0)
        savemat(
            str(mat_path),
            {
                "bearing": {
                    "sr": 6000.0,
                    "gs": signal.reshape(-1, 1),
                    "load": np.array([]),
                    "rate": 1.0,
                    "ball": 3.0,
                    "cage": 0.4,
                    "outer": 6.0,
                    "inner": 8.0,
                }
            },
        )
        form = UploadForm(machine_alias="WindTurbineBearing", rpm=60.0, iso_group="2", iso_support="rigid", machine_type="motor")
        case, kind, note = parse_upload(mat_path, form, bearings_cfg=bearings_cfg, mfpt_cfg=mfpt_cfg)

        assert kind == "mfpt_mat"
        assert case.source == "mfpt"
        assert case.validation_scope == ["rca"]
        assert case.expected.fault_type == "real_world"
        assert case.expected.faults == []  # never a fabricated answer key
        assert case.machine.bearing.n_balls == 14  # round(6.0 + 8.0)
        assert note == ""  # rate*60 = 60 rpm matches the form-entered rpm exactly

    def test_rig_shaped_upload_uses_form_bearing_and_flags_rpm_mismatch(self, tmp_path, bearings_cfg, mfpt_cfg):
        from scipy.io import savemat

        mat_path = tmp_path / "field_upload.mat"
        signal = self._bpfo_modulated_waveform(fs=12000.0, duration=0.5, carrier_hz=3000.0, mod_hz=81.12)
        savemat(str(mat_path), {"bearing": {"sr": 12000.0, "gs": signal.reshape(-1, 1), "load": "270", "rate": 25.0}})
        form = UploadForm(
            machine_alias="Pump", rpm=1000.0, iso_group="2", iso_support="rigid", machine_type="motor", bearing_model="6206"
        )
        case, kind, note = parse_upload(mat_path, form, bearings_cfg=bearings_cfg, mfpt_cfg=mfpt_cfg)

        assert kind == "mfpt_mat"
        assert case.expected is None  # no known label to attach for a generic rig-shaped upload
        assert case.machine.bearing.model == "6206"
        assert case.sensor_data.rpm == pytest.approx(25.0 * 60.0)
        assert "1000.0" in note and "1500.0" in note  # file rpm vs form rpm both surfaced

    def test_rig_shaped_upload_without_form_bearing_defaults_to_mfpt_nice(self, tmp_path, bearings_cfg, mfpt_cfg):
        from scipy.io import savemat

        mat_path = tmp_path / "field_upload.mat"
        signal = self._bpfo_modulated_waveform(fs=12000.0, duration=0.5, carrier_hz=3000.0, mod_hz=81.12)
        savemat(str(mat_path), {"bearing": {"sr": 12000.0, "gs": signal.reshape(-1, 1), "load": "270", "rate": 25.0}})
        form = UploadForm(machine_alias="Pump", rpm=1500.0, iso_group="2", iso_support="rigid", machine_type="motor")
        case, kind, note = parse_upload(mat_path, form, bearings_cfg=bearings_cfg, mfpt_cfg=mfpt_cfg)

        assert case.machine.bearing.n_balls == bearings_cfg["bearings"]["MFPT_NICE"]["n_balls"]
        assert note == ""  # 25Hz*60 = 1500rpm matches the form exactly -- nothing to flag

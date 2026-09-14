"""Reference test packets lifted verbatim from reference/flows.json's
'Flow T — Test Harness' inject nodes. Each REFERENCE_CASES entry's key is
the reference's own test name; the payload is the NCD packet's addr,
top-level battery_percent, and sensor_data exactly as captured in the
Node-RED export. Every pdm_core port is checked against the diagnosis its
name asserts (e.g. T07 -> imbalance, T12 -> bearing_outer_race).

T14 (baseline seed), T17-T19 (lifecycle mode events), T20 (malformed
packet), and T21 (unknown MAC) exercise message-routing / stateful
lifecycle concerns that live above pdm_core (mac resolution, sensor
lifecycle tracking) — out of scope for Phase 1's pure per-machine
functions; deferred to the agent/tools layer. T22/T23 (30-day backfill)
are reproduced in test_trend.py via their generator formula, not as static
packets.
"""

from __future__ import annotations

REFERENCE_CASES: dict[str, dict] = {
    "T01_healthy_zone_a": {
        "mac": "TEST-PUMP-01",
        "battery_percent": 98.56,
        "sensor_data": {
            "mode": 0, "msg_type": "regular", "odr": "800Hz", "temperature": 23.67,
            "x_rms_ACC_G": 0.023096, "x_max_ACC_G": 0.050813, "x_velocity_mm_sec": 0.25,
            "x_displacement_mm": 0.000966, "x_peak_one_Hz": 41.2, "x_peak_two_Hz": 53.7, "x_peak_three_Hz": 84.1,
            "y_rms_ACC_G": 0.033299, "y_max_ACC_G": 0.073262, "y_velocity_mm_sec": 0.3,
            "y_displacement_mm": 0.000965, "y_peak_one_Hz": 49.5, "y_peak_two_Hz": 81.3, "y_peak_three_Hz": 264.4,
            "z_rms_ACC_G": 0.026047, "z_max_ACC_G": 0.057305, "z_velocity_mm_sec": 0.22,
            "z_displacement_mm": 0.000663, "z_peak_one_Hz": 52.8, "z_peak_two_Hz": 184.6, "z_peak_three_Hz": 234.2,
            "rpm": 1800,
        },
    },
    "T02_machine_off": {
        "mac": "TEST-PUMP-01",
        "battery_percent": 98.56,
        "sensor_data": {
            "mode": 0, "msg_type": "regular", "odr": "800Hz", "temperature": 23.67,
            "x_rms_ACC_G": 2.4e-05, "x_max_ACC_G": 5.3e-05, "x_velocity_mm_sec": 0.003,
            "x_displacement_mm": 3.8e-05, "x_peak_one_Hz": 12.5, "x_peak_two_Hz": 47.3, "x_peak_three_Hz": 88.1,
            "y_rms_ACC_G": 7.6e-05, "y_max_ACC_G": 0.000167, "y_velocity_mm_sec": 0.005,
            "y_displacement_mm": 3.4e-05, "y_peak_one_Hz": 23.7, "y_peak_two_Hz": 91.4, "y_peak_three_Hz": 156.2,
            "z_rms_ACC_G": 1.1e-05, "z_max_ACC_G": 2.5e-05, "z_velocity_mm_sec": 0.002,
            "z_displacement_mm": 3.6e-05, "z_peak_one_Hz": 8.9, "z_peak_two_Hz": 64.2, "z_peak_three_Hz": 201.5,
            "rpm": 15,
        },
    },
    "T03_zone_b_clean": {
        "mac": "TEST-COMP-01",
        "battery_percent": 98.56,
        "sensor_data": {
            "mode": 0, "msg_type": "regular", "odr": "800Hz", "temperature": 23.67,
            "x_rms_ACC_G": 0.047515, "x_max_ACC_G": 0.104533, "x_velocity_mm_sec": 1.8,
            "x_displacement_mm": 0.006953, "x_peak_one_Hz": 41.2, "x_peak_two_Hz": 53.7, "x_peak_three_Hz": 84.1,
            "y_rms_ACC_G": 0.06343, "y_max_ACC_G": 0.139546, "y_velocity_mm_sec": 2.0,
            "y_displacement_mm": 0.006431, "y_peak_one_Hz": 49.5, "y_peak_two_Hz": 81.3, "y_peak_three_Hz": 264.4,
            "z_rms_ACC_G": 0.064276, "z_max_ACC_G": 0.141407, "z_velocity_mm_sec": 1.9,
            "z_displacement_mm": 0.005727, "z_peak_one_Hz": 52.8, "z_peak_two_Hz": 184.6, "z_peak_three_Hz": 234.2,
            "rpm": 1800,
        },
    },
    "T04_ab_boundary": {
        "mac": "TEST-MOTOR-01",
        "battery_percent": 98.56,
        "sensor_data": {
            "mode": 0, "msg_type": "regular", "odr": "800Hz", "temperature": 23.67,
            "x_rms_ACC_G": 0.034316, "x_max_ACC_G": 0.075496, "x_velocity_mm_sec": 1.3,
            "x_displacement_mm": 0.005022, "x_peak_one_Hz": 41.2, "x_peak_two_Hz": 53.7, "x_peak_three_Hz": 84.1,
            "y_rms_ACC_G": 0.044718, "y_max_ACC_G": 0.09838, "y_velocity_mm_sec": 1.41,
            "y_displacement_mm": 0.004534, "y_peak_one_Hz": 49.5, "y_peak_two_Hz": 81.3, "y_peak_three_Hz": 264.4,
            "z_rms_ACC_G": 0.047699, "z_max_ACC_G": 0.104939, "z_velocity_mm_sec": 1.41,
            "z_displacement_mm": 0.00425, "z_peak_one_Hz": 52.8, "z_peak_two_Hz": 184.6, "z_peak_three_Hz": 234.2,
            "rpm": 1800,
        },
    },
    "T05_bc_boundary_zone_c": {
        "mac": "TEST-MOTOR-01",
        "battery_percent": 98.56,
        "sensor_data": {
            "mode": 0, "msg_type": "regular", "odr": "800Hz", "temperature": 23.67,
            "x_rms_ACC_G": 0.019221, "x_max_ACC_G": 0.042287, "x_velocity_mm_sec": 1.0,
            "x_displacement_mm": 0.005305, "x_peak_one_Hz": 30, "x_peak_two_Hz": 41.2, "x_peak_three_Hz": 53.7,
            "y_rms_ACC_G": 0.054012, "y_max_ACC_G": 0.118825, "y_velocity_mm_sec": 2.81,
            "y_displacement_mm": 0.014908, "y_peak_one_Hz": 30, "y_peak_two_Hz": 81.3, "y_peak_three_Hz": 264.4,
            "z_rms_ACC_G": 0.054012, "z_max_ACC_G": 0.118825, "z_velocity_mm_sec": 2.81,
            "z_displacement_mm": 0.014908, "z_peak_one_Hz": 30, "z_peak_two_Hz": 184.6, "z_peak_three_Hz": 234.2,
            "rpm": 1800,
        },
    },
    "T06_cd_boundary_zone_d": {
        "mac": "TEST-COMP-01",
        "battery_percent": 98.56,
        "sensor_data": {
            "mode": 0, "msg_type": "regular", "odr": "800Hz", "temperature": 23.67,
            "x_rms_ACC_G": 0.028832, "x_max_ACC_G": 0.06343, "x_velocity_mm_sec": 1.5,
            "x_displacement_mm": 0.007958, "x_peak_one_Hz": 30, "x_peak_two_Hz": 41.2, "x_peak_three_Hz": 53.7,
            "y_rms_ACC_G": 0.086688, "y_max_ACC_G": 0.190713, "y_velocity_mm_sec": 4.51,
            "y_displacement_mm": 0.023926, "y_peak_one_Hz": 30, "y_peak_two_Hz": 81.3, "y_peak_three_Hz": 264.4,
            "z_rms_ACC_G": 0.086688, "z_max_ACC_G": 0.190713, "z_velocity_mm_sec": 4.51,
            "z_displacement_mm": 0.023926, "z_peak_one_Hz": 30, "z_peak_two_Hz": 184.6, "z_peak_three_Hz": 234.2,
            "rpm": 1800,
        },
    },
    "T07_imbalance": {
        "mac": "TEST-PUMP-01",
        "battery_percent": 98.56,
        "sensor_data": {
            "mode": 0, "msg_type": "regular", "odr": "800Hz", "temperature": 23.67,
            "x_rms_ACC_G": 0.010559, "x_max_ACC_G": 0.023229, "x_velocity_mm_sec": 0.4,
            "x_displacement_mm": 0.001545, "x_peak_one_Hz": 41.2, "x_peak_two_Hz": 53.7, "x_peak_three_Hz": 84.1,
            "y_rms_ACC_G": 0.120773, "y_max_ACC_G": 0.265701, "y_velocity_mm_sec": 6.5,
            "y_displacement_mm": 0.035673, "y_peak_one_Hz": 29, "y_peak_two_Hz": 81.3, "y_peak_three_Hz": 264.4,
            "z_rms_ACC_G": 0.126347, "z_max_ACC_G": 0.277964, "z_velocity_mm_sec": 6.8,
            "z_displacement_mm": 0.037319, "z_peak_one_Hz": 29, "z_peak_two_Hz": 184.6, "z_peak_three_Hz": 234.2,
            "rpm": 1740,
        },
    },
    "T08_bent_shaft_uncoupled": {
        "mac": "TEST-FAN-01",
        "battery_percent": 98.56,
        "sensor_data": {
            "mode": 0, "msg_type": "regular", "odr": "800Hz", "temperature": 23.67,
            "x_rms_ACC_G": 0.083292, "x_max_ACC_G": 0.183242, "x_velocity_mm_sec": 6.5,
            "x_displacement_mm": 0.051725, "x_peak_one_Hz": 20, "x_peak_two_Hz": 17.3, "x_peak_three_Hz": 56.2,
            "y_rms_ACC_G": 0.032035, "y_max_ACC_G": 0.070478, "y_velocity_mm_sec": 2.5,
            "y_displacement_mm": 0.019894, "y_peak_one_Hz": 20, "y_peak_two_Hz": 24.7, "y_peak_three_Hz": 84.1,
            "z_rms_ACC_G": 0.028191, "z_max_ACC_G": 0.06202, "z_velocity_mm_sec": 2.2,
            "z_displacement_mm": 0.017507, "z_peak_one_Hz": 20, "z_peak_two_Hz": 27.5, "z_peak_three_Hz": 64.2,
            "rpm": 1200,
        },
    },
    "T09_angular_misalignment": {
        "mac": "TEST-MOTOR-01",
        "battery_percent": 98.56,
        "sensor_data": {
            "mode": 0, "msg_type": "regular", "odr": "800Hz", "temperature": 23.67,
            "x_rms_ACC_G": 0.124938, "x_max_ACC_G": 0.274863, "x_velocity_mm_sec": 6.5,
            "x_displacement_mm": 0.034484, "x_peak_one_Hz": 30, "x_peak_two_Hz": 41.2, "x_peak_three_Hz": 53.7,
            "y_rms_ACC_G": 0.048053, "y_max_ACC_G": 0.105717, "y_velocity_mm_sec": 2.5,
            "y_displacement_mm": 0.013263, "y_peak_one_Hz": 30, "y_peak_two_Hz": 81.3, "y_peak_three_Hz": 264.4,
            "z_rms_ACC_G": 0.044209, "z_max_ACC_G": 0.097259, "z_velocity_mm_sec": 2.3,
            "z_displacement_mm": 0.012202, "z_peak_one_Hz": 30, "z_peak_two_Hz": 184.6, "z_peak_three_Hz": 234.2,
            "rpm": 1800,
        },
    },
    "T10_parallel_misalignment": {
        "mac": "TEST-PUMP-01",
        "battery_percent": 98.56,
        "sensor_data": {
            "mode": 0, "msg_type": "regular", "odr": "800Hz", "temperature": 23.67,
            "x_rms_ACC_G": 0.009611, "x_max_ACC_G": 0.021143, "x_velocity_mm_sec": 0.5,
            "x_displacement_mm": 0.002653, "x_peak_one_Hz": 30, "x_peak_two_Hz": 41.2, "x_peak_three_Hz": 53.7,
            "y_rms_ACC_G": 0.211433, "y_max_ACC_G": 0.465153, "y_velocity_mm_sec": 5.5,
            "y_displacement_mm": 0.014589, "y_peak_one_Hz": 60, "y_peak_two_Hz": 30, "y_peak_three_Hz": 264.4,
            "z_rms_ACC_G": 0.222966, "z_max_ACC_G": 0.490525, "z_velocity_mm_sec": 5.8,
            "z_displacement_mm": 0.015385, "z_peak_one_Hz": 60, "z_peak_two_Hz": 30, "z_peak_three_Hz": 234.2,
            "rpm": 1800,
        },
    },
    "T11_looseness": {
        "mac": "TEST-PUMP-01",
        "battery_percent": 98.56,
        "sensor_data": {
            "mode": 0, "msg_type": "regular", "odr": "800Hz", "temperature": 23.67,
            "x_rms_ACC_G": 0.033637, "x_max_ACC_G": 0.074002, "x_velocity_mm_sec": 3.5,
            "x_displacement_mm": 0.037136, "x_peak_one_Hz": 15, "x_peak_two_Hz": 30, "x_peak_three_Hz": 45,
            "y_rms_ACC_G": 0.073041, "y_max_ACC_G": 0.160689, "y_velocity_mm_sec": 3.8,
            "y_displacement_mm": 0.02016, "y_peak_one_Hz": 30, "y_peak_two_Hz": 60, "y_peak_three_Hz": 90,
            "z_rms_ACC_G": 0.030754, "z_max_ACC_G": 0.067659, "z_velocity_mm_sec": 3.2,
            "z_displacement_mm": 0.033953, "z_peak_one_Hz": 15, "z_peak_two_Hz": 45, "z_peak_three_Hz": 60,
            "rpm": 1800,
        },
    },
    "T12_bpfo_bearing_fault": {
        "mac": "TEST-COMP-01",
        "battery_percent": 98.56,
        "sensor_data": {
            "mode": 0, "msg_type": "regular", "odr": "800Hz", "temperature": 23.67,
            "x_rms_ACC_G": 0.013199, "x_max_ACC_G": 0.029037, "x_velocity_mm_sec": 0.5,
            "x_displacement_mm": 0.001931, "x_peak_one_Hz": 41.2, "x_peak_two_Hz": 53.7, "x_peak_three_Hz": 84.1,
            "y_rms_ACC_G": 0.357017, "y_max_ACC_G": 0.785437, "y_velocity_mm_sec": 5.2,
            "y_displacement_mm": 0.007723, "y_peak_one_Hz": 107.16, "y_peak_two_Hz": 214.32, "y_peak_three_Hz": 264.4,
            "z_rms_ACC_G": 0.329554, "z_max_ACC_G": 0.725019, "z_velocity_mm_sec": 4.8,
            "z_displacement_mm": 0.007129, "z_peak_one_Hz": 107.16, "z_peak_two_Hz": 214.32, "z_peak_three_Hz": 234.2,
            "rpm": 1800,
        },
    },
    "T13_severe_misalignment": {
        "mac": "TEST-COMP-01",
        "battery_percent": 98.56,
        "sensor_data": {
            "mode": 0, "msg_type": "regular", "odr": "800Hz", "temperature": 23.67,
            "x_rms_ACC_G": 0.111483, "x_max_ACC_G": 0.245262, "x_velocity_mm_sec": 5.8,
            "x_displacement_mm": 0.03077, "x_peak_one_Hz": 30, "x_peak_two_Hz": 60, "x_peak_three_Hz": 90,
            "y_rms_ACC_G": 0.09995, "y_max_ACC_G": 0.219891, "y_velocity_mm_sec": 5.2,
            "y_displacement_mm": 0.027587, "y_peak_one_Hz": 30, "y_peak_two_Hz": 60, "y_peak_three_Hz": 120,
            "z_rms_ACC_G": 0.094184, "z_max_ACC_G": 0.207205, "z_velocity_mm_sec": 4.9,
            "z_displacement_mm": 0.025995, "z_peak_one_Hz": 30, "z_peak_two_Hz": 60, "z_peak_three_Hz": 150,
            "rpm": 1800,
        },
    },
    "T15_zscore_spike": {
        "mac": "TEST-PUMP-01",
        "battery_percent": 98.56,
        "sensor_data": {
            "mode": 0, "msg_type": "regular", "odr": "800Hz", "temperature": 23.67,
            "x_rms_ACC_G": 0.028832, "x_max_ACC_G": 0.06343, "x_velocity_mm_sec": 1.5,
            "x_displacement_mm": 0.007958, "x_peak_one_Hz": 30, "x_peak_two_Hz": 41.2, "x_peak_three_Hz": 53.7,
            "y_rms_ACC_G": 0.096106, "y_max_ACC_G": 0.211433, "y_velocity_mm_sec": 5.0,
            "y_displacement_mm": 0.026526, "y_peak_one_Hz": 30, "y_peak_two_Hz": 81.3, "y_peak_three_Hz": 264.4,
            "z_rms_ACC_G": 0.094184, "z_max_ACC_G": 0.207205, "z_velocity_mm_sec": 4.9,
            "z_displacement_mm": 0.025995, "z_peak_one_Hz": 30, "z_peak_two_Hz": 184.6, "z_peak_three_Hz": 234.2,
            "rpm": 1800,
        },
    },
    "T16_zscore_zero_std": {
        "mac": "TEST-PUMP-01",
        "battery_percent": 98.56,
        "sensor_data": {
            "mode": 0, "msg_type": "regular", "odr": "800Hz", "temperature": 23.67,
            "x_rms_ACC_G": 0.018476, "x_max_ACC_G": 0.040653, "x_velocity_mm_sec": 0.2,
            "x_displacement_mm": 0.000773, "x_peak_one_Hz": 41.2, "x_peak_two_Hz": 53.7, "x_peak_three_Hz": 84.1,
            "y_rms_ACC_G": 0.02442, "y_max_ACC_G": 0.053725, "y_velocity_mm_sec": 0.22,
            "y_displacement_mm": 0.000707, "y_peak_one_Hz": 49.5, "y_peak_two_Hz": 81.3, "y_peak_three_Hz": 264.4,
            "z_rms_ACC_G": 0.024864, "z_max_ACC_G": 0.054702, "z_velocity_mm_sec": 0.21,
            "z_displacement_mm": 0.000633, "z_peak_one_Hz": 52.8, "z_peak_two_Hz": 184.6, "z_peak_three_Hz": 234.2,
            "rpm": 1800,
        },
    },
}

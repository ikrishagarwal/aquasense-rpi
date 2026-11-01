# Configuration

`default.yaml` and `simulation.yaml` select simulation. `hardware.yaml` records the
proposed contract but is rejected by the runtime. Each profile is standalone, with
no implicit merging. Shipped JSON-subset YAML parses using stdlib; general YAML
requires PyYAML 6.0.3. The safe YAML loader rejects duplicate keys, aliases and unsafe
tags. Schema validation rejects missing/unknown keys, wrong types, nonfinite values,
conflicting pins, unsupported modes and unsafe drain/TLS options.

All shipped profiles disable drain, vision and MQTT. `null` means unresolved, not
an implicit calibrated value. `device_id` must be the canonical UUID registered by
the backend before MQTT is enabled. A local simulation can leave it null; those
unregistered records must not be sent under an invented identity.

Relative database paths resolve from the profile's parent project directory
(`config/../`); deployment profiles use absolute `/var/lib/purifier` paths. Critical
SQLite durability is FULL and the writer queue defaults to 128 entries, maximum
4096. Pulse timing buffers are independently bounded at 4096 entries.

Pins use BCM numbering. `hardware_verified` records an inventory state and cannot
authorize physical actuation. `drain.enabled: true` is always rejected by this
implementation. Driver polarity, physical enable, cutoff, maximum duration and quiet
period must be established on a bench before a future real adapter is considered.

Initial settings: 3 s merge interval, 300 s maximum session, 450 pulses/litre
(uncalibrated), 5 s recognition deadline, TDS every 2 s, temperature every 5 s,
approximate pH every 10 s and telemetry every 15 s. Polling retries back off per
sensor, capped at 60 s or its configured interval if longer. Invalid/stale readings
produce null values plus quality, never invented measurements. A single float
provides LOW/OK/UNKNOWN, not percentage or tank volume.

The MQTT section specifies host/port, optional CA file, environment variable names
for per-device credentials, verified TLS and outbox batches of 1–20. Missing optional
transport packages/credentials keep the controller local and log unavailability.
The vision socket must be absolute. A real model_path is rejected; model-specific
settings are held for future hardware integration. IPC alone does not grant capture
permission: proximity/shutter eligibility must also be supplied by validated inputs.

TDS calibration, pH calibration, ADC address/conditioning and camera orientation
remain unresolved. Numeric alert thresholds are not enabled by default. A TDS
threshold requires a positive smaller hysteresis; debounce defaults to two distinct
fresh readings. A documented filter capacity enables its measured-volume alert at
the configurable fraction (default 0.9). Neither measurement establishes water safety
or true remaining filter life. See calibration.md before changing calibration flags.

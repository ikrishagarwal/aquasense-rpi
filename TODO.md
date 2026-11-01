# Remaining work and completion gates

This repository now has an integrated, tested simulation implementation. It is
**not a hardware-ready M0–M10 release**. Checked items below describe software
actually present; unchecked items require missing tools, physical contracts,
measurements, or the separate backend upgrade. Do not energize a valve to work
around an unchecked gate.

## Implemented software

- [x] M0 package, profiles, electrical contract and read-only environment inventory.
- [x] M1 strict config, structured logs, optional subsystem isolation, single bounded
  writer, signal handling and bounded shutdown with valve-close-first ordering.
- [x] M2 typed ports, fake clock, sensor/recognition/valve simulation and explicit faults.
- [x] M3 bounded thread-safe pulse buffer, merge/max-duration session state machine,
  checkpointing, optional asynchronous recognition, and simulated dispense CLI.
- [x] M4 transactional migrations, session/outbox commits, incomplete recovery,
  revision ACKs, retention, WAL maintenance, integrity checks and visible queue faults.
- [x] M5 serialized ADC port, calibrated conversion primitives, DS18B20 file reader,
  safe tank debounce, sensor freshness and independent bounded retry policies.
- [x] M6 metadata-only bounded IPC client/simulated worker, ephemeral capture/model
  ports, cosine threshold/margin matching, authenticated encrypted enrollment,
  shutter-checked enrollment service and local deletion path.
- [x] M7 optional verified-TLS Paho adapter, bounded queues, retry/commit-ACK sync,
  telemetry/LWT, strict command intake, fixtures and backend migration handoff.
- [x] M8 simulation-only interlocks, durable lifecycle/replay handling, automatic
  supervision and close-on-fault. The deployed runtime rejects all drain actuation.
- [x] M9 durable debounced/hysteretic alert transitions, calibrated measured-volume
  filter counter, periodic health/telemetry and bounded sensor retention.
- [x] M10 separate restricted simulation service templates, operations/security docs,
  calibration report command, fault tests and wall-clock soak harness.

## Blocked development validation

- [ ] Install pinned pytest/Ruff/mypy and complete lint/format/type verification,
  resolving any findings. Package installation is unavailable in this sandbox;
  these gates are not asserted passed. Stdlib test execution is available.
- [ ] Resolve/pin transitive dependencies and verify Python/PyYAML/Paho/crypto on the
  selected Pi OS/interpreter. Host execution is Python 3.14.8 x86_64 only.
- [ ] Exercise actual Unix IPC and broker TLS/Paho networking outside the sandbox's
  socket restrictions. Current tests cover protocol parsing, missing worker,
  transport configuration, and an in-memory backend, not live socket success.
- [ ] Verify/install systemd units on the Pi. Host unit verification was denied by
  socket-option restrictions; deployment paths/accounts are not installed here.

## Blocked hardware implementation and acceptance

- [ ] Inventory the actual Pi OS/kernel/Python, camera model/orientation, sensor
  boards, electrical levels, conditioning, pulls, grounds and driver polarity.
- [ ] Bind `PulseCounter` to the verified GPIO edge API. Confirm callback timestamp/
  snapshot ordering on that backend; calibrate 20 pours at each target volume.
- [ ] Implement the specific ADS1115 bus backend and board-specific TDS/pH transfer
  functions. Bind float, proximity and shutter GPIO adapters and validate unplug,
  stuck and open-wire states. Core camera eligibility remains false without these.
- [ ] Select/license/benchmark a real ARM64 face runtime/model; implement its exact
  detector, alignment, normalization and embedding API and actual Picamera2 capture.
  Generic model/camera ports are not a substitute for that adapter.
- [ ] Connect physical enrollment capture to the worker's administrative interface,
  verify matcher cache invalidation on deletion, and run real-person trials.
- [ ] Verify physical enable/emergency stop, normally closed driver and an independent
  automatic cutoff. Only then implement/review the physical valve adapter and
  deliberate bench-enable procedure; complete 20 controlled fault-inclusive trials.
- [ ] Measure Pi CPU/RSS, sensor calibration, thermal/throttling status, storage
  durability and hardware latency. Unknown throttling is currently null.

## Backend and endurance gates

- [ ] Upgrade the adjacent backend according to BACKEND.md. Supply a registered
  device UUID and test broker credentials; never use fixture IDs as live identity.
- [ ] Validate ten offline sessions, dropped commit ACKs, revisions, lifecycle ACK
  persistence and reconnects against the upgraded real backend.
- [ ] Run the representative >=12-hour Pi soak with real sensor/vision/MQTT load,
  power interruption and physical cutoff tests. The short host smoke soak is not
  equivalent and is not presented as this acceptance result.

These dependencies prevent an honest claim that every milestone is complete.
Backend code remains outside this repository's authorized implementation scope.

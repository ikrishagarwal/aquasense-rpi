# AGENTS.md — Smart Water Purifier Raspberry Pi Edge Software

## Purpose

This document is the implementation contract for coding agents working on the **Raspberry Pi 3 Model B+ edge software** for a camera-assisted water purifier add-on. Build a reliable, maintainable, offline-first controller for a **low-voltage bench prototype**. There is **no four-week deadline**. Work in dependency order, validate each layer, and prefer correctness and safety over speed or adding features.

The edge application measures dispensed water, captures a short camera burst for on-device face identification, reads water-quality and tank sensors, persists events locally, synchronizes to a remote backend over MQTT/TLS, and executes a tightly interlocked remote **drain tank** command.

Do not implement out-of-scope features without an explicit request: predictive filter-life ML, leaderboards, elderly/kids modes, voice assistants, multi-device dashboards, or reject-water tracking. A simple filter-usage counter and threshold alert are in scope.

## Operating assumptions and non-negotiables

- Target: **Raspberry Pi 3 B+, quad-core Cortex-A53, 1 GB RAM**, Raspberry Pi OS Lite 64-bit, Python 3. Use a Pi Zero 2 W only if explicitly requested.
- Keep FastAPI, PostgreSQL, MQTT broker, and Flutter app **off the Pi**. The Pi runs only edge control, local SQLite, vision inference, and an outbound MQTT client.
- The purifier must continue normal, local measurement with no Wi-Fi, broker, backend, or vision worker. **Never allow these failures to operate the valve.**
- Treat **flow measurement, persistence, and safety** as higher priority than camera, telemetry, and alerts.
- All GPIO inputs must be **3.3 V safe**. Never connect 5 V signals directly to a Pi GPIO. The 12 V valve uses a separate power supply, appropriate driver and flyback protection. Prefer a normally closed valve with an independent physical enable and hardware cutoff.
- No raw face photos are written to disk, sent to the server, or placed in logs. Only user-linked encrypted embeddings are retained. Captures must be ephemeral. Provide Guest mode and an enrollment deletion path.
- Never claim a TDS or approximate pH reading proves water safety. A single float switch provides a threshold state (e.g., LOW/OK/UNKNOWN), **not** an accurate tank percentage.
- Sensor failures must be represented as errors or stale/unknown readings; do not fabricate valid measurements.
- Document board and sensor variants, supply voltages, pin numbers, thresholds, library versions, and any assumptions in configuration and `docs/` before hardware is energized.

## Agent workflow

1. Inspect the existing repository, config, tests, and current hardware assumptions before modifying files. Follow established patterns where safe.
2. Implement **one bounded milestone at a time**, in the sequence below, with working code, tests, run commands, and a brief verification report.
3. Prefer small, well-typed Python modules and stdlib capabilities; add dependencies only when justified. Never silently substitute hardware or model APIs.
4. Use simulation first. A feature is not complete until it has failure-path tests and does not require hardware to run its pure business logic.
5. Run relevant checks after edits. Report exact commands, pass/fail status, unresolved risks, and any tests that could not run on current hardware.
6. Do not bypass a failed safety check, disable TLS validation, lower privacy protections, or claim hardware has been verified without actual measurements.
7. Never run unsafe valve actuations by default. All hardware actuation tests require an explicitly enabled bench setup, a physical emergency stop/enable, and an empty/safe collection path.
8. Avoid huge code dumps of unrelated milestones. Make each change independently reviewable and maintain the documented contracts.

## Recommended repository layout

```text
pi/
├── AGENTS.md
├── pyproject.toml
├── requirements.txt
├── requirements-vision.txt
├── config/
│   ├── default.yaml
│   ├── hardware.yaml
│   └── simulation.yaml
├── purifier/
│   ├── __init__.py
│   ├── __main__.py
│   ├── bootstrap.py
│   ├── core/
│   │   ├── runtime.py
│   │   ├── config.py
│   │   ├── state.py
│   │   ├── session_manager.py
│   │   ├── event_builder.py
│   │   ├── event_bus.py
│   │   └── errors.py
│   ├── hardware/
│   │   ├── interfaces.py
│   │   ├── hardware_factory.py
│   │   ├── gpio_flow.py
│   │   ├── ads1115.py
│   │   ├── tds.py
│   │   ├── ph.py
│   │   ├── ds18b20.py
│   │   ├── float_sensor.py
│   │   ├── proximity.py
│   │   └── valve.py
│   ├── vision/
│   │   ├── worker.py
│   │   ├── ipc_server.py
│   │   ├── camera.py
│   │   ├── face_detector.py
│   │   ├── image_quality.py
│   │   ├── embedding.py
│   │   ├── matcher.py
│   │   ├── enrollment.py
│   │   └── embedding_store.py
│   ├── storage/
│   │   ├── sqlite.py
│   │   ├── migrations.py
│   │   ├── repositories.py
│   │   ├── outbox.py
│   │   └── retention.py
│   ├── communication/
│   │   ├── mqtt.py
│   │   ├── schemas.py
│   │   ├── sync.py
│   │   ├── command_handler.py
│   │   └── acknowledgements.py
│   ├── safety/
│   │   ├── interlocks.py
│   │   ├── valve_controller.py
│   │   ├── watchdog.py
│   │   └── health.py
│   ├── simulation/
│   │   ├── simulated_flow.py
│   │   ├── simulated_sensors.py
│   │   ├── simulated_vision.py
│   │   ├── simulated_valve.py
│   │   └── scenarios.py
│   └── utils/
│       ├── logging.py
│       ├── time.py
│       └── metrics.py
├── scripts/
│   ├── check_hardware.py
│   ├── calibrate_flow.py
│   ├── calibrate_tds.py
│   ├── benchmark_vision.py
│   ├── simulate_dispense.py
│   └── database_status.py
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── simulation/
│   └── hardware/
├── deploy/
│   ├── purifier-core.service
│   └── purifier-vision.service
└── docs/
    ├── architecture.md
    ├── wiring.md
    ├── calibration.md
    ├── security.md
    ├── operations.md
    └── test-results.md
```

The parent monorepo may additionally contain `backend/`, `app/`, `shared/`, and `docs/`; this file governs the Pi implementation. Keep shared MQTT schemas synchronized with `shared/` if that directory exists.

## Process boundaries and resource budgets

**Process A — Core controller:** flow, sessions, sensor polling, SQLite writer, MQTT, command handling, interlocks, health. Use `asyncio` for coordination and network tasks; GPIO callbacks must do constant-time, nonblocking work. A single dedicated SQLite writer thread serializes writes.

**Process B — Vision worker:** proximity-eligible camera capture, detection, frame quality scoring, one embedding inference, identity matching, and enrollment. Exchange compact typed requests/results over a Unix domain socket; **never send images over IPC**. Core must continue if vision is absent. Keep model initialized once per worker; camera preview should not run continuously.

Start with these **measurement targets, not guarantees**:

| Budget | Initial goal |
|---|---|
| Core RSS | < 90 MB |
| Vision RSS | < 300 MB; investigate above 350 MB |
| Steady-state combined app RSS | < 400 MB |
| Idle CPU | < 10–15% total, measured |
| Vision jobs | 1 active, at most 1 pending |
| Burst capture | 3 frames at 640×480 initially |
| Inference threads | 1 initially; benchmark alternatives |
| Recognition deadline | 5 s target; fail to Guest on timeout |
| Sensor polling | Approximately 1–2 Hz for TDS |
| MQTT telemetry | Every 10–15 s, configurable |
| Outbox batch | Up to 20 events; bounded in-flight requests |
| In-memory queues | Bounded; never silently drop critical events |

Use systemd memory controls **only after measuring real RSS**, leaving OS/kernel and native library headroom. Run representative long-duration tests and monitor `/sys/class/thermal/thermal_zone0/temp`, throttling, free disk, process RSS, and SQLite health.

## Implementation milestones (no calendar deadline)

### M0 — Hardware contract and development environment

- Document physical pin numbers, **BCM numbering**, electrical levels, sensor board variants, resistor dividers, power rails, ground connections, and camera orientation in `docs/wiring.md`.
- Confirm supported Raspberry Pi OS release, Python version, architecture, Picamera2/libcamera availability, GPIO backend, and whether a compatible ARM64 vision runtime wheel exists.
- Set up the repo, virtual environment, pinned dependencies, formatting/linting, test runner, and two configuration profiles.
- **Do not enable the drain output yet.** On startup, outputs must be configured to fail safe.

Proposed configurable BCM pins: flow 17, DS18B20 4, float 27, PIR 22, drain driver 23, illumination 24, shutter-state 25; I²C SDA 2 and SCL 3. ADS1115 channels A0=TDS in, A1=TDS out, A2=pH. **Verify actual circuitry before connecting.** An ADS1115 powered at 3.3 V must not receive analog inputs above its supply voltage; add appropriate dividers/conditioning. Flow outputs and PIR outputs must be confirmed 3.3 V safe. Float input should fail to unsafe on open circuit wherever feasible. Pi never directly drives the valve coil.

### M1 — Bootstrap, config, logging, and lifecycle

- Implement `python -m purifier --config config/simulation.yaml`.
- Validate config on startup; reject unknown critical values and conflicting pins.
- Provide structured logs with timestamp, level, module, and optional event/command ID. Exclude credentials, embeddings, and images.
- Initialize SQLite and hardware adapters independently; start the controller loop and health supervision.
- Handle SIGTERM/SIGINT: immediately de-energize valve, stop intake safely, flush bounded critical writes, close sockets and GPIO, and exit.
- A process crash or power failure may skip cleanup, therefore physical fail-safe behavior is required.

**Done when:** simulation starts/stops cleanly, misconfiguration is explicit, and a missing optional sensor does not kill the controller.

### M2 — Hardware interfaces and deterministic simulation

- Define `Protocol` interfaces for flow, ADC, temperature, tank level, proximity, camera/recognition, and valve; provide real and simulated implementations behind `hardware_factory.py`.
- Keep all domain logic (`core/`) free from Pi GPIO/camera imports.
- Simulate pulse trains, short-pour merging, face match/Guest, stale sensor readings, network loss, invalid commands, and stuck/failed devices.
- Use deterministic clocks or injected time sources in unit tests; use monotonic time for elapsed intervals and UTC wall-clock timestamps for records.
- Simulation must **never energize actual GPIO outputs**.

**Done when:** the core is testable on a non-Pi machine.

### M3 — Flow driver and dispense sessions

- Count YF-S201 rising edges with a supported GPIO edge callback; callback only increments a counter/captures monotonic timing and notifies the event loop without blocking.
- Start with `450 pulses/litre` only as a configurable placeholder. Calibrate using 100/250/500 ml pours at different flow rates.
- Implement states `IDLE -> ACTIVE -> WAITING -> FINALIZING`; merge flow bursts separated by less than a configurable **3-second no-flow timeout**.
- Each dispense gets a UUID, start/end timestamps, calibrated volume, identity/Guest, latest valid sensor snapshot, and quality flags.
- Avoid double counting at state transitions and races between callbacks and finalization. Set a configurable maximum session duration and handle long/partial sessions explicitly.
- Trigger an *optional* vision identification request once per session; never await vision in the flow callback or block persistence on the camera.
- Checkpoint active sessions where feasible and mark interrupted sessions `incomplete` on reboot. **Do not claim flow is measured while the Pi is powered off.**

**Done when:** calibration meets the chosen acceptance threshold and back-to-back short bursts merge correctly.

### M4 — SQLite and durable outbox

- Use stdlib `sqlite3`, a single bounded writer queue/thread, schema migrations, foreign keys, WAL, `busy_timeout`, and measured durability settings. Start with `synchronous=FULL` for critical records, adjusting only with documented evidence.
- Tables: `dispense_sessions`, `outbox`, `command_log`, `sensor_samples`, `enrollments` (metadata only), `system_events`.
- Persist a dispense and its corresponding outbox row in the **same transaction**.
- Persist UUIDs and sync state; retry events after restart until the **backend confirms database commit**, not merely until MQTT publish returns success.
- Use backend uniqueness on `(device_id, event_id)` to make retries idempotent. Record revision numbers for late identity resolution and Guest reassignment.
- Implement bounded sensor retention, WAL checkpointing, storage health checks, and disk-full behavior. Do not silently drop critical events if the write queue fills; surface a critical fault and preserve any available recovery path.

**Done when:** 10 offline sessions survive reboot and synchronize once each after reconnect.

### M5 — Sensors and quality states

- Implement ADS1115 access through one serialized ADC manager: TDS inlet A0, TDS outlet A1, approximate pH A2. Use board-specific calibration and protect ADC inputs electrically.
- DS18B20: external three-wire power at 3.3 V and approximately 4.7 kΩ data pull-up, subject to actual module requirements.
- Tank float input supports LOW/OK/UNKNOWN and unsafe-on-fault; do not fabricate continuous tank percentage from one binary sensor.
- Proximity input gates camera eligibility; privacy shutter input additionally disables capture when closed. Proximity must **not** override the dispense-trigger privacy rule.
- Return readings with value, unit, timestamp, quality (`valid`, `stale`, `uncalibrated`, `out_of_range`, `sensor_error`). Never reuse stale values as if live.
- Initial polling: TDS every ~2 s, temperature ~5 s, approximate pH ~10 s; debounce and verify float state as needed.
- Sensor initialization/read failures are isolated, logged and retried with bounded backoff.

**Done when:** unplugging any noncritical sensor does not kill flow/session storage; unsafe level faults block draining.

### M6 — Separate on-device vision worker

- Build camera capture using Pi-supported Picamera2/libcamera APIs; capture three frames at 640×480 initially, score face quality (sharpness/size/centering), detect and align the best face, and run one embedding inference.
- Benchmark lightweight MobileFaceNet/InsightFace-compatible models with a CPU ARM64 ONNX or TFLite runtime. Do **not** assume packages, model inputs, or pretrained licenses; verify compatibility before choosing one.
- Keep only the model and required scratch buffers in worker memory. No persistent raw images, uploads, logs, or IPC transport of frames.
- Enroll from the actual purifier camera; collect several embeddings per person, store encrypted at rest, support re-enrollment and deletion.
- Normalize embeddings; use cosine similarity, a calibrated threshold **and** top-1/top-2 margin. Ambiguous matches, unavailable camera, closed shutter, and timeouts all result in Guest.
- Treat simplistic blink/motion cues as **weak spoof deterrents**, not dependable liveness guarantees.
- Worker handles one inference at a time, at most one pending job; send a compact `{request_id, session_id, status, user_id, similarity}` response over the Unix socket. The core uses deadline handling and ignores late/stale responses appropriately.
- Watch worker memory growth; restart it independently if hung. No valve access or unnecessary credentials in the vision worker.

**Done when:** model benchmarks and real identification tests are recorded, and killing vision doesn't interrupt a dispense.

### M7 — MQTT over TLS, synchronization and backend contract

- One long-lived outbound Paho MQTT client with per-device credentials, verified broker certificate, reconnect backoff+jitter, bounded in-flight publishes and subscriptions.
- Topics:
  - `purifier/{device_id}/events` — QoS 1, Pi -> backend
  - `purifier/{device_id}/events_ack` — QoS 1, backend -> Pi, explicitly means persisted
  - `purifier/{device_id}/telemetry` — QoS 0, Pi -> backend
  - `purifier/{device_id}/cmd` — QoS 1, backend -> Pi, **never retained**
  - `purifier/{device_id}/ack` — QoS 1, Pi -> backend
  - `purifier/{device_id}/status` — QoS 1, LWT/availability
- Version JSON messages. Validate device ID, types, timestamps, and required fields; cap message size. Use UTC for wire timestamps.
- Never treat MQTT QoS 1 as exactly once. Track outbox state and backend persistence acknowledgements. Revisions to event identity must use explicit versioned updates.
- Continue offline when broker is down. No synchronous network operations inside sensor callbacks or safety paths.

**Done when:** repeated disconnects, duplicate sends and dropped ACKs produce no duplicate backend events and no lost persisted records.

### M8 — Drain command and valve safety

- Only one remote command is in scope: `drain`. The backend must enforce admin role and confirmation; Pi independently enforces replay prevention, short command expiry, strict payload schema, and local interlocks.
- Record command lifecycle: `received`, `accepted`/`rejected`, `running`, `completed`/`failed`, including reason codes and timestamps.
- Preconditions: not dispensing, quiet period elapsed, valid fresh tank status, not empty, physical enable active, driver healthy, no conflicting operation, valid unexpired command ID.
- During drain: recheck safety conditions, enforce maximum valve-on time, de-energize on any relevant fault, and publish final ACK. Commands must be idempotent across MQTT retries/reboot.
- Design a **hardware-independent automatic cutoff** or safety relay/watchdog; a software timeout alone is insufficient for a hung process. Use a normally-closed valve and an accessible manual stop.
- If there is no valve-position or drain-flow feedback, return `commanded_open/commanded_closed`; do **not** claim that water physically drained successfully.
- All actuation is **disabled by default** in config and tests; require a deliberate bench enable procedure.

**Done when:** drain during a pour or unsafe level is rejected and logged; loss of control signal results in a closed valve; command latency is measured separately from network latency.

### M9 — Alerts and device health

- Produce an alert when outlet TDS crosses a calibrated, configurable threshold, with hysteresis/debounce so it does not spam.
- Maintain a basic filter-usage counter from measured litres, with an alert at 90% of a documented capacity; do not infer true filter condition from TDS difference alone.
- The backend handles mobile push through FCM; the Pi reports alert events over MQTT and queues them offline as needed.
- Collect periodic RSS, CPU temperature, throttling, disk free space, event queue depth, pending outbox size, stale sensors, SQLite integrity, vision responsiveness, and MQTT connection state.
- Limit log volume, retain recent diagnostics, and make operational status available without high-frequency sampling.

**Done when:** alerts are deduplicated and offline-safe, and health reports identify resource leaks or sensor failures.

### M10 — Service deployment, hardening and long-duration tests

- Deploy two `systemd` services (`purifier-core`, `purifier-vision`) with restart policy, limited privileges, explicit environment/config paths, and safe device access.
- Core starts without a network dependency; vision restarts independently. Use `Restart=on-failure`, appropriate restart limits, and evaluate watchdog support.
- Measure and tune resource budgets using real Pi 3 B+ hardware; avoid over-tight `MemoryMax` values that create restart loops. Ensure the OS retains enough headroom for camera, kernel buffers and filesystem caching.
- Test camera failures, malformed config, Wi-Fi loss, broker outage, repeated ACKs, slow SD card, full disk, power interruption, sensor unplugging, and valve safety transitions.
- Run an extended soak test (at least 12 h, preferably longer); track memory growth, CPU temperature/throttling, queue sizes, record counts, process restarts, and database integrity.

**Done when:** core remains responsive under simultaneous flow counting, sensors, vision and MQTT, and safety conditions hold under tested failures.

## Interface contracts and data rules

### Dispense event (initial schema)

```json
{
  "schema_version": 1,
  "event_id": "uuid",
  "revision": 1,
  "device_id": "purifier-001",
  "user_id": "guest",
  "start_ts": "2026-11-01T10:00:00Z",
  "end_ts": "2026-11-01T10:00:08Z",
  "volume_ml": 250,
  "temperature_c": 26.5,
  "tds_in": 340.0,
  "tds_out": 82.0,
  "ph_approx": null,
  "confidence": null,
  "recognition_status": "unknown",
  "volume_quality": "measured"
}
```

Values whose sensors are unavailable must be `null` with corresponding quality metadata rather than made-up numbers. Document exact units (TDS in ppm, temperature in °C, pH unitless) and calibration settings. Keep timestamps timezone-aware and IDs stable across retries.

### Command envelope (initial schema)

```json
{
  "schema_version": 1,
  "command_id": "uuid",
  "device_id": "purifier-001",
  "type": "drain",
  "issued_at": "2026-11-01T10:01:00Z",
  "expires_at": "2026-11-01T10:01:15Z",
  "duration_s": 5
}
```

The command is trusted only if delivered via an authenticated, authorized backend path; the Pi also validates schema, expiry, replay, physical enable, and interlocks. Don't encode admin authorization as an unauthenticated boolean in the payload.

## Performance and engineering conventions

- Target Python 3 features supported by the installed Raspberry Pi OS; avoid language features unsupported by its interpreter. Pin dependencies once verified.
- Use `dataclasses`, `Enum`, `Protocol`, and explicit return types for core domain messages. Keep functions small and name failure states.
- Do not import NumPy, OpenCV, Picamera2 or inference runtimes in the core process. Keep heavy imports inside `vision/`.
- No unbounded `queue.Queue`, `asyncio.Queue`, image buffer collection, retry loop, or log growth.
- GPIO callbacks: short, thread-safe, no disk/network/vision work. Avoid busy waits and arbitrary `sleep()` in the main event loop.
- Distinguish monotonic deadlines from wall-clock timestamps. Handle clock corrections and reboot explicitly.
- Initialize outputs in fail-safe states, use physical pull-ups/pull-downs, and document active-low versus active-high driver behavior.
- Return explicit operation results; do not catch all exceptions and silently continue. Catch expected failures at subsystem boundaries and log actionable context.
- No insecure TLS modes, hardcoded passwords/tokens, or credentials in repository. Use environment files or root-readable secrets outside version control.
- Make configuration values such as GPIO pins, thresholds, timing, calibration, model path, and broker endpoint overrideable without editing source code.
- New production behavior requires unit tests and, for hardware boundaries, at least one simulation failure test.

## Acceptance criteria

| Area | Acceptance test |
|---|---|
| Flow | 20 measured pours each at 100, 250 and 500 ml; report mean/max error and proportion within ±5% |
| Sessions | Merge bursts separated by less than 3 s, split after timeout, preserve one UUID per session |
| Face recognition | At least 50 dispenses across 5–10 enrolled people and varied lighting; target ≥90% correct identification, report Guest and false-match rates separately |
| Vision isolation | Kill or hang worker during flow; controller records volume and returns Guest |
| Offline persistence | 10 dispenses with Wi-Fi down; on recovery backend contains each exactly once |
| Database | Duplicate UUID and lost ACK tests pass; SQLite integrity check succeeds after restart |
| TDS | Compare both readings with calibrated reference meter; target ±10%, document calibration method |
| pH | Clearly labelled approximate, calibration/fault states respected |
| Drain | 20 authorized trials; target ≤5 s app-to-open under test network conditions; no unintended opens |
| Safety | Drain rejected during dispensing, empty/unknown level, expired/replayed command, or physical disable |
| Reliability | At least 12 h soak test; zero unintended valve activation, zero missing *committed* events, no unrecovered controller failure |

Distinguish **measured and verified** results from engineering targets. The Pi cannot measure water while completely unpowered; document this honestly.

## Definition of done for every change

A milestone is complete only when it has: working implementation; updated configuration and docs; simulation path; tests for normal and failure behavior; exact run/test commands; clear logs; no regression in core flow/persistence/safety; and a short report of actual results, limitations, and next logical milestone.

## First action for a new coding agent

Start at **M0/M1** unless the repository already contains tested work. Inspect the current environment, create the package skeleton and configuration validator, implement structured logging plus clean startup/shutdown, then add deterministic simulation drivers. Run `python -m purifier --config config/simulation.yaml` and the initial test suite before implementing physical GPIO. Do not start by integrating the face model or energizing the valve.

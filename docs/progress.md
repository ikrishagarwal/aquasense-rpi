# Implementation ledger

Work progressed in dependency order from M0 to the simulation and software portions
of M10. The integrated controller and the component/failure tests are implemented;
physical milestones remain open. See TODO.md for the precise checked/blocked list.

| Layer | Implemented and exercised on host |
|---|---|
| M0/M1 | Package/profiles, strict config, safe logs, bounded SQLite lifecycle, signal/partial-startup cleanup |
| M2/M3 | Deterministic adapters, clocks, pulse buffering, session boundaries, checkpoint race test, optional vision isolation |
| M4 | Transactional migration, atomic outbox, restart recovery, revision ACKs, rollback/queue faults, retention/WAL |
| M5 | Sensor conversion ports, CRC/range/freshness errors, tank debounce, independent retry policy |
| M6 | Bounded IPC protocol, Guest fallback, ephemeral capture/model ports, matching and encrypted enrollment/delete |
| M7 | Optional verified-TLS transport, bounded queues, commit-based synchronization, strict command rejection and backend handoff |
| M8 | Simulation-only durable drain lifecycle, replay protection, interlocks and automatic close supervision |
| M9 | Durable alert transitions, measured-volume filter counter, periodic health/telemetry |
| M10 | Separate restricted simulation service templates, operator docs, calibration report and soak harness |

Current regression result: **52 tests passed** through stdlib unittest. Development
format/lint/type tools and live IPC/broker tests remain unavailable in this sandbox.
Exact commands, results and limitations are in test-results.md.

The latest reviewed fixes include atomic DDL migration, batch-level checkpoints,
finalization at the pulse snapshot cutoff, bounded shutdown flushing, replay-safe
command history/outbox recovery, automatic simulated drain supervision and strict
configuration error redaction. No physical adapter has been enabled or energized.

The supplied backend was read but not modified. BACKEND.md maps its existing files,
topics and database behavior to the required contract, with fixtures in contracts/.
Real Pi/board/model details and backend deployment access are still needed to finish
hardware-dependent implementation and acceptance; simulation cannot replace them.

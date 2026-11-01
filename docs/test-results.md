# Verification report — 2026-11-01

Current result: **52 host tests passed** (31 unit, 16 integration, 5 simulation).
The integrated CLI starts/stops and commits a simulated pour. This report supersedes
the initial M0-only report. Hardware milestones are not marked complete.

Host: Arch Linux x86_64, Python 3.14.8, SQLite 3.53.4. Optional host libraries used
by tests: PyYAML 6.0.3 and cryptography 50.0.2. No real GPIO, camera, valve, broker
or upgraded backend was accessed. Physical outputs remain unavailable.

| Exact command | Actual result |
|---|---|
| `python -m unittest discover -s tests/unit -q` | PASS: 31 tests |
| `python -m unittest discover -s tests/integration -q` | PASS: 16 tests |
| `python -m unittest discover -s tests/simulation -q` | PASS: 5 tests |
| `python -m unittest discover -s tests/unit -p test_lifecycle.py -q` | PASS: 7 tests after final config error-redaction change |
| `python -m compileall -q purifier scripts tests` | PASS |
| `python -m purifier --config config/simulation.yaml --demo --run-for 4` | PASS: clean startup/shutdown; simulated flow persisted |
| `python -m scripts.simulate_dispense --database /tmp/aquasense-offline-demo.sqlite3 --sessions 10` | PASS: ten simulated sessions committed to a fresh database |
| `python -m scripts.database_status /tmp/aquasense-offline-demo.sqlite3` | PASS: integrity ok, 10 sessions, 10 pending outbox, 0 active checkpoints |
| `python -m scripts.soak --seconds 2 --database /tmp/aquasense-soak.sqlite3` | PASS: harness smoke only; 2 sessions, integrity ok, 0 simulated valve opens |
| `python -m build --no-isolation` | PASS: source archive and wheel built using host setuptools 84.0.0 |
| `.venv/bin/python -m pip install --no-index --no-deps --force-reinstall dist/aquasense_pi-0.0.1-py3-none-any.whl` | PASS: wheel installation |
| `.venv/bin/python -m pip install --retries 0 --timeout 5 -r requirements-dev.txt` | BLOCKED: packages unavailable from the restricted environment; earlier attempts reported PyPI DNS failure |
| `python -m pytest tests/unit tests/simulation` | BLOCKED: pytest module unavailable |
| `python -m pytest tests/integration` | BLOCKED: pytest module unavailable |
| `python -m ruff check .` | BLOCKED: Ruff module unavailable; no lint pass claimed |
| `python -m ruff format --check .` | BLOCKED: Ruff module unavailable; no format pass claimed |
| `python -m mypy purifier` | BLOCKED: mypy module unavailable; no type-check pass claimed |
| `systemd-analyze verify deploy/purifier-core.service deploy/purifier-vision.service` | BLOCKED: sandbox rejected required socket options; units not installed/verified on a Pi |

A separate fresh temporary profile was also run through the integrated CLI for
4 seconds. Its read-only database status reported integrity ok, **1 session,
1 pending outbox event and 0 active checkpoints**. SIGTERM and SIGINT process tests
both returned exit 0. An invalid database path returned a clean failure without a
traceback. These process tests are part of the integration count above.

## Covered behavior

- Config unknown/duplicate keys, types, nonfinite values, conflicting pins, unsafe
  TLS/drain options, safe YAML handling and privacy-safe logs.
- Bounded writer saturation, visible persistence failure, rollback of session plus
  outbox/counter changes, ten offline sessions surviving writer restart, exact
  revision ACKs and incomplete checkpoint recovery.
- Session merge/split boundary, maximum duration, incomplete shutdown, concurrent
  callback totals and explicit timing overflow. A pulse arriving during a checkpoint
  remains in the same session at the merge boundary.
- Sensor unplug/fault, stale value, ADC range, DS18B20 CRC, tank debounce and retry
  backoff. Hung/missing vision and stale sensors do not block session commit.
- Metadata-only IPC parsing, stale response rejection, capture privacy gate, one
  inference from three synthetic frames, matcher margin, encrypted enrollment
  replacement/deletion/tamper detection and shutter closure during enrollment.
- MQTT TLS context/queue limits, overflow disconnect, in-memory backend reconnect,
  lost/duplicate commit ACKs, strict expiring commands and durable runtime rejection.
- Simulated drain interlock matrix, replay prevention, automatic physical-disable
  supervision, interrupted-drain restart rejection and no opening when acceptance cannot commit. Physical cutoff behavior
  has not been measured.
- Durable alert debounce, hysteresis and restart deduplication; filter usage is not
  double-counted by dispense retries or identity revisions.

## Limits and release gates

The two-second smoke soak is **not** the required >=12-hour representative Pi test.
Its reported RSS/thermal values are observations of the development host, not Pi
performance. No calibration accuracy, real face-identification rate, hardware
latency, electrical safety or physical-drain result is claimed.

Socket restrictions prevent live Unix IPC and broker validation here. The in-memory
backend test does not certify the adjacent backend, whose required changes are
listed in BACKEND.md. Ruff/mypy may reveal additional issues once available; do not
interpret their absence as a pass. TODO.md records every remaining gate, including
real adapters, model selection, backend deployment, dependency checks and bench tests.

The freshly installed wheel was additionally started from `/tmp` (outside the
source checkout) using `.venv/bin/python -m purifier` with an absolute simulation
profile and `--run-for 0.2`; startup and shutdown both succeeded.

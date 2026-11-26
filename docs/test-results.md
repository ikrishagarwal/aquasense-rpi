# Verification report — 2026-11-01

## Arduino recognition deadline trial — 2026-11-26

| Command | Host result |
|---|---|
| `PYTHONPATH=/tmp/aquasense-check-deps python -m pytest -q` | 82 tests and 54 subtests passed |
| `/tmp/aquasense-check-deps/bin/ruff check purifier/arduino_monitor.py purifier/vision/arduino_worker.py purifier/vision/arduino_capture.py purifier/vision/pipeline.py tests/unit/test_arduino_vision.py` | Passed |
| `/tmp/aquasense-check-deps/bin/ruff format --check purifier/arduino_monitor.py purifier/vision/arduino_worker.py purifier/vision/arduino_capture.py purifier/vision/pipeline.py tests/unit/test_arduino_vision.py` | Passed |
| `python -m purifier.arduino_monitor --help` | Passed |
| `git diff --check` | Passed |

With a ten-second budget, the simulated worker picked up a job after five
seconds and finished in time for a matched pour summary. A spawned worker that
hung was killed at ten seconds on the simulated monitor clock. The monitor
reported Guest with the pour's volume and TDS, then recognized the next pour
with a fresh worker. Configuration tests verified the 5.0-second default and
rejection of nonnumeric, non-finite, and out-of-range settings.

The Pi at `raspberrypi.local` responded to SSH and rejected authentication.
The ten-second bench setting, cold and warm timings, and live match for Krish
remain to be verified using the [bench procedure](operations.md#enable-automatic-face-recognition).

## Live-pour vision diagnostics

| Command | Host result |
|---|---|
| `PYTHONPATH=/tmp/aquasense-check-deps python -m pytest -q` | 79 tests and 42 subtests passed |
| `/tmp/aquasense-check-deps/bin/ruff check purifier/arduino_monitor.py purifier/vision/arduino_worker.py purifier/vision/arduino_capture.py purifier/vision/pipeline.py tests/unit/test_arduino_vision.py` | Passed |
| `git diff --check` | Passed |

Simulations distinguished a matched pour, a burst with no usable face, a
deadline expiring during scoring, and a hung spawned worker killed at the
deadline. Volume and TDS still appeared in the pour summaries. The first and
second live-pour timing comparison is pending on the Pi with `/dev/ttyUSB0`.

## Arduino enrollment diagnostics

| Command | Host result |
|---|---|
| `PYTHONPATH=/tmp/aquasense-check-deps python -m unittest discover -s tests/unit -p 'test_arduino*.py' -q` | 18 passed |
| `PYTHONPATH=/tmp/aquasense-check-deps python -m pytest -q` | 78 passed, 40 subtests passed |
| `/tmp/aquasense-check-deps/bin/ruff check purifier/vision/arduino_worker.py purifier/vision/enrollment.py tests/unit/test_arduino_vision.py` | Passed |
| `git diff --check` | Passed |

Simulated enrollment reports an invalid key before opening the camera and
identifies the sample number when all three frames are unusable. The latter
case closes the camera without saving an enrollment. The error seen on the Pi
requires rerunning enrollment there to identify its cause.

## Arduino flow-only capture and enrollment

| Command | Host result |
|---|---|
| `PYTHONPATH=/tmp/aquasense-check-deps python -m unittest discover -s tests/unit -p 'test_arduino*.py' -v` | 17 passed |
| `PYTHONPATH=/tmp/aquasense-check-deps python -m pytest -q` | 77 passed, 40 subtests passed |
| `/tmp/aquasense-check-deps/bin/ruff check purifier/arduino_monitor.py purifier/vision/arduino_capture.py purifier/vision/arduino_worker.py tests/unit/test_arduino_vision.py` | Passed |
| `python -m compileall -q purifier tests` | Passed |
| `git diff --check` | Passed |

With a simulated camera and GPIO imports disabled, positive flow started one
burst with frames at 1.0, 1.4, and 1.8 seconds. A second interval inside the
merge window shared that burst and the matched summary. The enrollment command
saved an encrypted enrollment with both GPIO inputs unset. A configured
unverified or closed shutter blocked camera opening. A shutter closing during
capture returned Guest, and an inactive verified proximity sensor blocked
automatic capture. The worker timeout test continued to report volume and TDS
as Guest.

Pi verification is pending. The next check uses the Arduino and camera with
both GPIO fields set to `null` to measure capture timing and test recognition
and enrollment on the actual hardware.

## Arduino dispense recognition

| Command | Host result |
|---|---|
| `python -m unittest discover -s tests/unit -p 'test_arduino*.py' -v` | 14 passed |
| `PYTHONPATH=/tmp/aquasense-check-deps python -m pytest -q` | 74 passed, 33 subtests passed |
| `/tmp/aquasense-check-deps/bin/ruff check purifier/arduino_monitor.py purifier/vision/arduino_capture.py purifier/vision/arduino_worker.py tests/unit/test_arduino_monitor.py tests/unit/test_arduino_vision.py` | Passed |
| `python -m compileall -q purifier tests` | Passed |
| `git diff --check` | Passed |

The controlled-clock simulation captured three frames at 1.0, 1.4, and 1.8
seconds from initial flow detection and issued one burst for merged flow. It
covered shutter closure, ambiguous identification, missing TDS, disconnects,
stale results, and recovery from a hung subprocess. A CLI run against a Linux
pseudo-terminal with the example privacy settings emitted a single Guest summary
for 248.9 mL and 82.0 ppm, then exited cleanly on SIGTERM.

Pi hardware was unavailable. The real camera delay and frame spacing,
disconnected-input behavior, recognition accuracy, and Arduino volume and TDS
readings still need bench measurements.

## Arduino dispense monitor

| Command | Host result |
|---|---|
| `python -m unittest discover -s tests/unit -p test_arduino_monitor.py -v` | 8 passed, including Linux pseudo-terminal framing, timeout, and disconnect tests |
| `python -m unittest discover -s tests/unit -q` | 39 passed, 1 import error in the existing still-photo tests because NumPy is unavailable |
| `python -m unittest discover -s tests/simulation -q` | 5 passed |
| `python -m unittest discover -s tests/integration -q` | 16 passed |
| `python -m purifier.arduino_monitor --help` | Passed |

The CLI was also launched against a pseudo-terminal via a device symlink. It
reported an incomplete 4.4 mL pour on disconnect, reopened the replacement port,
and reported a 2.2 mL pour after three seconds of zero-flow reports. Ctrl+C
returned exit status 0.

No Arduino device was exposed to this workspace. The reported volumes use the
sketch's 2.222 mL/pulse calibration. A measuring-cup comparison on the Pi is
pending.

## Still-photo recognition update — 2026-11-04

Added shared 640x480 preprocessing, explicit OpenCV-compatible camera output,
verbose frame diagnostics, capture cleanup and bounded argument validation.
Regression tests were added for preprocessing/alignment, mixed/no-face scoring,
camera lifecycle failures and argument errors. Execution was skipped at the user's
request. Windows Python is unavailable in this workspace; WSL access was not
approved. No new runtime or camera validation is claimed.

`git -c core.safecrlf=false diff --check` passed (line-ending notices only).
On the Pi, use the verbose recognition command in operations.md and record actual
detection counts and elapsed time. The user's resized-image enrollment succeeded,
but fresh camera recognition with this update remains unverified.

## Earlier host verification

Earlier result: **52 host tests passed** (31 unit, 16 integration, 5 simulation).
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

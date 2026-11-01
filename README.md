# AquaSense Pi

Offline-first Raspberry Pi 3 B+ edge-controller software with a runnable simulation,
durable SQLite outbox, optional MQTT/TLS transport and simulation-only drain safety.
Hardware actuation is disabled and hardware profiles are rejected pending bench
verification. Actual GPIO, ADC board and camera/model integration are not complete.

From the repository root:

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pip install --no-deps -e .
python -m purifier --config config/simulation.yaml --demo --run-for 4
python -m scripts.simulate_dispense --database var/demo.sqlite3 --sessions 10
python -m scripts.database_status var/demo.sqlite3
python -m pytest tests/unit tests/simulation
python -m pytest tests/integration
python -m ruff check .
python -m ruff format --check .
python -m mypy purifier
```

Without optional development packages, run the same unittest-based tests:

```sh
python -m unittest discover -s tests/unit -v
python -m unittest discover -s tests/simulation -v
python -m unittest discover -s tests/integration -v
```

Crypto tests require `cryptography`; transport uses `paho-mqtt` only when enabled.
See [actual results](docs/test-results.md), [remaining work](TODO.md),
[operations](docs/operations.md), [wiring](docs/wiring.md),
[privacy/security](docs/security.md), and [backend handoff](BACKEND.md).
No hardware, live broker/backend, real face recognition or 12-hour soak pass is
claimed by these host simulations.

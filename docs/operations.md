# Running and operating the current implementation

Hardware mode is deliberately rejected. The integrated core is simulation-only;
MQTT transport can be explicitly enabled for a matching test backend. Vision IPC
is implemented but the core privacy gate remains closed without verified proximity
and shutter inputs; the separate worker is simulation-only and returns Guest.

```sh
python -m purifier --config config/simulation.yaml
python -m purifier --config config/simulation.yaml --demo --run-for 4
python -m scripts.simulate_dispense --database var/demo.sqlite3 --sessions 10
python -m scripts.database_status var/demo.sqlite3
python -m scripts.soak --seconds 43200 --database var/soak.sqlite3
```

SIGTERM/SIGINT first request valve closure and stop pulse intake. Buffered accepted
pulses are drained, the active session is committed as incomplete, optional network
work is stopped and the bounded writer is closed. A crash can skip cleanup;
physical fail-safe circuitry is mandatory before any future hardware adapter.
Uncommitted writes can be lost on power loss. SQLite checkpoints recover only the
last committed pulse count; no measurement occurs while the Pi is unpowered.

A pulse buffer overflow records a critical count fault; it cannot reconstruct the
lost timing of individual pulses. Storage queue/disk faults stop further unsafe
claims and preserve committed records; an uncommitted event may remain only in
memory. Do not delete pending outbox data to make room. Back up a stopped database
or use SQLite's online backup API; copying only a live `.sqlite3` file without WAL
is not a reliable backup. Restore to an isolated test path and run integrity checks
before replacing state. Keep the database and enrollment keys/backups access-limited.

Shipped profiles use JSON-subset YAML; general YAML is supported with optional PyYAML. There is no implicit merging. Unknown
keys, duplicate keys, wrong types, nonfinite measurements, pin conflicts and unsafe
options fail explicitly. Relative database paths resolve from the profile's parent
project directory (`config/../`). Deployment profiles use absolute paths. MQTT
credentials are read from environment names configured in the profile; absent
optional transport/dependencies are logged and local measurement continues.

## Service installation (simulation templates; not installed by this work)

Create a `purifier-ipc` group and separate `purifier` and `purifier-vision` system
users. Place the project and virtual environment under `/opt/aquasense`, owned by
an administrator. Install `deploy/simulation.yaml` as `/etc/purifier/simulation.yaml`
and the two service units under `/etc/systemd/system/`. Supply any core credentials
via a root-readable systemd EnvironmentFile drop-in; never share it with vision.
Reload systemd and explicitly start each service. Inspect journal health before
enabling at boot. Core has no network-online dependency and vision restarts
independently. The vision socket is under its private RuntimeDirectory with trusted
IPC-group access. Local manual worker launches must remove stale sockets only after
confirming the owner process is gone; do not unlink a live worker's socket.

The units use PrivateDevices because they currently run simulation. Do not remove
that restriction or grant real GPIO/camera access until the wiring and adapter
review is complete. No MemoryMax is set before actual Pi RSS/OS headroom measurement.
The units are not a completed hardware deployment. This sandbox could not complete
systemd unit verification because required socket options are denied.

## Validation and remaining external gates

Use tests and exact results in test-results.md. A two-second smoke soak exercises
only the harness; it is not the required representative 12-hour Pi soak. Record
RSS, CPU/temperature/throttling, queue sizes, committed record counts, restarts,
SQLite integrity and unintended valve activation on the actual bench. Hardware
measurements and model trials remain pending. Consult TODO.md for unimplemented
hardware-dependent components and BACKEND.md for the required backend work.

Configure a bounded journald storage quota on the deployment host in addition to
the supplied rate limits; stdout itself does not implement filesystem retention.
Shutdown permits at most 5 s for session flushing, 3.2 s for network cleanup and
5 s for the writer to drain; service TimeoutStopSec is 20 s. A missed deadline
reports failure rather than claiming all critical writes finished.

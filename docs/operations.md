# Running and operating the current implementation

## One-shot local face identification (prototype)

This standalone CLI takes still photos only; it does not use a continuous preview
or connect to dispense recognition. Install Picamera2 from the matching Raspberry
Pi OS repository (`sudo apt install python3-picamera2`), then install NumPy and
OpenCV headless in an environment that can see the OS camera bindings:

```sh
python3 -m venv --system-site-packages .venv-vision
. .venv-vision/bin/activate
python -m pip install 'numpy>=1.24' 'opencv-python-headless>=4.8,<5'
```

Download `face_detection_yunet_2023mar.onnx` and
`face_recognition_sface_2021dec.onnx` from the corresponding model folders in
the OpenCV Zoo GitHub repository and place them in a private local model folder.
Enroll using 5–10 images (omit `--images` to capture them, or supply image paths),
then recognize a five-photo burst:

```sh
python -m purifier.vision.still_identify enroll --user alice --count 5 --store var/face-users.json --yunet models/face_detection_yunet_2023mar.onnx --sface models/face_recognition_sface_2021dec.onnx
python -m purifier.vision.still_identify recognize --store var/face-users.json --yunet models/face_detection_yunet_2023mar.onnx --sface models/face_recognition_sface_2021dec.onnx
```

Recognition averages each user's best enrolled-sample cosine score over frames
with a detected face; default threshold is 0.363. Raw captures are held in memory
and are not intentionally saved. The JSON embedding store contains biometric data:
keep it private and delete it when no longer needed. This prototype has not been
benchmarked or accuracy-validated on a Pi 3 B+.

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

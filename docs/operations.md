# Running and operating the current implementation

## Arduino dispense monitor

Connect the Arduino running the existing flow and TDS sketch to the Pi over USB.
Close any Arduino Serial Monitor or other program using the same port. List the
device paths on the Pi and launch the monitor with the matching path.

```sh
ls -l /dev/serial/by-id/
python -m purifier.arduino_monitor --port /dev/ttyACM0
```

When available, pass the matching `/dev/serial/by-id/…` path to `--port` so the
monitor can reopen the device if its numbered port changes after reconnecting.
If there is no by-ID entry, look for the newly created `/dev/ttyACM*` or
`/dev/ttyUSB*` device. Run from the project root with Python 3.11 or later on
Linux and an account with read access to the serial device. The monitor uses the
standard library at 9600 baud and runs independently of the controller, GPIO,
database, and network.

```text
Flow started
Dispensed 248.9 mL | TDS estimate: 82.0 ppm
```

The first positive interval starts a pour. The monitor sums reported interval
volumes and times the idle period from the first zero-flow report. Flow that
resumes before three seconds is included in the same pour. The total includes
the latest valid TDS estimate received during the pour, or `TDS estimate:
unavailable`. TDS alone cannot establish whether the water is safe to drink.

A malformed flow report, inconsistent cumulative total, Arduino reset, or
connection loss during a pour prints `Incomplete pour` with the accepted volume
so far. Two seconds without a complete serial line also ends an active pour as
incomplete. The monitor takes a fresh cumulative baseline when reports resume
and retries disconnected ports once per second. Press Ctrl+C to stop.

Volume uses the sketch's existing calibration of 2.222 mL per pulse. To check
physical accuracy on the Pi, dispense into a measuring cup, wait for the total,
and record the cup reading, console total, and their difference in the test
results. The test command below exercises report handling and Linux serial
framing with a pseudo-terminal.

```sh
python -m unittest discover -s tests/unit -p test_arduino_monitor.py -v
```

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

Each successful recognition run also prints `TDS: 76 ppm`, where the value is a
random integer from 75 to 77 inclusive. This console demo value is not a sensor
measurement and is never stored or transmitted. It appears for matched users,
`unknown`, and `no face detected`; enrollment and failed runs do not print it.

Recognition averages each user's best enrolled-sample cosine score over frames
with a detected face; the best average must be strictly greater than the default
threshold of 0.6. A score of exactly 0.6 returns `unknown`. Use `--threshold` to
override this cutoff; the comparison remains strict. Raw captures are held in memory
and are not intentionally saved. The JSON embedding store contains biometric data:
keep it private and delete it when no longer needed. This prototype has not been
benchmarked or accuracy-validated on a Pi 3 B+.

The still stream explicitly requests 640x480 with Picamera2 `RGB888` (BGR byte
order in the captured array, as OpenCV expects). Loaded images are also resized
to 640x480 before detection and alignment. This matches the resized images that
successfully enrolled on the user's Pi. Existing enrollment files remain usable.

After updating the project on the Pi, run a fresh five-photo burst with diagnostics:

```sh
cd /home/pi/aquasense-rpi
. .venv-vision/bin/activate
python -m purifier.vision.still_identify recognize --store var/face-users.json --yunet models/face_detection_yunet_2023mar.onnx --sface models/face_recognition_sface_2021dec.onnx --verbose
```

`--verbose` also works for enrollment. It prints each frame's processed dimensions
and detected face count, the total usable frames, and total elapsed seconds to
stderr. The recognition result remains on stdout. It never saves frames or prints
embeddings. A camera/model failure exits with an error; successful inference with
no detected faces returns `no face detected`.

To copy only the updated CLI from the Windows workspace, run this in PowerShell
on the PC (replace the SSH hostname if needed):

```powershell
scp F:/hackathons/aquasense/aquasense-rpi/purifier/vision/still_identify.py pi@raspberrypi:/home/pi/aquasense-rpi/purifier/vision/still_identify.py
```

Capture count is bounded to 1..10 for recognition and 5..10 for enrollment;
recognition defaults to five photos separated by 0.4 seconds after a one-second
camera warm-up. Delays must be finite and nonnegative. Enrollment still requires
a detected face in every supplied image and leaves the previous enrollment intact
on detection failure. Retry with `--verbose` to locate missed frames.

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

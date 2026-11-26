# Running and operating the current implementation

## Arduino dispense monitor

Connect the Arduino running the flow and TDS sketch to the Pi over USB.
Close any Arduino Serial Monitor or other program using the same port. List the
device paths on the Pi and launch the monitor with the matching path.

```sh
ls -l /dev/serial/by-id/
python -m purifier.arduino_monitor --port /dev/ttyACM0
```

Use the matching `/dev/serial/by-id/…` path when available so the monitor can
reopen the device if its numbered port changes after reconnecting. Otherwise,
look for the newly created `/dev/ttyACM*` or `/dev/ttyUSB*` device. Run from the
project root with Python 3.11 or later on Linux and an account with serial read
access. The standard-library serial monitor uses 9600 baud. With the command
above, each pour is reported as Guest and the camera stays off.

```text
User: Guest | TDS estimate: 82.0 ppm | Dispensed: 248.9 mL
```

The first positive interval starts a pour. The monitor sums interval volumes
and times the idle period from the first zero-flow report. Flow that resumes
before three seconds is included in the same pour. The summary uses the latest
valid TDS estimate received during the pour, or displays `TDS estimate: unavailable`
if no valid reading arrived. TDS alone cannot establish whether the water is safe
to drink.

A malformed flow report, inconsistent cumulative total, Arduino reset, or
connection loss during a pour prints `Incomplete pour` with the accepted volume
so far. Two seconds without a complete serial line also ends an active pour as
incomplete. The monitor takes a fresh cumulative baseline when reports resume
and retries disconnected ports once per second. Press Ctrl+C to stop.

Volume uses the sketch's existing calibration of 2.222 mL per pulse. First run
the Guest-only command on the Pi. Dispense into a measuring cup and record the
cup reading, console total, their difference, and the Arduino TDS estimate in
the test results.

```sh
python -m pytest tests/unit/test_arduino_*.py
```

### Send completed dispenses over HTTPS

Create a `.env` file in the Pi repo with the Render HTTPS origin, the provisioned
device UUID, and the matching per-device token. The repo ignores `.env`. Restrict
access to the file with `chmod 600 .env`.

```dotenv
AQUASENSE_BACKEND_URL=https://your-service.onrender.com
AQUASENSE_DEVICE_ID=your-provisioned-device-uuid
AQUASENSE_DEVICE_TOKEN=your-per-device-token
```

Start the monitor from the Pi repo. It reads `.env` from the working directory.

```sh
python -m purifier.arduino_monitor --port /dev/ttyACM0 \
  --vision-config var/arduino-vision.json
```

Use `--env-file /path/to/arduino.env` to load a different file. Shell environment
variables override the corresponding file entries. With all three delivery
settings absent, the monitor runs in console-only mode.

The monitor saves each completed pour in `var/arduino-dispenses.sqlite3` before
sending it. It includes the first and last positive-flow timestamps in UTC,
the accumulated volume in mL, the latest valid TDS estimate in ppm, and the
local enrollment UUID when recognized. Guest and unavailable TDS values are
stored as null. The displayed name stays on the Pi.

Network errors, HTTP 429 and server errors are retried after restart using the
same event UUID. Keep the same working directory and device UUID across
restarts. Permanent rejections remain in the outbox and can be inspected with
SQLite. Persistence failures and permanent rejections are reported on stderr.

```sh
sqlite3 var/arduino-dispenses.sqlite3 \
  'SELECT event_id, attempts, failure_reason, rejected FROM arduino_dispenses;'
```

### Enable automatic face recognition

Install Picamera2 from the matching Raspberry Pi OS packages and use a virtual
environment with access to the system camera bindings. Download the YuNet
2023mar and SFace 2021dec models specified in the example configuration and
review their licenses.
[Picamera2 manual](https://pip-assets.raspberrypi.com/categories/652-raspberry-pi-camera-module-2/documents/RP-008156-DS-7-picamera2-manual.pdf)
[GPIO Zero pin backends](https://gpiozero.readthedocs.io/en/stable/api_pins.html)
[YuNet model](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet)
[SFace model](https://github.com/opencv/opencv_zoo/tree/main/models/face_recognition_sface)

```sh
sudo apt install python3-picamera2
python3 -m venv --system-site-packages .venv-vision
. .venv-vision/bin/activate
python -m pip install -r requirements-vision.txt
mkdir -p var
cp config/arduino-vision.example.json var/arduino-vision.json
(umask 077; set -C; head -c 32 /dev/urandom > var/arduino-vision.key)
```

Paths in the JSON resolve from the directory containing it. Confirm the model
and key paths before use. Keep the key outside the enrollment directory and
retain it if you want to keep access to existing enrollments. Keep the console
private because it displays user labels. Keep disk-backed swap disabled for
capture sessions so raw frames stay out of disk-backed memory.

For the setup with only the Arduino and Pi camera, keep these two fields in
`var/arduino-vision.json` as shown. Passing `--vision-config` enables automatic
capture on flow. Run without that flag for Guest-only operation.

```json
{
  "proximity": null,
  "shutter": null
}
```

If either optional GPIO input is fitted, install `python3-gpiozero` and
`python3-lgpio`. Set each fitted input's `pin_bcm`, `eligible_high`, and
`verified` fields after checking its wiring and fault behavior. The monitor
checks a configured shutter before opening the camera and before and after
each frame. An unverified, closed, or faulty configured shutter leaves the pour
in Guest mode. A verified proximity input adds a gate to automatic capture.
[Wiring and fault checks](wiring.md#arduino-monitor-camera-privacy)

Stop the monitor while enrolling and position your face in view of the camera.
Open the shutter if one is configured. Enrollment can run without flow or any
GPIO inputs. It captures three samples by default and saves encrypted
embeddings. Keep the UUID for re-enrollment or deletion. The optional label
appears on the console in place of the UUID.

```sh
user_id="$(python -c 'import uuid; print(uuid.uuid4())')"
printf '%s\n' "$user_id"
python -m purifier.vision.arduino_worker --config var/arduino-vision.json enroll --user-id "$user_id" --label alice
python -m purifier.arduino_monitor --port /dev/ttyACM0 --vision-config var/arduino-vision.json
# Stop the monitor before deleting the enrollment.
python -m purifier.vision.arduino_worker --config var/arduino-vision.json delete --user-id "$user_id"
```

If enrollment fails while preparing storage, check the configured key path and
permissions. Keep the existing key if you already have enrollments. If model
loading fails, check that both paths point to the downloaded ONNX files. If the
command reports no usable face, face the camera in good light, move closer, and
retry with the same UUID.

The first valid positive-flow report triggers one three-frame burst for the
pour. Flow that resumes within the three-second merge window shares that burst.
The worker schedules the first 640×480 frame one second after that report and
targets 0.4 seconds between frames. Camera startup can shift
the first frame later. It embeds the highest-scoring usable face frame and
requires cosine similarity of at least 0.60 and a lead of at least 0.05 over
the next enrolled person by default. The Arduino vision JSON accepts
`recognition_deadline_s` as a finite number from 5 through 20 seconds and
defaults to 5.0 when omitted. The deadline starts at the first positive-flow
report and includes worker startup, capture, and inference. At expiry the
monitor kills the worker and reports the pour as Guest with its measured
volume and latest valid TDS estimate. Serial measurement continues throughout.
A pour that starts while the only worker is busy reports Guest. Interrupted
pours print the accepted volume immediately and cancel their recognition.

For the Pi bench trial, set `"recognition_deadline_s": 10.0` in the active
`var/arduino-vision.json`. Run the debug monitor and make two pours with Krish
in view of the camera before starting each one. Wait for the first pour
summary before starting the second and keep the same monitor running.

```sh
python -m purifier.arduino_monitor --port /dev/ttyUSB0 \
  --vision-config var/arduino-vision.json --vision-debug
```

The debug lines go to stderr. Use `flow_at` to group lines for each pour.
`worker_ready` shows the elapsed time from flow detection to worker pickup.
Compare the first worker job (`cold=True`) with the second (`cold=False` if the
worker survived). For each pour record worker pickup and the elapsed times for
capture, scoring, embedding, and the outcome. A successful recognition prints
`outcome=matched` on stderr and `User: Krish` on stdout. A phase with only a
start line was interrupted before it reported completion. If capture still
misses the ten-second cutoff, investigate camera capture before raising it again.
`deadline_expired` means the recognition budget elapsed, `no_quality_face`
means the burst produced no usable frames, and `no_match` or `ambiguous` means
matching ran but did not identify a person. Volume and TDS remain on stdout.

On the bench, test an enrolled pour and a Guest pour. If a shutter is fitted,
close it during a burst and confirm Guest. Disconnect any fitted GPIO gate and
verify that the camera stays off. Measure the time from first positive report
to first frame and the inter-frame spacing. Compare several console volumes
with measuring-cup readings and record results alongside the camera, model, and
any GPIO package versions. Physical capture timing, recognition accuracy, and
water measurements are pending Pi verification.

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

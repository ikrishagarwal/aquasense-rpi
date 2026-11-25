# AquaSense: Raspberry Pi Water Purifier Controller

*Project overview | November 2026*

## Purpose and Hardware

AquaSense is an edge computing prototype for a camera-assisted water purifier.
It combines local user identification with software for dispense measurement,
water-quality monitoring, durable event storage, and remote synchronization.
The intended outcome is a purifier that can associate water usage with enrolled
users while continuing local measurement when internet access is unavailable.
Network services, the mobile application, and the backend are kept off the Pi.

The controller targets a Raspberry Pi 3 Model B+ with 1 GB RAM, running Raspberry
Pi OS Lite. Its camera is the Raspberry Pi Camera Module V1, based on the
OmniVision OV5647 sensor. Python coordinates the application; Picamera2 provides
camera access, and OpenCV with NumPy performs face processing on the device.
The proposed sensing hardware includes a flow sensor, inlet and outlet TDS
sensors through an ADS1115 ADC, a temperature probe, an approximate pH sensor,
and a tank float switch. Physical sensor integration and calibration remain
development tasks.

## Local Face Identification

The Arduino dispense monitor uses an independent worker for automatic face
identification. A valid positive-flow report triggers one three-frame burst per
pour when recognition is enabled, starting about one second after flow detection.
The Arduino and camera setup can run without GPIO inputs. A configured shutter
and a verified proximity sensor add capture gates. The worker matches the best
usable frame against encrypted local enrollments and reports
Guest when recognition is unavailable or ambiguous. The console summary
includes the Arduino TDS estimate and measured pour volume.
[Arduino monitor guide](operations.md#arduino-dispense-monitor)

### One-shot face identification prototype

Face identification uses short, explicitly triggered bursts of still photos.
There is no continuous monitoring or persistent camera preview. The default
recognition burst contains five photos, separated by 0.4 seconds after a
one-second camera warm-up. Captured and loaded images are processed at
640 x 480 resolution, keeping the working image size suitable for the Pi.
Capture uses a configured color format that supplies the BGR arrays expected
by OpenCV.

The vision component uses two OpenCV Zoo models: YuNet
(`face_detection_yunet_2023mar.onnx`) for detection and SFace
(`face_recognition_sface_2021dec.onnx`) for recognition. YuNet locates faces
and facial landmarks. When multiple faces appear, the largest face is selected.
The landmarks guide alignment and cropping before SFace extracts a numerical
embedding. Each embedding is normalized to unit length for cosine comparison.
Both models run locally through OpenCV, without a cloud inference service.

Enrollment accepts five to ten photos captured by the camera or supplied as
existing images. Every enrollment photo must contain a detectable face. The
resulting embeddings are associated with a user name; enrolling that name again
replaces its existing samples. During recognition, each usable frame is compared
with every enrolled sample. The highest sample score for each user is retained
for that frame, then averaged across frames containing a face.

The user with the highest average is returned only when that score is strictly
greater than 0.6. Otherwise, the result is `unknown`. If no frame contains a
detected face, the result is `no face detected`. Missing-face frames are skipped
rather than terminating recognition. The similarity score measures embedding
resemblance; it is not a probability of correct identity. This prototype does
not implement liveness detection or secure identity verification.

<div style="page-break-after: always;"></div>

## Controller Architecture and Persistence

The main controller uses asynchronous coordination for sessions, sensor polling,
health supervision, and optional network communication. Camera and inference
dependencies are kept outside the core measurement path. The architecture
provides a separate vision worker boundary. The Arduino monitor's real camera
worker is a standalone path alongside the main controller.

Flow-session software groups closely spaced pulse bursts into one dispense,
tracks a stable event identifier, and checkpoints active sessions. Monotonic
time supports elapsed intervals, while recorded events use UTC timestamps.
An interrupted session can be recovered as incomplete. The physical pulse-driver
binding and volume calibration still need verification; the system cannot
measure water while the Pi is unpowered.

SQLite provides local storage through a single writer thread with a bounded
queue. Write-ahead logging and transactional writes keep dispense records and
their corresponding outgoing events together. A durable outbox retains events
until the backend confirms database persistence. MQTT synchronization is designed
to use verified TLS and tolerate retries without treating publish success as
proof of backend storage. Live backend integration remains to be validated.

## Sensors, Safety, and Privacy

Sensor software distinguishes valid, stale, uncalibrated, and failed readings.
A single float switch represents a threshold state rather than an accurate tank
percentage. TDS and approximate pH measurements must not be presented as proof
that water is safe to drink. The current recognition demonstration prints a
random TDS value between 75 and 77 ppm; it is demonstration data, not a sensor
measurement, and is not persisted or transmitted.

Drain handling includes simulated checks for dispensing, tank condition, expiry,
replay, physical enable, and maximum operation time. Actual actuation remains
disabled. A physical installation requires a normally closed valve, suitable
driver protection, an accessible stop, and an independent hardware cutoff.
Software interlocks alone do not establish physical safety.

Camera captures remain in memory and are not intentionally saved or uploaded.
The Arduino monitor uses encrypted local enrollment and provides deletion.
The one-shot prototype uses a separate local JSON store with restricted file
permissions. Biometric embeddings require protection even when raw photographs
are held only in memory.

## Current Results and Remaining Work

The Pi detected and initialized the OV5647 camera, and enrollment succeeded using
resized saved photographs. Earlier full-resolution processing failed to detect
faces, leading to shared resizing and explicit camera-format configuration.
Successful live recognition with the latest changes has not yet been confirmed.
The repository records 52 earlier host tests passing for controller behavior;
these results do not validate the subsequent camera changes. Recent regression
tests were added but not executed at the user's request.

The next steps are live recognition trials, measured latency and memory use,
verification of the wired privacy inputs, physical sensor calibration, backend
synchronization tests, and safety validation. A two-to-five-second recognition
cycle remains a target rather than a measured guarantee. The current deliverable
combines a functioning saved-image enrollment path with simulated purifier
control; complete hardware operation and long-duration reliability remain open.

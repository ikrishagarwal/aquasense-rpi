# Environment and dependency inventory

Observed on 2026-11-01: Arch Linux, x86_64, Python **3.14.8**. Only AGENTS.md
existed initially. The mounted `.git` is not a usable Git repository; no commits
or version-control initialization were attempted. The adjacent backend is outside
this slice; no adjacent shared/ directory was supplied.

The target remains Raspberry Pi 3 Model B+, 1 GB RAM, Raspberry Pi OS Lite 64-bit.
The source compatibility floor is provisionally Python 3.11, not a verified Pi
interpreter. Exact Pi OS release, Python patch version, kernel, architecture,
camera module, GPIO backend and all installed hardware packages are **unverified**.
No bench access details were provided. M0 is not hardware-complete.

The stdlib scaffold needs no third-party runtime libraries. PyYAML 6.0.3 is the
M1 parser candidate and is available in the host system interpreter. An isolated
`.venv` was created, but installation from PyPI failed on DNS resolution. Pytest
9.0.2, Ruff 0.14.10, mypy 1.19.1 and types-PyYAML 6.0.12.20250915 are proposed
exact development pins, not verified installations. Setuptools 84.0.0 is the
host's installed build backend. These files are direct dependency pins, not a
fully resolved transitive lock. Resolve and record the complete environment on the
chosen Pi OS before claiming reproducibility. No hardware or vision packages are
silently installed as substitutes.

## Read-only bench inventory procedure

With actuator power disconnected, run and record:

```sh
python3 -m scripts.check_hardware
uname -a
getconf LONG_BIT
python3 --version
cat /etc/os-release
cat /proc/device-tree/model
dpkg-query -W python3-picamera2 python3-libcamera python3-lgpio python3-gpiozero
```

The inventory script reads bounded OS/model text only. Missing or unreadable
facts have explicit states; it does not prove electrical readiness. Record exact
board markings and measure voltages separately in wiring.md. Package presence
does not establish camera or GPIO operation. Choose the edge callback backend
only after verifying the OS/kernel API and measuring pulse capture on the bench.

Picamera2 should use the matching OS Picamera2/libcamera packages, installed via
apt; its future worker environment may need `--system-site-packages` to see those
bindings. See the [official camera documentation](https://www.raspberrypi.com/documentation/accessories/camera-software.html)
and [Picamera2 manual](https://datasheets.raspberrypi.com/camera/picamera2-manual.pdf).
Core must remain independent of these packages.

ARM64 ONNX/TFLite wheel availability has **not** been confirmed for the unknown
Pi interpreter. Once it is inventoried, use `python3 -m pip download --only-binary=:all:
--no-deps <candidate>==<version>` on that Pi with a single chosen candidate,
then verify installation, inference, model inputs/license and RSS in M6. Do not
select or pin a face model until those checks pass. This is a remaining M0
compatibility investigation and M6 performance gate, not evidence of support.

## Current implementation update

The core now runs simulation, creates SQLite state and supports optional network/
vision boundaries. Host PyYAML 6.0.3 and cryptography 50.0.2 were used in tests.
Paho 2.1.0 is pinned as an optional transport dependency but is not installed or
validated against a live broker here. A repeat dependency-install attempt after
the session resumed still failed. See test-results.md for current test counts.

# Security and privacy

The shipped profiles disable hardware actuation, network and vision. Enabling MQTT
requires a registered UUID, broker and per-device credentials supplied through the
configured environment variable names. Credentials, encryption keys and face data
must not enter version control. Protect environment files with mode 0600, owned by
the intended service account/root. No insecure TLS option exists.

`purifier.communication.mqtt` uses a verified SSLContext, hostname checking, minimum
TLS 1.2, bounded inbound/outbound queues and Paho callback API v2. Its optional
Paho dependency is not installed/tested against a real broker here. APIs were
checked against [official Paho documentation](https://eclipse.dev/paho/files/paho.mqtt.python/html/client.html).
Broker ACLs and the required backend changes are in BACKEND.md. Runtime commands
are always durably rejected as `actuation_disabled`; simulation interlock tests
are not authorization for a physical valve.

Core logs allow only event codes, timestamp, level and module; they omit arbitrary
exception text and payloads. Bounded journald rate settings are supplied with the
service templates. Logs do not include raw photos, embeddings or credentials.
The pipeline retains at most three ephemeral frame references and invokes one
embedding pass. Actual memory zeroization is not guaranteed by Python/native camera
buffers; no image is intentionally persisted, transmitted, or logged.

Enrollment storage uses AES-256-GCM with fresh 12-byte nonces, authenticated
user/model/dimension context and atomic replacement/fsync. Keys must contain 32
random bytes in a private regular file outside the enrollment directory. The store
supports replacement and deletion and caps persons/samples/dimensions. Crypto was
tested with host cryptography 50.0.2; an ARM64 wheel is not yet verified. API reference:
[cryptography authenticated encryption](https://cryptography.io/en/latest/hazmat/primitives/aead/).
Deletion unlinks current enrollment files; SD wear leveling and backups mean secure
physical erasure is not promised. Protect backups and delete corresponding backup
entries under the chosen retention policy. No raw photos are part of backups.

The simulation Unix worker accepts only bounded identify metadata. Its socket is
0660 in a private or trusted-group directory; do not grant the IPC group to other
users. The separate service user has no core secrets or database access. Actual
camera enrollment and administrative capture over the worker boundary remain
blocked until the model, camera and shutter adapters are validated. The encrypted
store API is not yet a complete enrollment UX.

A local deletion path runs entirely in the vision process:

```sh
python -m purifier.vision.enrollment delete --user-id USER_UUID --directory /var/lib/purifier-vision/enrollments --key /etc/purifier-vision/enrollment.key --model-id VALIDATED_MODEL_ID --dimension VALIDATED_DIMENSION
```

Run it under the vision service account after stopping the worker to avoid racing
its in-memory matches. Re-enrollment uses `EnrollmentService` with the actual camera
and model adapters; all captures check the shutter and only encrypted embeddings
are retained. Those physical adapters are still unavailable, so no real enrollment
command is exposed yet. Deletion must also invalidate any cached matcher references
before restarting the worker; the current simulated worker retains no embeddings.

# Architecture and current boundaries

The CLI composes an asyncio core, simulation pulse source, independent sensor
polling policies, session controller and one bounded SQLite writer thread. Core
supports local operation with absent sensors, vision, MQTT and backend. Heavy vision
and Pi driver packages are not imported into the core. Hardware mode is blocked.

A pulse callback appends timing/count information with constant-time bounded work.
The event loop takes a batch, updates sessions, and checkpoints once per batch.
Finalization uses the batch's cutoff time so a pulse arriving during an outstanding
checkpoint cannot be incorrectly split at a later wall time. Buffer overflow retains
a cumulative count fault but cannot reconstruct missing timing; it is explicit and
fatal to further normal measurement claims. The physical GPIO binding is pending.

SQLite uses WAL, FULL synchronous mode, foreign keys, a busy timeout and transactional
schema setup. Successful write futures mean commit. Sessions and outbox rows commit
atomically; checkpoints recover as incomplete after restart. A bounded writer queue
rejects saturation explicitly. Sensor history is bounded; pending outbox rows are
never discarded for retention. Identity revisions cannot alter measured fields or
increment filter usage twice. Only exact backend commit ACKs mark revisions synced.

The writer bridge polls only outstanding futures at 20 Hz. This avoids dependence
on asyncio executor completion socket writes, which this sandbox rejects. There is
one database writer thread; no per-event worker thread or unbounded task collection.

Vision is a separate process boundary. The IPC protocol accepts only compact IDs
and recognition metadata, caps payload size and outstanding jobs, enforces deadlines
and returns Guest on absent/invalid/late responses. The current worker is explicitly
simulation-only. Real capture, detection/alignment and embedding APIs must be supplied
by a validated adapter. The generic pipeline checks dispense/proximity/shutter
eligibility, retains three ephemeral frames and performs one embedding inference.
No images cross IPC. Core capture eligibility remains false until real inputs exist.

Enrollment storage uses authenticated encryption, user/model/dimension binding,
private key/files, atomic replacement and deletion. The vision-side enrollment
service checks the shutter during explicit enrollment. No actual camera/model is
selected, and no real-person identification accuracy is claimed.

MQTT is optional and runs Paho's network loop outside core. A bounded inbound queue
feeds strict parsing and durable command rejection; bounded outbox batches retry
until backend database-commit ACKs. A network failure never opens a valve. Runtime
commands are rejected as actuation_disabled. The separate simulation drain state
machine persists acceptance before opening, supervises interlocks, closes before
fault persistence, prevents replay and never resumes interrupted work.

Durable alerts use validated fresh readings and measured-volume counters. Health
reports retain unknown states for unavailable measurements. Service templates isolate
core/vision users and give neither simulation service access to real devices. No
MemoryMax is imposed before Pi measurements. See TODO.md for remaining gates and
BACKEND.md for required integration changes. No water can be measured while unpowered.

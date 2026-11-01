# AquaSense backend integration handoff

Inspected the adjacent `../aquasense-backend` TypeScript/Fastify/Supabase source.
No backend files were changed. A registered device UUID and reachable test broker
were not supplied. Local simulation does not establish interoperability.

## Observed differences and concrete changes

| File | Current behavior | Required implementation |
|---|---|---|
| `src/plugins/mqtt.ts` | Subscribes to `telemetry/status/+`, `telemetry/sessions/+`, `device/+/command_ack` | Subscribe to `purifier/+/events`, `/telemetry`, `/ack`, `/status`; expose a commit-ACK publisher to ingestion |
| `src/ingestion/schemas.ts` | Status requires numeric `tank_level_ml`; sessions use `id`, no schema/revision | Strict version-1 unions for dispense/alert events, telemetry and lifecycle ACKs; nullable sensor values plus quality; threshold tank state |
| `src/ingestion/handlers.ts` | `upsert(... ignoreDuplicates: true)` on session `id`; no persisted ACK | Call a transactional SQL RPC that enforces `(device_id,event_id)` uniqueness and revision checks; ACK only after RPC commits |
| `src/modules/devices/routes.ts` | Publishes `{command_id,command}` on legacy topic; supports flush/reboot/update | For this edge capability accept only drain; require owner/admin and explicit UI confirmation; construct expiring versioned envelope |
| Same route and ingestion ACK handler | `pending`/`delivered` followed by completed/failed only | Store received, accepted/rejected, running, completed/failed with timestamps/reasons; broker delivery is not acceptance |
| `supabase/migrations/002_device_members.sql` and dashboard/device queries | Tank-volume and filter-life assumptions; command insert policy allows members | Add a migration for threshold tank/nullable values and command history; restrict command creation to backend-authorized admin/owner path |

## Topics and payloads

All topics use the actual `devices.id` UUID. Broker ACLs must restrict each device
to publishing its own events, telemetry, ACK and status and subscribing only to
its own `events_ack` and `cmd`. Only the backend may publish commands/commit ACKs.
Use verified MQTT/TLS, per-device credentials, QoS 1 except telemetry QoS 0.
Commands must use `retain: false`; clear any old retained command messages during
migration. Do not log raw malformed payloads (the current ingestion handler does).

Dispense payload fields come from `purifier/core/session_manager.py` plus
`device_id`: schema_version, event_id, revision, user_id (`guest` or UUID), UTC
start_ts/end_ts, volume_ml (finite nonnegative number; do not truncate), pulse_count,
temperature_c, tds_in, tds_out, ph_approx, sensor_quality, confidence,
recognition_status, volume_quality, incomplete, quality_flags. Missing/unusable
sensors are null, never zero. TDS is ppm, temperature °C, pH approximate/unitless.
Volume may be uncalibrated. Store these qualifiers and expose them to the app.
Map `guest` to nullable user reference if required; preserve identity revisions.

`events` additionally carries `{type:"alert", name, active, timestamp}` plus the
schema/device/event/revision envelope. Store deduplication by event ID; send mobile
push only on a newly committed alert transition, using a transactional notification
outbox. In-memory alert maps alone lose deduplication on restart.

Commit ACK on `purifier/{device_id}/events_ack`:

```json
{"schema_version":1,"device_id":"registered-device-uuid","event_id":"event-uuid","revision":1,"status":"committed"}
```

Placeholders above are descriptive; actual fields must be canonical UUIDs.
Do not ACK rejected payloads, failed transactions, unknown devices, or mere MQTT
receipt. A duplicate exact committed revision should be ACKed again, covering lost
ACKs. For a stale revision already superseded, the backend must remember/verify
that revision's canonical payload before acknowledging it; never overwrite newer
identity. Equal revision with different payload is a conflict, not success.

Recommended SQL RPC transaction:

1. Validate registered device and payload; lock the event row (or serialize via
   a unique insert conflict path for first delivery).
2. Keep an immutable `(device_id,event_id,revision,payload_hash)` receipt table.
3. Insert initial revision 1 or update only revision `current+1`. Identity updates
   cannot change measured volume, pulse count, timestamps or calibration quality.
4. Insert a receipt and any alert notification work atomically. Return committed
   event ID/revision only after the transaction succeeds.
5. Publish the commit ACK. If publishing fails, the Pi retries; repeat steps safely.

For `ack` messages, the Pi simulation emits lifecycle payloads with
`type:"command_ack"`, command_id, event_id=command_id, revision, status, reason,
timestamp and `valve_state`. Apply them transactionally to command history and
acknowledge each revision on `events_ack` using the same commit envelope. This is
an explicit extension so lifecycle ACKs remain durable offline. Do not interpret
`completed`/`commanded_closed` as evidence that water physically drained.

Drain envelope:

```json
{"schema_version":1,"command_id":"command-uuid","device_id":"registered-device-uuid","type":"drain","issued_at":"2026-11-01T10:01:00Z","expires_at":"2026-11-01T10:01:15Z","duration_s":5}
```

Use server-generated IDs/UTC time, short TTL (Pi limit 30 s), duration within the
validated local maximum (software simulation ceiling 15 s). Persist request before
publish, require admin/owner confirmation and implement durable idempotency keys
for HTTP retries. Do not authorize using a payload boolean. The Pi rechecks
expiry, replay, dispensing, quiet time, fresh tank state, physical enable, driver,
storage/control health and independent cutoff. Runtime currently rejects actuation.

Telemetry should carry threshold tank `LOW/OK/UNKNOWN`, reading timestamps and
quality, pending outbox and health. Availability/LWT is independent of water state.
Remove fabricated tank millilitres and predictive filter-life claims; measured
filter usage is a counter, not a verified remaining-life estimate.

## Required integration tests

- Ten sessions while offline, reconnect, drop ACKs, resend duplicates: ten database
  records; every committed revision acknowledged; no counter double increment.
- Equal revision conflict, stale revision, identity revision, unknown/wrong-device
  topic, Guest/null sensors, malformed/oversized payload and transaction failure.
- Backend process death after commit before ACK; restart and retry succeeds once.
- Admin denial, missing confirmation, retained/expired/replayed commands, clock
  errors and every local interlock. No physical actuation in contract tests.
- Out-of-order lifecycle delivery, final state preservation and duplicate ACKs.
- Invalid/expired broker certificate and hostname mismatch: connection rejected.

Start with the Python in-memory backend tests in
`tests/integration/test_safety_sync.py`; these are contract examples, not evidence
that this adjacent backend has been upgraded or validated.

Retry a command with the same command ID if only MQTT delivery is known and no
lifecycle receipt arrives before expiry; never infer Pi acceptance from PUBACK.
Do not generate a fresh ID automatically to retry a possibly executed drain.
The Pi keeps durable replay history and re-emits a final ACK on duplicate intake.

# Version 1 contract fixtures

These deterministic UUIDs are **test fixtures only**, not registered devices.
Never copy them into a live device profile. Each fixture is a separate example;
reused example event IDs do not mean the alert and dispense should share a live ID.

- `dispense-v1.json`: uncalibrated local volume, Guest and unavailable sensors.
- `events-ack-v1.json`: confirms backend transaction commit for an exact revision.
- `drain-v1.json`: short-lived command; intentionally expired outside its test clock.
- `alert-v1.json`: durable threshold transition.
- `command-ack-v1.json`: durable lifecycle revision, including commanded state only.

Command ACK delivery uses the `events_ack` commit envelope as documented in
BACKEND.md. The real backend must adopt that extension before command history
synchronization can be claimed complete. Other payload definitions and migration
instructions are in BACKEND.md; local parsing lives in communication/schemas.py.

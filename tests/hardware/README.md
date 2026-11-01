# Hardware tests

No hardware tests or valve actuation code exist in M0. This directory is excluded
from default pytest collection. Future tests require an identified bench and
explicit opt-in; valve trials additionally require physical enable/emergency stop,
independent cutoff, verified driver polarity, and an empty/safe collection path.
Changing `drain.enabled` alone must never constitute authorization or readiness.

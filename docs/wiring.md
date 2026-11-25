# Proposed wiring contract — NOT VERIFIED FOR CONNECTION

No physical wiring was inspected or energized. Record actual manufacturer, board
revision, schematic, supply measurements and pin measurements before connecting
any affected device. BCM numbers below are configurable proposals for the Pi 3 B+
40-pin header; confirm orientation using the board's pin-1 marking.

| Function | BCM | Physical pin | Proposed level / connection | Unresolved gate |
|---|---:|---:|---|---|
| YF-S201 flow input | 17 | 11 | Rising edge, 0–3.3 V at GPIO | Exact variant, supply, output topology, level conditioning |
| DS18B20 data | 4 | 7 | External 3.3 V, ~4.7 kΩ pull-up to 3.3 V | Probe pinout and module pull-up |
| Tank float | 27 | 13 | 3.3 V safe contact input | Contact polarity, pull, debounce, open-wire fault behavior |
| PIR proximity | 22 | 15 | 0–3.3 V at GPIO | Board supply, output voltage and warm-up behavior |
| Drain driver | 23 | 16 | Driver logic only; disconnected | Active polarity, boot bias, all cutoffs |
| Illumination driver | 24 | 18 | Disabled; driver needed if load requires | Load, polarity, safe boot bias |
| Privacy shutter state | 25 | 22 | Capture disabled on unknown/closed | Contact polarity, wiring and debounce |
| I²C SDA | 2 | 3 | 3.3 V bus | Breakout pull-ups must not reach 5 V |
| I²C SCL | 3 | 5 | 3.3 V bus | Bus address, pull-ups and wiring |
| Sensor 3.3 V rail | — | 1 or 17 | Pi 3.3 V, within verified rail budget | Total sensor load |
| Ground reference | — | 6 (also other GND pins) | Logic reference | Ground topology, isolation, noise |
| Header 5 V rail | — | 2 or 4 | Never a GPIO signal | No proposed sensor connection until rated |

Pi power uses a suitable separate regulated Pi supply. The 12 V normally closed
valve uses its own correctly rated supply, fuse, coil driver and flyback protection.
Never drive its coil from GPIO. For a non-isolated driver, establish the required
common logic ground while keeping coil return current out of sensor/Pi paths;
for an isolated driver, follow its verified isolation schematic. Actual grounding
and protective components remain unresolved.

ADC proposal: ADS1115 at 3.3 V, A0 inlet TDS, A1 outlet TDS, A2 approximate pH,
A3 unused. Address, board revision, PGA range and conditioning are unknown.
Operating analog inputs must remain between GND and VDD. Selecting a larger
PGA range does not permit inputs above VDD. See the
[TI ADS1115 datasheet](https://www.ti.com/lit/ds/symlink/ads1115.pdf).
For each analog board, measure worst-case output (including faults) and document
divider/conditioning resistors, tolerance, source impedance and calibration impact.
No divider values are approved yet. Flow/PIR may also require conditioning;
never directly connect a 5 V signal. Pi signal levels are documented in the
[official hardware reference](https://www.raspberrypi.com/documentation/computers/raspberry-pi.html).

Float design should report unsafe on open wire where feasible; a single switch
cannot detect every fault or measure percentage/volume. Unknown means unsafe for
drain. Neither active polarity nor pull resistors are assumed. Camera model,
CSI cable/contact orientation, mounting rotation, field of view, shutter mechanics
and illumination are unknown; keep capture disabled until verified.

## Drain approval record (all pending)

- Driver part/coil rating, active-high or active-low logic and physical off bias.
- Normally closed behavior on power/control loss, including boot and process crash.
- Accessible manual emergency stop and series physical enable; sensing pin unassigned.
- Independent cutoff/relay or hardware watchdog, timing and reset semantics. It must
  remove coil power during a hung process; a Python timeout is insufficient.
- Maximum on-time, quiet period and fresh level requirements from bench validation.
- Safe collection path and deliberately enabled bench procedure (M8).

Keep the drain electronics disconnected. A future drain adapter requires
verified fail-safe output initialization and the hardware checks above.

## Arduino monitor camera privacy

The standalone Arduino monitor measures flow and TDS through USB serial. The
first valid positive-flow report signals presence for opt-in automatic capture.
For the Arduino and camera setup, leave `"shutter": null` and `"proximity": null`.
If adding GPIO gates, GPIO Zero with lgpio can read a shutter contact on BCM 25
(physical pin 22) or a proximity sensor on BCM 22 (physical pin 15). Keep actuator
power disconnected during camera verification.

For fitted inputs, record the sensor and switch variants, supply voltages,
ground reference, logic levels, active polarities, pull resistors, and camera
orientation on the bench. Verify each signal is 3.3 V safe before connection. Check that the
shutter contact reports eligible only when the shutter is open. If fitted, check
that proximity reports eligible only for the intended detection area, including
sensor warm-up.
The software selects a pull opposite each input's eligible level. Verify each
line reads ineligible with the device disconnected, unpowered, and in its
ineligible state. Resolve any external pull or module output that overrides the
safe bias before setting `verified` in the Arduino vision JSON.

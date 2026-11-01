# Calibration and physical acceptance

No physical calibration has been performed. 450 pulses/litre remains a placeholder;
software events therefore report `volume_quality: uncalibrated`. Do not mark it
measured merely because a simulated volume matches the conversion formula.

For flow, collect 20 pours each near 100, 250 and 500 ml at varied flow rates using
a calibrated reference volume or mass with documented density assumptions. Record
CSV columns `nominal_ml,reference_ml,pulses` and run:

```sh
python -m scripts.calibrate_flow measured-pours.csv --pulses-per-litre 450
```

The tool reports mean/max absolute percentage error, proportion within ±5%, and a
suggested aggregate factor. Retest with the fitted factor on independent pours;
it never changes config automatically or manufactures missing trials. Record sensor
variant, installation orientation, rates and low-flow limitations.

For each TDS board record its transfer function, ADC rail/conditioning, temperature
compensation method and a reference-meter comparison; target ±10%. The provided
linear conversion class is only valid for a board whose calibrated voltage-to-unit
relationship is actually linear. It is not a universal TDS algorithm. Calibration
config remains blocked until an actual board adapter/transfer function is selected.
Approximate pH additionally needs reference solutions and an explicitly documented
transfer curve/range. TDS/pH do not prove water safety.

For vision record camera mounting/lighting, exact model hash, input shape/channel
order, normalization, detection/alignment API and license. Benchmark on the actual
ARM64 interpreter before selecting the runtime. Measure at least 50 trials across
5–10 people, reporting correct identification, Guest and false-match rates separately.
Choose threshold and top-two-person margin from those results. No real model has
been selected; the generic pipeline tests use numbers, not photos or real faces.

Drain trials remain blocked until wiring.md's physical gates pass. Then perform 20
deliberately enabled bench trials into a safe collection path, including physical
stop, control-signal loss, process hang and independent cutoff tests. Measure local
accept-to-command latency separately from app/network latency. Without feedback,
report only commanded states, never proven physical draining.

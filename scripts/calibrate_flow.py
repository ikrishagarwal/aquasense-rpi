"""Summarize measured pour CSV; never fabricates trials or writes calibration."""

import argparse
import csv
import json
import math
from pathlib import Path


def summarize(path: Path, factor: float) -> dict[str, object]:
    if not math.isfinite(factor) or factor <= 0:
        raise ValueError('positive pulses-per-litre required')
    groups: dict[int, list[float]] = {100: [], 250: [], 500: []}
    total_pulses, total_litres = 0, 0.0
    with path.open(newline='') as stream:
        for index, row in enumerate(csv.DictReader(stream)):
            if index >= 10000:
                raise ValueError('trial file exceeds 10000 rows')
            nominal = int(row['nominal_ml'])
            actual, pulses = float(row['reference_ml']), int(row['pulses'])
            if nominal not in groups or not math.isfinite(actual) or actual <= 0 or pulses <= 0:
                raise ValueError('invalid measured trial')
            error = abs(pulses / factor * 1000 - actual) / actual * 100
            groups[nominal].append(error)
            total_pulses += pulses
            total_litres += actual / 1000
    if not total_litres:
        raise ValueError('no measured trials')
    return {'tested_factor': factor, 'suggested_factor': total_pulses / total_litres,
            'groups': {str(size): {'trials': len(errors),
                'mean_absolute_error_percent': sum(errors)/len(errors) if errors else None,
                'max_absolute_error_percent': max(errors) if errors else None,
                'proportion_within_5_percent': sum(e <= 5 for e in errors)/len(errors) if errors else None,
                'minimum_trial_count_met': len(errors) >= 20} for size, errors in groups.items()}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('csv', type=Path)
    parser.add_argument('--pulses-per-litre', type=float, required=True)
    args = parser.parse_args()
    print(json.dumps(summarize(args.csv, args.pulses_per_litre), indent=2))


if __name__ == '__main__':
    main()

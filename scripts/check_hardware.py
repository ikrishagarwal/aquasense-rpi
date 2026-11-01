"""Print read-only inventory; no probing, imports of drivers, or actuation."""

import json

from purifier.utils.inventory import collect

if __name__ == "__main__":
    print(json.dumps(collect(), indent=2))

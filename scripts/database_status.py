"""Read-only database counts/integrity; never prints enrollment or event payloads."""

import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database', type=Path)
    args = parser.parse_args()
    with closing(sqlite3.connect(args.database.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        result = {'integrity': db.execute('PRAGMA quick_check').fetchone()[0],
                  'sessions': db.execute('SELECT COUNT(*) FROM dispense_sessions').fetchone()[0],
                  'pending_outbox': db.execute('SELECT COUNT(*) FROM outbox WHERE acknowledged=0').fetchone()[0],
                  'active_sessions': db.execute('SELECT COUNT(*) FROM active_sessions').fetchone()[0]}
        print(json.dumps(result))


if __name__ == '__main__':
    main()

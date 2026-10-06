"""Read-only check of a copied HA ``.storage`` directory against the record registry.

Usage: python scripts/inspect_records.py <path-to-.storage>

Intended for isolated restore drills. It opens files read-only, never writes,
renames or repairs them, and contacts nothing. Exit status 1 means at least one
BSV settlement file is unregistered or would be refused at load. A clean result
is not freshness evidence: a coherent older backup also passes.
"""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from custom_components.bsv_settlement.records import inspect_directory  # noqa: E402


def main(argv):
    if len(argv) != 2 or not Path(argv[1]).is_dir():
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2
    rows = inspect_directory(argv[1])
    print(json.dumps(rows, indent=2))
    return 0 if all(r["result"] == "ok" for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))

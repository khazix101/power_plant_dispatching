"""Audit the tracked data/ examples without opening Dataset/ private data."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.validation.data_audit import audit_site_data


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=root / "data",
                        help="Only the five documented example CSVs in this directory are scanned")
    parser.add_argument("--output", type=Path,
                        default=root / "local/results/d_validation/data_quality_audit.json")
    args = parser.parse_args()
    result = audit_site_data(args.data_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True),
                           encoding="utf-8")
    print(json.dumps({"output": str(args.output), "site_count": result["site_coverage"]["site_count"],
                      "files_audited": len(result["files"]),
                      "private_raw_data_read": result["privacy"]["private_raw_data_read"]},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

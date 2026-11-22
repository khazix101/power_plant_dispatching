"""Inspect raw ERA5 SSRD GRIB accumulation metadata for the second draft."""
import json
from pathlib import Path
import eccodes

root = Path.cwd()
out = {}
for name in ("data.grib", "4be969e0cf4eb24bf77b25e39779b8f1.grib"):
    path = root / "Dataset" / "10.09" / name
    examples = []
    counts = {}
    with path.open("rb") as handle:
        while True:
            gid = eccodes.codes_grib_new_from_file(handle)
            if gid is None:
                break
            try:
                if eccodes.codes_get(gid, "shortName") != "ssrd":
                    continue
                item = {key: eccodes.codes_get(gid, key) for key in (
                    "stepRange", "stepType", "validityDate", "validityTime", "dataDate", "dataTime")}
                counts[str(item["stepRange"])] = counts.get(str(item["stepRange"]), 0) + 1
                if len(examples) < 8:
                    examples.append(item)
            finally:
                eccodes.codes_release(gid)
    out[name] = {"ssrd_messages": sum(counts.values()), "stepRange_counts": counts,
                 "examples": examples}
dest = root / "parper" / "era5_ssrd_metadata_audit.json"
dest.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
print(dest)
print(json.dumps({k: {"messages":v["ssrd_messages"],"ranges":v["stepRange_counts"]} for k,v in out.items()}))

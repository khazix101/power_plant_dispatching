"""Resume the 16:00 batch's partial matrix cases to the next 08:00 CST cutoff."""
import hashlib, json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "parper/experiment_runs/soh_date_matrix_v4_continued_to_1600_20261113"
TARGET = ROOT / "parper/experiment_runs/soh_date_matrix_v4_continued_to_0800_20261114"
def h(b): return hashlib.sha256(b).hexdigest()
def write(p,v): p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
if TARGET.exists(): raise SystemExit(f"Continuation already exists: {TARGET}")
oldb=(SOURCE/"manifest.json").read_bytes(); old=json.loads(oldb); m=dict(old); m["output"]=str(TARGET); m["deadline_utc"]="2026-11-14T00:00:00+00:00"; m["continuation"]={"parent_output":str(SOURCE),"parent_manifest_file_sha256":h(oldb),"parent_manifest_sha256":old["manifest_sha256"],"created_utc":datetime.now(timezone.utc).isoformat(),"reason":"User requested completion beyond the 16:00 CST cutoff","unchanged":"cases, inputs, frozen source snapshot, solver, physical model and acceptance gates"}; m.pop("manifest_sha256",None); m["manifest_sha256"]=h(json.dumps(m,sort_keys=True).encode()); write(TARGET/"manifest.json",m)
rows=[]
for case in old["cases"]:
 p=SOURCE/"cases"/(case["case_id"]+".json")
 if not p.exists(): continue
 b=p.read_bytes(); d=json.loads(b); d["continuation_parent_case_sha256"]=h(b); d["continuation_parent_status"]=d["status"]; d["manifest_sha256"]=m["manifest_sha256"];
 if d["status"] in ("running","deadline_partial","continuation_pending"): d["status"]="continuation_pending"
 write(TARGET/"cases"/(case["case_id"]+".json"),d); rows.append({"case_id":case["case_id"],"parent_status":d["continuation_parent_status"],"parent_file_sha256":h(b),"copied_steps":len(d.get("steps",[]))})
write(TARGET/"CONTINUATION_PROVENANCE.json",{"parent_manifest_file_sha256":h(oldb),"continuation_manifest_sha256":m["manifest_sha256"],"copied_cases":len(rows),"copied_steps":sum(r["copied_steps"] for r in rows),"note":"Parent batch is untouched; completed cases remain terminal and partial cases resume at recorded half-hour boundaries."})
print(json.dumps({"target":str(TARGET),"copied_cases":len(rows),"copied_steps":sum(r["copied_steps"] for r in rows)}))

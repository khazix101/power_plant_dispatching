"""Print synthetic centralized vs independent audit JSON; never write datasets."""
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.game.multisite import solve_centralized, solve_independent, synthetic_case


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sites",type=int,choices=(2,4),default=2)
    parser.add_argument("--periods",type=int,default=4)
    parser.add_argument("--time-limit",type=float,default=60.)
    parser.add_argument("--full",action="store_true",help="Include all interval audit records")
    parser.add_argument("--request",type=Path,help="A-contract JSON object: run_id, input, config, contract_version")
    parser.add_argument("--scale",action="store_true",help="Benchmark 2/4 sites at 48/96/336 half-hour steps")
    args = parser.parse_args()
    if args.request:
        from src.multisite.contract import MultiSiteInput, CoordinationRequest
        from src.game.multisite_coordinator import CentralizedCoordinator
        payload = json.loads(args.request.read_text(encoding="utf-8"))
        payload["input"] = MultiSiteInput.from_dict(payload["input"])
        result = CentralizedCoordinator().coordinate(CoordinationRequest(**payload))
        print(json.dumps(asdict(result),indent=2,allow_nan=False))
        return
    if args.scale:
        runs = []
        for count in (2,4):
            for periods in (48,96,336):
                r = solve_centralized(synthetic_case(count,periods),time_limit=args.time_limit)
                r.pop("schedule",None)
                r.pop("ties",None)
                r.get("checks",{}).pop("records",None)
                runs.append(dict(sites=count,periods=periods,**r))
        print(json.dumps(dict(synthetic=True,random_seed=0,runs=runs),indent=2,allow_nan=False))
        return
    case = synthetic_case(args.sites,args.periods)
    revision = subprocess.check_output(["git","rev-parse","HEAD"],cwd=Path(__file__).resolve().parents[1],text=True).strip()
    runs = [solve_centralized(case,time_limit=args.time_limit),solve_independent(case,time_limit=args.time_limit)]
    if not args.full:
        for r in runs:
            r.pop("schedule",None)
            r.pop("ties",None)
            r.pop("site_runs",None)
            r.get("checks",{}).pop("records",None)
    case_config = asdict(case)
    digest = hashlib.sha256(json.dumps(case_config,sort_keys=True,allow_nan=False).encode()).hexdigest()
    print(json.dumps(dict(source_revision=revision,synthetic=True,random_seed=0,config=case_config,
                         input_config_sha256=digest,sites=args.sites,periods=args.periods,runs=runs),indent=2,allow_nan=False))


if __name__ == "__main__":
    main()

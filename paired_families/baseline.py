"""Predict a deadline from source completion frequency and the frozen episode speed factor."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics as st
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from compare import interval


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def sealed(p):
    receipt=json.loads(p.with_suffix(".seal.json").read_text())
    if sha(p)!=receipt["sha256"]:
        raise ValueError("Invalid seal: "+p.name)
    return json.loads(p.read_text())


def predict(study, model, destination):
    is_qwen=model=="qwen"
    seq=study/"raw" if is_qwen else study/"raw"/model
    out=study/"analysis" if is_qwen else study/"analysis"/model
    folder=seq/"source"/sealed(seq/"source/accepted.json")["attempt"]/"calibration"
    obs=[]
    for f in sorted(folder.glob("*.json")):
        events=json.loads(f.read_text())["events"]
        if len(events)<=32:
            continue
        if not events[-1]["is_terminal"] and len(events)!=1024:
            raise ValueError("Invalid source calibration outcome")
        obs.append(events[-1]["server_ready_ns"]-events[31]["server_ready_ns"]
                   if events[-1]["is_terminal"] else None)
    if not obs:
        raise ValueError("No active source calibration observations")
    deadlines=[Decimal(str(d))*10**9 for d in sealed(out/"fit/model.json")["grid"]]
    if not (study/"design/confirmation_lock.json").is_file():
        raise ValueError("Confirmation size must be fixed before issuing forecasts")
    ready=sorted((out/"forecasts").glob("confirmation-[0-9][0-9][0-9].seal.json"))
    destination.mkdir(parents=True,exist_ok=True)
    for item in ready:
        episode=item.name.replace(".seal.json","")
        f=item.with_name(episode+".json")
        episode_forecast=sealed(f)
        scale=Decimal(str(episode_forecast["history"]["direct_scale"]))
        if not scale.is_finite() or scale<=0:
            raise ValueError("Invalid scale")
        p=[sum(t is not None and Decimal(t)*scale<=d for t in obs)/len(obs) for d in deadlines]
        record=dict(episode=episode,frozen_forecast_sha256=sha(f),source_active=len(obs),
                    deadlines_seconds=[float(d/10**9) for d in deadlines],scale=float(scale),
                    natural_durations_ns=obs,p=p)
        dest=destination/(episode+".json")
        data=json.dumps(record,indent=2,allow_nan=False)+"\n"
        if dest.exists() and dest.read_text()!=data:
            raise ValueError("Previously issued baseline changed: "+episode)
        if not dest.exists():
            dest.write_text(data)
    return len(ready)


def score(table, destination):
    with table.open(newline="") as f:
        rows=[r for r in csv.DictReader(f) if r.get("regime","contention")=="contention"]
    by=defaultdict(list)
    for r in rows:
        by[int(r["episode"])].append(r)
    if sorted(by)!=list(range(1,len(by)+1)):
        raise ValueError("Episode gaps")
    all_loss=[]
    s={m:[] for m in ("hazard","direct_scale")}
    counts={m:[] for m in s}
    for ep, sample in sorted(by.items()):
        rec=json.loads((destination/f"confirmation-{ep-1:03d}.json").read_text())
        grid=[Decimal(str(g))*10**9 for g in rec["deadlines_seconds"]]
        differences={m:[] for m in s}
        for r in sample:
            if int(r["steps"])<=32:
                continue
            y=[int(int(r["eos"])==1 and int(r["elapsed_after_32_ns"])<=d) for d in grid]
            base=st.mean((p-a)**2 for a,p in zip(y,rec["p"]))
            all_loss.append(base)
            for m in differences:
                probs=[r[f"{m}_d{j}"] for j in (1,2,3)]
                if any(p=="" for p in probs):
                    continue
                differences[m].append(base-st.mean((float(p)-a)**2 for a,p in zip(y,probs)))
        for m,vals in differences.items():
            s[m].append(math.fsum(vals));counts[m].append(len(vals))
    return {"eligible":len(all_loss),"brier":st.mean(all_loss) if all_loss else None,
            "control_minus_method":{m:{"n":sum(counts[m]),"interval":interval(s[m],counts[m])} for m in s}}


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest="cmd",required=True)
    a=sub.add_parser("predict")
    a.add_argument("study",type=Path)
    a.add_argument("model",choices=("qwen","olmo","ministral"))
    a.add_argument("destination",type=Path)
    b=sub.add_parser("score")
    b.add_argument("table",type=Path);b.add_argument("destination",type=Path)
    args=p.parse_args()
    if args.cmd=="predict":
        print(predict(args.study,args.model,args.destination))
    else:
        print(json.dumps(score(args.table,args.destination),indent=2))

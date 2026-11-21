"""Compare the public per-offer table with the retained private Metal capture."""
import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path


def check(ok, reason):
    if not ok:
        raise ValueError(reason)


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1<<20), b""):
            h.update(chunk)
    return h.hexdigest()


def read(path):
    return json.loads(path.read_bytes())


def sealed(path):
    check(digest(path)==read(path.with_suffix(".seal.json"))["sha256"], "Seal mismatch: "+path.name)
    return read(path)


def canonical(obj):
    return hashlib.sha256(json.dumps(obj,sort_keys=True,separators=(",",":")).encode()).hexdigest()


def native_rows(folder, ids, eog):
    by_id = {}
    with (folder/"progress.jsonl").open("rb") as f:
        for line in f:
            p=json.loads(line)
            check(p["status"]=="complete" and p["task_id"] not in by_id,"Duplicate or incomplete task")
            by_id[p["task_id"]]=p["request_id"]
    check(set(by_id.values())==ids and len(by_id)==len(ids),"Unexpected foreground assignments")
    current, finished, shared = {}, {}, Counter()
    with (folder/"events.jsonl").open("rb") as f:
        for line in f:
            e=json.loads(line)
            tid=e["task_id"]
            if e["slot_id"]!=0 or tid not in by_id:
                continue
            rid, kind=by_id[tid], e["event_type"]
            if kind=="request_admission":
                check(rid not in current and rid not in finished and e["max_tokens"]==1024, "Admission")
                current[rid]={"steps":0,"prefix":[],"last":None,"eos":False,"seed":e["generation_seed"]}
            elif kind=="token":
                r=current[rid]
                ts=e["server_ready_ns"]
                check(e["step_index"]==r["steps"]+1 and not r["eos"] and r["steps"]<1024 and
                      (r["last"] is None or ts>=r["last"]) and e["clock_domain"]=="server_steady_clock" and
                      e["timing_source"]=="host_ready" and e["is_terminal"]==(e["token_id"] in eog),"Native step")
                r.update(steps=e["step_index"],eos=e["is_terminal"],last=ts)
                if r["steps"]<=32:
                    item={k:e[k] for k in ("step_index","token_id","server_ready_ns","batch_id","batch_size")}
                    item["request_context_tokens"]=e["context_tokens"]
                    r["prefix"].append(item)
                if r["steps"]>32 and 1 in e["batch_slots"]:
                    shared[rid]+=1
            elif kind=="request_finish":
                r=current.pop(rid)
                check(e["decode_steps"]==r["steps"] and e["is_terminal"]==r["eos"] and r["last"]==e["server_ready_ns"] and
                      (r["eos"] or r["steps"]==1024),"Invalid native finish")
                prefix=r.pop("prefix")
                if r["steps"]>32:
                    t32=prefix[31]["server_ready_ns"]
                    r.update(elapsed=r["last"]-t32, prefix_hash=canonical(dict(request_id=rid,k=32,feature_cutoff_ns=t32,tokens=prefix)))
                finished[rid]=r
    check(not current and set(finished)==ids,"Incomplete native trace")
    return finished,shared


def audit(root, phase):
    scores=root/"analysis/results"/phase
    forecasts=root/"analysis/forecasts"
    index=sealed(forecasts/(phase+"_index.json"))
    technical=sealed(root/"capture/technical.json")
    with (scores/"requests.csv").open(newline="") as f:
        rows=list(csv.DictReader(f))
    roster={(int(r["episode"]),int(r["offer"])):r for r in rows}
    check(len(roster)==len(rows)==16*len(index),"Incomplete table")
    evidence={}
    total=Counter()
    for b,(name,expected) in enumerate(sorted(index.items()),1):
        check(name==f"{phase}-{b-1:03}" and digest(forecasts/(name+".json"))==expected, "Unexpected forecast")
        pred=sealed(forecasts/(name+".json"))
        pointer=sealed(root/"raw"/name/"accepted.json")
        folder=root/"raw"/name/pointer["attempt"]
        check(digest(folder/"complete.json")==pointer["complete_sha256"], "Changed capture")
        closure=read(folder/"complete.json")
        evidence[name]={}
        for k,file in (("raw_trace_sha256","events.jsonl"),("schedule_sha256","schedule.json"),("run_sha256","run.json")):
            check(digest(folder/file)==closure[k], "Source digest mismatch: "+file)
            evidence[name][file]=closure[k]
        run=read(folder/"run.json")
        check(run["model_sha256"]==technical["model_sha256"] and run["ctx_per_slot"]==2048, "Model or context mismatch")
        plan=read(folder/"schedule.json")
        ids={a["request_id"] for a in plan}
        native,shared=native_rows(folder,ids,set(technical["eog_ids"]))
        history=[a["request_id"] for a in plan if a["split"]=="adaptation"]
        check(len(history)==5 and pred["history"]["request_ids"]==history, "History mismatch")
        test=[a for a in plan if a["split"]=="pilot_test"]
        check(len(test)==len(pred["rows"])==16, "Test size")
        episode_shared=0
        for i,(a,p) in enumerate(zip(test,pred["rows"]),1):
            r=roster[b,i]; rid=a["request_id"]; n=native[rid]
            check(a["regime"]=="contention" and n["seed"]==a["seed"] and rid==p["request_id"] and p["offer"]==i,"Wrong assignment")
            check(int(r["steps"])==n["steps"] and bool(int(r["eos"]))==n["eos"],"Export differs from native finish")
            total["test_offers"]+=1
            if n["steps"]<=32:
                check(not p["eligible"] and not r["elapsed_after_32_ns"], "Early end")
                continue
            check(p["eligible"] and int(r["elapsed_after_32_ns"])==n["elapsed"] and p["prefix_hash"]==n["prefix_hash"],"Native landmark mismatch")
            for method in ("direct_scale","hazard"):
                probs=[r[f"{method}_d{j}"] for j in (1,2,3)]
                if p.get(method) is None:
                    check(not any(probs), "Unsupported score mismatch")
                else:
                    check(list(map(float,probs))==p[method], "Export differs from issued probabilities")
            total["eligible"]+=1
            total["test_post32_steps"]+=n["steps"]-32
            episode_shared+=shared[rid]
        total["episodes_with_shared_test_decode"]+=int(episode_shared>0)
        total["test_shared_post32_steps"]+=episode_shared
    return dict(phase=phase,episodes=len(index),counts=dict(total),source_sha256=evidence)


if __name__=="__main__":
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("study",type=Path)
    ap.add_argument("phase",choices=("development","confirmation"))
    args=ap.parse_args()
    print(json.dumps(audit(args.study,args.phase),indent=2))

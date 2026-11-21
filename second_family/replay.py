"""Recalculate the registered Qwen comparison from request-level numerical data."""
import argparse
import csv
import hashlib
import json
import math
import statistics
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path


METHODS = ("direct_scale", "hazard")


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def t95(df):
    """Two-sided 95% critical value using the cosine-power form of Student's t."""
    def area(theta):
        s, c = math.sin(theta), math.cos(theta)
        v = s if (df-1) % 2 else theta
        for n in range(3 if (df-1) % 2 else 2, df, 2):
            v = (s*c**(n-1) + (n-1)*v)/n
        return v
    a, b = 0.0, math.pi/2
    target = area(b)*0.95
    for _ in range(60):
        mid = (a+b)/2
        if area(mid) < target:
            a = mid
        else:
            b = mid
    return math.sqrt(df)*math.tan((a+b)/2)


def ratio_interval(sums, counts):
    n = len(counts)
    total = sum(counts)
    require(n >= 2 and total > 0 and len(sums) == n, "Insufficient episode denominators")
    d = math.fsum(sums)/total
    influences = [(x-d*k)/(total/n) for x,k in zip(sums,counts)]
    half = t95(n-1)*statistics.stdev(influences)/math.sqrt(n)
    return dict(estimate=d, lower_95=d-half, upper_95=d+half, half_width=half)


def bit(value):
    require(str(value).lower() in ("true","false","0","1"), "Expected a boolean flag")
    return str(value).lower() in ("true","1")


def decision(interval, complete):
    if not complete:
        return "incomplete coverage"
    lo, hi = interval["lower_95"], interval["upper_95"]
    if lo >= -0.01 and hi <= 0.01:
        return "practical equivalence"
    if lo > 0.01:
        return "practical advantage for hazard"
    if hi < -0.01:
        return "practical advantage for direct"
    return "inconclusive"


def analyze(rows, grid, n):
    grid_ns = [Decimal(str(d))*10**9 for d in grid]
    require(len(grid_ns)==3 and grid_ns==sorted(set(grid_ns)) and grid_ns[0]>0, "Invalid grid")
    seen = set()
    positive, active, fallback = Counter(), Counter(), Counter()
    per_episode = defaultdict(list)
    for r in rows:
        e, i = int(r["episode"]), int(r["offer"])
        require(1<=e<=n and 1<=i<=16 and (e,i) not in seen, "Invalid or repeated assignment")
        seen.add((e,i))
        k, eos = int(r["steps"]), bit(r["eos"])
        require(0<k<=1024 and (eos or k==1024), "Invalid terminal step")
        vectors = {m:[r[f"{m}_d{j}"] for j in (1,2,3)] for m in METHODS}
        if k<=32:
            require(not r["elapsed_after_32_ns"] and not any(v for p in vectors.values() for v in p),
                    "Ineligible assignment has forecast or remaining time")
            continue
        active[e] += 1
        elapsed = int(r["elapsed_after_32_ns"])
        require(elapsed>=0, "Remaining time below zero")
        y = [int(eos and elapsed<=d) for d in grid_ns]
        positive.update({j:v for j,v in enumerate(y)})
        scores = {}
        for method, vector in vectors.items():
            if method=="direct_scale" and not bit(r["direct_scale_supported"]):
                require(not any(vector), "Probability supplied outside model support")
                continue
            p = [float(x) for x in vector]
            require(all(0<=x<=1 for x in p) and all(a<=b+1e-12 for a,b in zip(p,p[1:])),
                    "Invalid probability vector")
            fallback[method] += bit(r[method+"_fallback"])
            scores[method] = [(x-z)**2 for x,z in zip(p,y)]
        if len(scores)==2:
            per_episode[e].append(scores)
    require(len(seen)==16*n, "Incomplete assignment roster")
    blocks = [per_episode[e] for e in range(1,n+1)]
    counts = [len(b) for b in blocks]
    paired, eligible = sum(counts), sum(active.values())
    res = dict(episodes=n, assigned=len(seen), eligible=eligible, paired=paired,
               eligible_by_episode=[active[e] for e in range(1,n+1)], paired_by_episode=counts,
               positives_by_deadline=[positive[j] for j in range(3)],
               fallbacks={m:fallback[m] for m in METHODS})
    if not paired:
        res["decision"] = "incomplete coverage"
        return res
    sums = [math.fsum((math.fsum(row["direct_scale"])-math.fsum(row["hazard"]))/3 for row in b) for b in blocks]
    ci = ratio_interval(sums, counts)
    res.update(brier={m:math.fsum(math.fsum(row[m])/3 for b in blocks for row in b)/paired for m in METHODS},
               direct_minus_hazard=ci, decision=decision(ci, paired==eligible))
    res["descriptive_deadlines"]=[]
    for j in range(3):
        differences=[math.fsum(row["direct_scale"][j]-row["hazard"][j] for row in b) for b in blocks]
        res["descriptive_deadlines"].append(dict(
            deadline_seconds=float(grid[j]), positive=positive[j], eligible=eligible, paired=paired,
            brier={m:math.fsum(row[m][j] for b in blocks for row in b)/paired for m in METHODS},
            direct_minus_hazard=ratio_interval(differences,counts)))
    return res


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory",type=Path,nargs="?",default=Path(__file__).resolve().parent)
    d=parser.parse_args().directory
    meta=json.loads((d/"study.json").read_text(),parse_float=Decimal)
    outputs={}
    for split,n in (("development",4),("confirmation",meta.get("confirmation_episodes",0))):
        if not n:
            continue
        path=d/(split+".csv")
        require(hashlib.sha256(path.read_bytes()).hexdigest()==meta["sha256"][path.name], "Table digest mismatch")
        with path.open(newline="") as f:
            outputs[split]=analyze(csv.DictReader(f),meta["deadlines_s"],n)
    print(json.dumps(outputs,indent=2,allow_nan=False))


if __name__=="__main__":
    main()

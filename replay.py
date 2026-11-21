"""Recalculate the paper's two main endpoints using only the minimized CSVs."""
import argparse
import csv
import json
import math
import statistics
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path


POLICIES = ("forecast", "expiry", "fifo")
METHODS = ("aft", "hazard", "direct_scale")
T95 = {11: 2.200985160082949, 15: 2.131449545559323}


def check(ok, msg):
    if not ok:
        raise ValueError(msg)


def table(path):
    with path.open(newline="", encoding="utf-8") as src:
        return list(csv.DictReader(src))


def interval(numerators, denominators):
    n = len(denominators)
    denominator = sum(denominators)
    estimate = math.fsum(numerators) / denominator
    influence = [(a-estimate*b) / (denominator/n) for a, b in zip(numerators, denominators)]
    half = T95[n-1] * statistics.stdev(influence) / math.sqrt(n)
    return {"estimate": estimate, "lower_95": estimate-half, "upper_95": estimate+half}


def live(rows):
    seen, schedule, periods = set(), {}, {}
    summary = {p: Counter() for p in POLICIES}
    timely = defaultdict(Counter)
    for r in rows:
        b, i, p, order = int(r["block"]), int(r["offer"]), r["policy"], int(r["period"])
        key = (b, i, p)
        check(1 <= b <= 12 and 1 <= i <= 12 and p in POLICIES and key not in seen, "Live roster")
        seen.add(key)
        arrival, deadline, dispatch = (int(r[k]) for k in ("arrival_ns","deadline_ns","dispatch_ns"))
        expected_arrival = 4*10**9*((i-1)//3) + (0,120000000,320000000)[(i-1)%3]
        check(schedule.setdefault((b,i),arrival) == arrival == expected_arrival and deadline == arrival+10**9 and
              0 <= arrival <= dispatch and deadline <= 14*10**9, "Invalid offer time")
        check(periods.setdefault((b,p), order) == order and 1 <= order <= 3, "Policy period")
        outcome = r["end"]
        steps = int(r["native_steps"])
        check(outcome in ("eos","limit","cancelled") and 0 < steps <= 1024, "Native outcome")
        check(outcome != "limit" or steps == 1024, "Cap reached at wrong step")
        check(outcome != "cancelled" or (p != "fifo" and steps >= 32), "Invalid controller cancellation")
        check((outcome == "cancelled") == bool(r["cancel_ns"]) and
              (outcome != "cancelled") == bool(r["final_frame_ns"]), "Missing or conflicting client event")
        frame_or_cancel = int(r["final_frame_ns"] or r["cancel_ns"])
        check(dispatch <= frame_or_cancel, "Receipt before dispatch")
        check(p != "expiry" or outcome != "cancelled" or frame_or_cancel >= deadline,
              "Expiry cancelled before deadline")
        success = outcome == "eos" and frame_or_cancel <= deadline
        summary[p]["offers"] += 1
        summary[p][outcome] += 1
        summary[p]["timely"] += success
        timely[b][p] += success
    check(len(seen) == 432 and all({periods[b,p] for p in POLICIES} == {1,2,3} for b in timely), "Incomplete roster")
    orders = Counter(tuple(sorted(POLICIES, key=lambda p: periods[b,p])) for b in timely)
    check(len(orders) == 6 and set(orders.values()) == {2}, "Unbalanced crossover")
    paired = [60/14 * (timely[b]["forecast"] - timely[b]["expiry"]) for b in sorted(timely)]
    return dict(by_policy={p:{k:summary[p][k] for k in ("offers","timely","eos","limit","cancelled")} for p in POLICIES},
                block_timely_counts=dict(sorted(timely.items())), forecast_minus_expiry_per_min=interval(paired, [1]*12))


def offline(rows, deadlines):
    by_episode = defaultdict(lambda: defaultdict(list))
    seen = set()
    for r in rows:
        b, i, steps, eos = (int(r[k]) for k in ("episode","offer","steps","eos"))
        check(1 <= b <= 16 and 1 <= i <= 32 and (b,i) not in seen, "Forecast roster")
        seen.add((b,i))
        check(eos in (0,1) and 0 < steps <= 1024 and (eos or steps == 1024), "Invalid terminal step")
        if steps <= 32:
            check(not r["elapsed_after_32_ns"] and all(not r[f"{m}_d{j}"] for m in METHODS for j in (1,2,3)),
                  "Ineligible request has landmark forecast")
            continue
        elapsed = int(r["elapsed_after_32_ns"])
        check(elapsed >= 0, "Negative remaining time")
        y = [int(eos and elapsed <= d) for d in deadlines]
        for m in METHODS:
            p = [float(r[f"{m}_d{j}"]) for j in (1,2,3)]
            check(all(0 <= x <= 1 for x in p) and p == sorted(p), "Invalid deadline probabilities")
            by_episode[b][m].append(math.fsum((a-v)**2 for a,v in zip(p,y))/3)
    check(len(seen) == 512 and len(by_episode) == 16, "Incomplete forecast cohort")
    ordered = [by_episode[b] for b in sorted(by_episode)]
    counts = [len(x["hazard"]) for x in ordered]
    def contrast(m):
        numerators = [math.fsum(a-b for a,b in zip(x[m],x["hazard"])) for x in ordered]
        return interval(numerators, counts)
    return dict(assigned=len(seen), active=sum(counts), active_by_episode=counts,
                brier={m:math.fsum(math.fsum(x[m]) for x in ordered)/sum(counts) for m in METHODS},
                aft_minus_hazard=contrast("aft"), direct_scale_minus_hazard=contrast("direct_scale"))


def sensitivity(rows, baseline):
    rows = sorted(rows, key=lambda r: int(r["episode"]))
    counts = [int(r["eligible"]) for r in rows]
    check([int(r["episode"]) for r in rows] == list(range(1,17)) and
          counts == baseline["active_by_episode"], "Refit and original test populations differ")
    result = {}
    for m in METHODS:
        masked = [float(r[f"masked_{m}_loss_sum"]) for r in rows]
        full = [float(r[f"full_source_{m}_loss_sum"]) for r in rows]
        check(all(0 <= a <= n and 0 <= b <= n for a,b,n in zip(masked,full,counts)), "Invalid episode losses")
        check(math.isclose(sum(masked)/sum(counts), baseline["brier"][m], abs_tol=1e-12),
              "Masked refit differs from frozen primary mean")
        result[m] = dict(masked_brier=sum(masked)/sum(counts), full_brier=sum(full)/sum(counts),
                         paired_change=interval([b-a for a,b in zip(masked,full)],counts))
    for comparator in ("aft","direct_scale"):
        def differences(regime):
            return [float(r[f"{regime}_{comparator}_loss_sum"])-float(r[f"{regime}_hazard_loss_sum"]) for r in rows]
        masked, full = differences("masked"), differences("full_source")
        delta = [b-a for a,b in zip(masked,full)]
        check(all(math.isclose(d, float(r[f"change_{comparator}_minus_hazard_loss_sum"]), abs_tol=1e-12)
                  for r,d in zip(rows,delta)), "Unmatched episode contrast")
        result[comparator+"_minus_hazard"] = dict(masked=interval(masked,counts), full=interval(full,counts),
                                                  paired_change=interval(delta,counts))
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("directory", nargs="?", type=Path, default=Path(__file__).resolve().parent)
    root = ap.parse_args().directory
    index = json.loads((root/"source_index.json").read_text(encoding="utf-8"), parse_float=Decimal)
    deadlines = [d*10**9 for d in index["deadlines_seconds"]]
    check(len(deadlines) == 3 and deadlines == sorted(deadlines), "Invalid deadline grid")
    frozen = offline(table(root/"deadline_forecasts.csv"), deadlines)
    print(json.dumps(dict(deadline_forecasts=frozen, live_delivery=live(table(root/"live_delivery.csv")),
                          training_sensitivity=sensitivity(table(root/"paired_episode_brier.csv"),frozen)), indent=2))


if __name__ == "__main__":
    main()

"""Reconstruct the article's cross-family comparison from request-level numeric tables."""
import csv
import hashlib
import json
import math
import statistics
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
# Two-sided Student critical values for 95% intervals, df 11..23.
T95 = {11: 2.200985160082949, 12: 2.178812829663418, 13: 2.160368656461013,
       14: 2.144786687916928, 15: 2.131449545559323, 16: 2.119905299221011,
       17: 2.109815577833181, 18: 2.100922040240960, 19: 2.093024054408263,
       20: 2.085963447265837, 21: 2.079613844727662, 22: 2.073873067904015,
       23: 2.068657610419041}


def interval(group_sums, group_counts):
    total = sum(group_counts)
    if not total:
        return None
    effect = math.fsum(group_sums) / total
    count = len(group_counts)
    influences = [(x - effect * n) * count / total for x, n in zip(group_sums, group_counts)]
    width = T95[count - 1] * statistics.stdev(influences) / math.sqrt(count)
    return [effect, effect - width, effect + width]


def read_family(spec):
    path = HERE / spec['csv']
    if hashlib.sha256(path.read_bytes()).hexdigest() != spec['csv_sha256']:
        raise ValueError(f'Input differs from manifest: {path.name}')
    with path.open(newline='') as stream:
        rows = [row for row in csv.DictReader(stream) if row.get('regime', 'contention') == 'contention']
    if len(rows) != spec['assigned']:
        raise ValueError(f'Wrong number of attempts for {spec["name"]}')
    secs = [Decimal(str(d)) for d in spec['deadlines_seconds']]
    if len(set(secs)) != 3 or sorted(secs) != secs or secs[0] <= 0:
        raise ValueError('Invalid source deadline grid')
    bins = defaultdict(list)
    for row in rows:
        bins[int(row['episode'])].append(row)
    if len(bins) != spec['episodes']:
        raise ValueError('Unexpected episode count')
    sums, counts, direct, hazard = [], [], [], []
    events, eligible, matched = [0, 0, 0], 0, 0
    discord = [0, 0, 0]
    pred_direct, pred_hazard = [[], [], []], [[], [], []]
    for episode in sorted(bins):
        diffs = []
        for row in bins[episode]:
            steps, eos = int(row['steps']), int(row['eos'])
            if not 0 < steps <= 1024 or (not eos and steps != 1024):
                raise ValueError('Invalid natural or horizon stop')
            if steps <= 32:
                continue
            eligible += 1
            elapsed = int(row['elapsed_after_32_ns'])
            if elapsed <= 0:
                raise ValueError('Invalid post-landmark clock')
            labels = [int(eos == 1 and elapsed <= d * 10**9) for d in secs]
            events = [n + y for n, y in zip(events, labels)]
            if row.get('direct_scale_supported', '1') != '1':
                continue
            p = [float(row[f'direct_scale_d{j}']) for j in (1, 2, 3)]
            q = [float(row[f'hazard_d{j}']) for j in (1, 2, 3)]
            for v in [p, q]:
                if not all(math.isfinite(x) and 0 <= x <= 1 for x in v) or v != sorted(v):
                    raise ValueError('Probabilities outside monotone CDF')
            matched += 1
            a = statistics.mean((x - y) ** 2 for x, y in zip(p, labels))
            b = statistics.mean((x - y) ** 2 for x, y in zip(q, labels))
            direct.append(a)
            hazard.append(b)
            diffs.append(a - b)
            for j in range(3):
                discord[j] += abs(p[j] - q[j]) > 0.10
                pred_direct[j].append(p[j])
                pred_hazard[j].append(q[j])
        sums.append(math.fsum(diffs))
        counts.append(len(diffs))
    est = interval(sums, counts)
    support = matched == eligible and matched > 0
    return dict(name=spec['name'], episodes=len(bins), assigned=len(rows), eligible=eligible,
                matched=matched, deadlines_seconds=spec['deadlines_seconds'], observed_events=events,
                direct_common_brier=statistics.mean(direct) if direct else None,
                hazard_common_brier=statistics.mean(hazard) if hazard else None,
                common_delta_interval=est, full_support=support,
                complete_cohort_delta_interval=est if support else None,
                probabilities_over_10pp_apart=discord,
                mean_direct_on_common=[statistics.mean(x) if x else None for x in pred_direct],
                mean_hazard_on_common=[statistics.mean(x) if x else None for x in pred_hazard])


def main():
    manifest = json.loads((HERE / 'families.json').read_text())
    out = {slug: read_family(spec) for slug, spec in manifest.items()}
    print(json.dumps(out, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()

"""Rebuild the two minimized evidence tables from the identified, private study ZIPs."""
import argparse
import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from zipfile import ZipFile


LIVE_HASH = "7ef956ffedc9fada1ecf32c8f00295d0d3b36d46077e7e8e296c101fa2f409b6"
METAL_HASH = "0dcb3e8481600cfca0ad7c419e5a070eb56103bbd25212e6e0b49bdc42b76644"
METHODS = ("aft", "hazard", "direct_scale")
LIVE_ROOT = "execution/20261121/live_revision"


def check(ok, reason):
    if not ok:
        raise ValueError(reason)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def records(archive, name, manifest):
    data = archive.read(name)
    manifest[name] = digest(data)
    if name.endswith(".jsonl"):
        return (json.loads(line) for line in data.splitlines())
    return json.loads(data)


def csv_out(path, rows):
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0], lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def client_data(archive, manifest):
    plan = records(archive, LIVE_ROOT + "/manifest.json", manifest)
    lock = records(archive, LIVE_ROOT + "/lock.json", manifest)
    check(lock["inputs"]["files"][LIVE_ROOT + "/manifest.json"] == manifest[LIVE_ROOT + "/manifest.json"],
          "Arrival schedule differs from lock")
    check(plan["deadline_s"] == 1 and plan["window_s"] == 14, "Unexpected endpoint")
    out = []
    families = set()
    for b, block in enumerate(plan["blocks"]):
        for order, policy in enumerate(block["order"], 1):
            prefix = f"{LIVE_ROOT}/blocks/{block['block']}/attempt-000/{policy}/"
            ledger = records(archive, prefix + "client_ledger.json", manifest)
            arm = records(archive, prefix + "arm.json", manifest)
            native = defaultdict(list)
            for e in records(archive, prefix + "events.jsonl", manifest):
                if e["slot_id"] == 0:
                    native[e["task_id"]].append(e)
            ordered = sorted(native, key=lambda t: native[t][0]["server_ready_ns"])
            check(len(ordered) == 14 and len(block["jobs"]) == len(ledger["jobs"]) == len(arm["jobs"]) == 12,
                  "Invalid native or client roster")
            origin = ledger["origin_ns"]
            for i, (job, report, assigned) in enumerate(zip(ledger["jobs"], arm["jobs"], block["jobs"])):
                check(job["request_id"] == report["request_id"] == assigned["request_id"]
                      and job["group_id"] == assigned["group_id"] and report["native_task_id"] == ordered[i+1],
                      "Incorrect request assignment")
                families.add(assigned["group_id"])
                events = native[ordered[i+1]]
                tokens = [e for e in events if e["event_type"] == "token"]
                finish = [e for e in events if e["event_type"] == "request_finish"]
                check(len(finish) <= 1 and len(tokens) == report["native_steps"]
                      and events[0]["event_type"] == "request_admission" and tokens
                      and [t["step_index"] for t in tokens] == list(range(1, len(tokens)+1)), "Native sequence")
                native_end = "cancelled"
                if finish:
                    check(finish[0]["is_terminal"] == tokens[-1]["is_terminal"], "Native terminal flag")
                    native_end = "eos" if finish[0]["is_terminal"] else "limit"
                check(native_end == report["native_end"] == job["status"], "Native and client outcomes differ")
                arrival = job["arrival_ns"] - origin
                stop = job["client_sse_stop_ns"]
                cancel = job["cancel_sent_ns"]
                check(arrival == round(assigned["arrival_s"] * 10**9)
                      and job["deadline_ns"] == job["arrival_ns"] + 10**9, "Deadline differs from schedule")
                out.append(dict(block=b+1, offer=i+1, policy=policy, period=order,
                                arrival_ns=arrival, deadline_ns=job["deadline_ns"]-origin,
                                dispatch_ns=job["dispatch_ns"]-origin, end=native_end,
                                final_frame_ns=None if stop is None else stop-origin,
                                cancel_ns=None if cancel is None else cancel-origin,
                                native_steps=len(tokens)))
    check(len(families) == 144, "Unexpected live family count")
    return out


def forecast_data(archive, manifest):
    model = records(archive, "models.json", manifest)
    out = []
    for b in range(16):
        episode = f"confirmation-{b:03}"
        attempt = records(archive, episode + "/accepted.json", manifest)["attempt"]
        prefix = f"{episode}/{attempt}/"
        schedule = records(archive, prefix + "schedule.json", manifest)
        selected = [x for x in schedule if x["regime"] == "contention" and x["split"] == "pilot_test"]
        target = {x["request_id"] for x in selected}
        progress = records(archive, prefix + "progress.jsonl", manifest)
        lookup = {x["task_id"]: x["request_id"] for x in progress if x["request_id"] in target and x["status"] == "complete"}
        check(len(lookup) == len(target) == 32 and set(lookup.values()) == target, "Native roster mismatch")
        pred = {x["request_id"]: x for x in records(archive, episode + "/forecasts.json", manifest)["rows"]}
        observations = defaultdict(dict)
        for e in records(archive, prefix + "events.jsonl", manifest):
            if e["slot_id"] != 0 or e["task_id"] not in lookup:
                continue
            sample = observations[lookup[e["task_id"]]]
            kind = e["event_type"]
            if kind == "token" and e["step_index"] == 32:
                check(e["clock_domain"] == "server_steady_clock", "Unexpected landmark clock")
                sample["t32"] = e["server_ready_ns"]
            if kind == "request_finish":
                check("finish" not in sample, "Duplicate finish")
                sample["finish"] = e
        for i, assignment in enumerate(selected, 1):
            rid = assignment["request_id"]
            sample, prediction = observations[rid], pred[rid]
            finish = sample["finish"]
            n = finish["decode_steps"]
            active = n > 32
            check(active == prediction["eligible"] and 0 < n <= 1024
                  and (finish["is_terminal"] or n == 1024), "Eligibility or cap mismatch")
            p = {x["method"]: x["probability"] for x in prediction["forecasts"] if x["budget"] == 5} if active else {}
            if active:
                check(sample["t32"] == prediction["checkpoint_ns"], "Sealed forecast uses another landmark")
            row = dict(episode=b+1, offer=i, steps=n, eos=int(finish["is_terminal"]),
                       elapsed_after_32_ns=finish["server_ready_ns"]-sample["t32"] if active else None)
            for method in METHODS:
                for j in range(len(model["grid"])):
                    row[f"{method}_d{j+1}"] = p[method][j] if active else None
            out.append(row)
    return out, model["grid"]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("live_zip", type=Path)
    ap.add_argument("metal_zip", type=Path)
    ap.add_argument("output", type=Path, help="new output directory")
    args = ap.parse_args()
    live_manifest, metal_manifest = {}, {}
    for path, expected in ((args.live_zip, LIVE_HASH), (args.metal_zip, METAL_HASH)):
        check(digest(path.read_bytes()) == expected, "Unexpected archive fingerprint: " + path.name)
    with ZipFile(args.live_zip) as archive:
        jobs = client_data(archive, live_manifest)
    with ZipFile(args.metal_zip) as archive:
        forecasts, deadlines = forecast_data(archive, metal_manifest)
    args.output.mkdir(parents=True, exist_ok=False)
    csv_out(args.output / "live_delivery.csv", jobs)
    csv_out(args.output / "deadline_forecasts.csv", forecasts)
    metadata = dict(live_zip_sha256=LIVE_HASH, metal_zip_sha256=METAL_HASH, deadlines_seconds=deadlines,
                    live_source_members=live_manifest, metal_source_members=metal_manifest)
    (args.output / "source_index.json").write_text(json.dumps(metadata, sort_keys=True, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

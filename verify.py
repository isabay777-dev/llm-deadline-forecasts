"""Verify the closed AI2 author package and replay selected analyses on CPU."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from zipfile import BadZipFile, ZipFile


LOCK = Path(__file__).with_name("revision_1.json")
NATIVE_SCRIPT = "reproduction/reproduce_study.py"
NATIVE_ARCHIVE = "reproduction/metal_confirmation.zip"
LIVE_SCRIPT = "reproduction/live_policy_artifact.py"
LIVE_ARCHIVE = "reproduction/live_policy_v2.zip"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(stream):
    result = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        result.update(block)
    return result.hexdigest()


def check_contents(package, lock):
    names = package.namelist()
    raw = package.read("AUTHOR_SHA256.json")
    require(hashlib.sha256(raw).hexdigest() == lock["manifest_sha256"], "manifest digest mismatch")
    manifest = json.loads(raw)
    declared = manifest["files"]
    require(len(names) == len(set(names)), "duplicate member")
    require(len(declared) == lock["payload_files"], "unexpected payload count")
    require(set(names) == set(declared) | {"AUTHOR_SHA256.json"}, "unexpected payload membership")
    require(manifest["primary_manuscript"] == lock["manuscript"], "unexpected manuscript")
    for name, pin in lock["members"].items():
        require(declared[name] == pin, f"pinned member mismatch for {name}")
    for name, expected in declared.items():
        require(package.getinfo(name).file_size == expected["bytes"], f"member size mismatch for {name}")
        with package.open(name) as stream:
            require(digest(stream) == expected["sha256"], f"member digest mismatch for {name}")


def replay(package, lock, live, scratch):
    members = (LIVE_SCRIPT, LIVE_ARCHIVE) if live else (NATIVE_SCRIPT, NATIVE_ARCHIVE)
    script, archive = [scratch / Path(name).name for name in members]
    for name, target in zip(members, (script, archive)):
        with package.open(name) as source, target.open("xb") as output:
            shutil.copyfileobj(source, output)
        with target.open("rb") as source:
            require(digest(source) == lock["members"][name]["sha256"], f"replay input mismatch for {name}")
    receipt = scratch / "receipt.json"
    command = [sys.executable, "-I", "-B", str(script), "verify", str(archive), "--receipt", str(receipt)]
    if live:
        command.extend(["--out", str(scratch / "recalculated")])
    subprocess.run(command, cwd=scratch, check=True, stdout=subprocess.DEVNULL)
    verified = json.loads(receipt.read_text(encoding="utf-8"))
    require(verified["archive_sha256"] == lock["members"][members[1]]["sha256"], "replay digest mismatch")
    if live:
        return live_results(lock["live_v2"], verified, scratch)
    return native_results(lock["confirmatory"], verified)


def native_results(expected, verified):
    require(verified["all_aggregate_fields_agree"] and verified["scientific_decision_90_agrees"],
            "inconsistent confirmatory results")
    for key in ("episodes", "foreground_requests", "dual_advantage"):
        require(verified[key] == expected[key], f"unexpected {key}")
    contrasts = verified["cells"]["contention_m5"]["contrasts"]
    primary = {}
    for method, reference in expected["primary_comparisons"].items():
        brier = contrasts[method]["brier"]
        for key in ("estimate", "lower", "upper"):
            require(math.isclose(brier[key], reference[key], rel_tol=0, abs_tol=1e-10),
                    f"unexpected {method} {key}")
        require(brier["denominator"] == reference["denominator"], f"unexpected denominator for {method}")
        primary[method] = {key: brier[key] for key in reference}
    return {"episodes": verified["episodes"], "foreground_requests": verified["foreground_requests"],
            "primary_comparisons": primary, "dual_advantage": verified["dual_advantage"]}


def live_results(expected, verified, scratch):
    require(verified["verified"] and verified["score_reference"] == "supplied", "unverified live scores")
    for key in ("blocks", "arms", "score_sha256"):
        require(verified[key] == expected[key], f"unexpected live {key}")
    summary = json.loads((scratch / "recalculated" / "summary.json").read_text(encoding="utf-8"))
    require(summary["primary_contrast"] == "forecast_minus_expiry", "unexpected live contrast")
    policies = summary["policies"]
    offers = {policy: scores["offers"] for policy, scores in policies.items()}
    timely = {policy: scores["timely"] for policy, scores in policies.items()}
    require(all(n == expected["offers_per_policy"] for n in offers.values()), "live denominator mismatch")
    require(timely == expected["timely"], "timely counts mismatch")
    supported = summary["primary_directional_gain_supported"]
    require(supported == expected["primary_directional_gain_supported"], "unexpected live decision")
    return {"blocks": verified["blocks"], "arms": verified["arms"], "offers": offers, "timely": timely,
            "forecast_minus_expiry_per_window": summary["contrasts"]["forecast_minus_expiry"]["timely_count_difference"],
            "primary_directional_gain_supported": supported, "score_sha256": verified["score_sha256"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("integrity", "replay", "live"))
    parser.add_argument("archive", type=Path, help="closed author ZIP supplied separately")
    parser.add_argument("--receipt", type=Path, help="also write the portable JSON result to a new file")
    args = parser.parse_args()
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    if args.receipt and args.receipt.exists():
        parser.error("receipt already exists")
    with args.archive.open("rb") as stream:
        require(stream.seek(0, 2) == lock["archive_bytes"], "outer archive size mismatch")
        stream.seek(0)
        require(digest(stream) == lock["archive_sha256"], "outer archive SHA-256 mismatch")
        with ZipFile(stream) as package:
            check_contents(package, lock)
            result = {"revision": lock["revision"], "mode": args.mode, "archive_sha256": lock["archive_sha256"],
                      "checked_payload_files": lock["payload_files"], "manuscript_sha256": lock["members"][lock["manuscript"]]["sha256"]}
            if args.mode != "integrity":
                with TemporaryDirectory(prefix="ai2-reader-") as directory:
                    result["recalculated"] = replay(package, lock, args.mode == "live", Path(directory))
    rendered = json.dumps(dict(result, verified=True), indent=2, allow_nan=False) + "\n"
    if args.receipt:
        with args.receipt.open("x", encoding="utf-8") as target:
            target.write(rendered)
    print(rendered, end="")


if __name__ == "__main__":
    try:
        main()
    except (OSError, KeyError, ValueError, BadZipFile, subprocess.CalledProcessError) as error:
        sys.exit(f"Verification failed: {error}")

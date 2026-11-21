"""Exercise the reader with the supplied archive and a same-length, one-bit change."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory


def check(archive):
    program = str(Path(__file__).with_name("verify.py").resolve())
    with TemporaryDirectory(prefix="ai2-reader-tests-") as temporary:
        root = Path(temporary)
        reports = {}
        for mode in ("integrity", "replay", "live"):
            receipt = root / (mode + ".json")
            run = subprocess.run(
                [sys.executable, program, mode, str(archive), "--receipt", str(receipt)],
                capture_output=True, text=True, check=True)
            recorded = json.loads(receipt.read_text(encoding="utf-8"))
            if not recorded["verified"] or recorded != json.loads(run.stdout):
                raise RuntimeError(f"{mode} did not produce matching results")
            reports[mode] = recorded

        changed = root / "changed.zip"
        shutil.copyfile(archive, changed)
        with changed.open("r+b") as stream:
            stream.seek(4096)
            original = stream.read(1)
            stream.seek(4096)
            stream.write(bytes([original[0] ^ 1]))
        missing_receipt = root / "changed.json"
        bad_run = subprocess.run(
            [sys.executable, program, "replay", str(changed), "--receipt", str(missing_receipt)],
            capture_output=True, text=True)
        if bad_run.returncode == 0 or "outer archive SHA-256 mismatch" not in bad_run.stderr or missing_receipt.exists():
            raise RuntimeError("same-size changed archive was not rejected before replay")
        return {"pass": True, "one_bit_change_rejected_before_replay": True, "checks": reports}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    options = parser.parse_args()
    print(json.dumps(check(options.archive), indent=2, allow_nan=False))

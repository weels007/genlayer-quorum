"""Check that the address on file is the contract in this checkout.

    python scripts/verify_deployment.py

The deploy script records the first twelve characters of the SHA-256 of
contracts/Quorum.py next to the address, because a register with no owner cannot
be upgraded: a submission has to point at the source it was reviewed as. This
check is what makes that claim testable — if the number in deployment.json stops
matching the contract, the recorded address runs code that is no longer here.

No network and no wallet: it reads two files.
"""

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CONTRACT_PATH = ROOT / "contracts" / "Quorum.py"
DEPLOYMENT_PATH = ROOT / "deployment.json"

ADDRESS_VALUE = re.compile(r"0x[0-9a-fA-F]{40}")
REVISION_CHARS = 12


def main() -> int:
    if not DEPLOYMENT_PATH.exists():
        print("[verify] no deployment.json yet; there is nothing recorded to check")
        return 0

    payload = json.loads(DEPLOYMENT_PATH.read_text(encoding="utf-8"))
    address = str(payload.get("address", "")).strip()
    revision = str(payload.get("revision", "")).strip()

    if not ADDRESS_VALUE.fullmatch(address):
        print(f"[verify] deployment.json has no usable address: {address!r}")
        return 1

    code = CONTRACT_PATH.read_text(encoding="utf-8")
    expected = hashlib.sha256(code.encode("utf-8")).hexdigest()[:REVISION_CHARS]
    if revision != expected:
        print(
            "[verify] deployment.json records revision "
            f"{revision!r}, but contracts/Quorum.py is {expected!r}."
        )
        print(
            "[verify] the address is not this source: redeploy before submitting "
            "(Deploy Contract, force)."
        )
        return 1

    print(
        f"[verify] {address} is revision {expected}, which is contracts/Quorum.py "
        "in this checkout"
    )
    print(f"[verify] explorer: {payload.get('explorer', '')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

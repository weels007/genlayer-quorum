"""Deploy Quorum once and record the address.

    python scripts/deploy.py            # deploy only when the source changed
    python scripts/deploy.py --force    # redeploy and replace the address

The contract has no constructor arguments and no owner, so a new deployment is a
new, empty register of grants. The address is recorded in deployment.json.
"""

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path

from genlayer_py import create_client, studionet

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from wallet import load_account  # noqa: E402

CONTRACT_PATH = ROOT / "contracts" / "Quorum.py"
DEPLOYMENT_PATH = ROOT / "deployment.json"

POLL_INTERVAL = 10_000
POLL_RETRIES = 60

DEPLOY_ATTEMPTS = 3
RETRY_PAUSE = 20

ADDRESS_VALUE = re.compile(r"0x[0-9a-fA-F]{40}")

# The bounds the contract enforces. A deployment is only recorded when the
# address answers with exactly these, so a wrong contract is never written down
# as the one to test against.
MIN_DEPOSIT = 200
MIN_THRESHOLD = 2
MAX_ATTEMPTS = 3
# The bounds the reading itself relies on: at most four sources are read, and a
# quote shorter than twenty letters cannot ground in any of them. An address that
# disagrees on either is not this contract.
MAX_SOURCES = 4
MIN_QUOTE_CHARS = 20


def recorded() -> dict:
    if not DEPLOYMENT_PATH.exists():
        return {}
    try:
        payload = json.loads(DEPLOYMENT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def write_deployment(address: str, revision: str) -> None:
    DEPLOYMENT_PATH.write_text(
        json.dumps(
            {
                "contract": "Quorum",
                "network": "GenLayer Studio (61999)",
                "address": address,
                "revision": revision,
                "explorer": f"https://explorer-studio.genlayer.com/address/{address}",
                "min_deposit": MIN_DEPOSIT,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def find_address(receipt) -> str:
    if not isinstance(receipt, dict):
        return ""
    candidates = [receipt.get("to_address"), receipt.get("recipient")]
    for key in ("data", "tx_data_decoded"):
        block = receipt.get(key)
        if isinstance(block, dict):
            candidates.append(block.get("contract_address"))
            candidates.append(block.get("to_address"))
    for value in candidates:
        if isinstance(value, str) and ADDRESS_VALUE.fullmatch(value):
            return value
    return ""


def execution_succeeded(receipt) -> bool:
    if not isinstance(receipt, dict):
        return False
    if receipt.get("status_name") not in ("ACCEPTED", "FINALIZED"):
        return False
    leader = (receipt.get("consensus_data") or {}).get("leader_receipt")
    if isinstance(leader, list):
        leader = leader[0] if leader else None
    if isinstance(leader, dict):
        return leader.get("execution_result") == "SUCCESS"
    return str(receipt.get("tx_execution_result_name", "")) == "FINISHED_WITH_RETURN"


def report_funds(client, account) -> None:
    """Say what the sender holds and what the network asks for a transaction.

    Both numbers are printed before the send so a rejected deployment is
    diagnosable from the log alone: a revert at the EVM step that carries the
    deployment happens before any contract code runs.
    """
    try:
        held = client.w3.eth.get_balance(account.address)
    except Exception as error:  # noqa: BLE001 - a diagnostic must not stop a deploy
        held = f"unknown ({error})"
    try:
        asked = client.estimate_transaction_fees().get("feeValue")
    except Exception as error:  # noqa: BLE001 - a diagnostic must not stop a deploy
        asked = f"unknown ({error})"
    print(f"[deploy] {account.address} holds {held} wei; the network asks {asked} wei")


def inflated_fees(client, factor: int):
    """The quoted fee distribution, with the deposit multiplied.

    The quote is built from the validator allocation and the execution budget; a
    deployment also writes its own source into storage, so a contract that has
    grown can sit above the quote. Raising only the deposit keeps the same
    distribution and the same transaction.
    """
    try:
        estimate = client.estimate_transaction_fees()
    except Exception as error:  # noqa: BLE001 - fall back to the default quote
        print(f"[deploy] the fee could not be quoted again: {error}")
        return None
    distribution = estimate.get("distribution")
    fee_value = estimate.get("feeValue")
    if not distribution or not fee_value:
        return None
    return {"distribution": distribution, "feeValue": int(fee_value) * factor}


def send_deploy(client, code: str, account) -> str:
    """Send the deployment, paying a larger fee deposit after a revert.

    A revert at the EVM step that carries the deployment happens before any
    contract code runs, so it says the transaction was not accepted, not that the
    contract is wrong. The first send uses the quoted fee; later sends multiply the
    deposit, because the quote is the one number this side can get wrong.
    """
    for attempt in range(DEPLOY_ATTEMPTS):
        fees = None if attempt == 0 else inflated_fees(client, 2**attempt)
        if fees:
            print(f"[deploy] sending again with a fee deposit of {fees['feeValue']} wei")
        try:
            return client.deploy_contract(code=code, args=[], account=account, fees=fees)
        except Exception as error:
            if "reverted" not in str(error).lower() or attempt == DEPLOY_ATTEMPTS - 1:
                raise SystemExit(str(error))
            print(f"[deploy] the network reverted the send, trying again: {error}")
            time.sleep(RETRY_PAUSE)
    raise SystemExit("the deployment was rejected")


def deploy_once(code: str, revision: str):
    """Deploy from the first configured wallet that can send, deployer first."""
    rejected = []
    for name in ("tester1", "tester2"):
        try:
            account = load_account(name)
        except SystemExit as error:
            print(f"[deploy] no {name} wallet here: {error}")
            continue

        client = create_client(studionet, account=account)
        print(
            f"[deploy] deploying Quorum from {account.address} "
            f"({name}, revision {revision})"
        )
        report_funds(client, account)

        try:
            tx_hash = send_deploy(client, code, account)
        except SystemExit as error:
            print(f"[deploy] {name} could not send the deployment: {error}")
            rejected.append(f"{name} ({account.address}): {error}")
            continue

        print(f"[deploy] transaction {tx_hash}")
        receipt = client.wait_for_transaction_receipt(
            tx_hash, retries=POLL_RETRIES, interval=POLL_INTERVAL
        )
        if not execution_succeeded(receipt):
            raise SystemExit(f"[deploy] the deployment did not succeed: {receipt}")

        address = find_address(receipt)
        if not address:
            raise SystemExit(f"[deploy] no contract address in the receipt: {receipt}")
        return client, address

    raise SystemExit(
        "[deploy] no configured wallet could send the deployment:\n  "
        + "\n  ".join(rejected)
        + "\n[deploy] Studio reverts a transaction it will not accept before any "
        "contract code runs, so this is about the sender rather than the source: a "
        "wallet without enough test GEN is the usual reason, and the Studio faucet "
        "is where to top it up. Then run this workflow again."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force", action="store_true", help="redeploy even when this revision is recorded"
    )
    args = parser.parse_args()

    code = CONTRACT_PATH.read_text(encoding="utf-8")
    revision = hashlib.sha256(code.encode("utf-8")).hexdigest()[:12]
    previous = recorded()

    if not args.force and previous.get("address") and previous.get("revision") == revision:
        print(
            f"[deploy] deployment.json already points at {previous['address']} "
            f"at revision {revision}; reusing it"
        )
        return 0

    client, address = deploy_once(code, revision)

    try:
        rules = client.read_contract(address, "get_rules")
        summary = client.read_contract(address, "get_summary")
    except Exception as error:
        raise SystemExit(f"[deploy] the deployed contract does not answer: {error}")

    if int(rules.get("min_deposit", 0)) != MIN_DEPOSIT or int(
        rules.get("max_attempts", 0)
    ) != MAX_ATTEMPTS:
        raise SystemExit(f"[deploy] the contract at {address} has the wrong rules: {rules}")
    if int(rules.get("min_threshold", 0)) != MIN_THRESHOLD:
        raise SystemExit(
            f"[deploy] the contract at {address} does not require a quorum: {rules}"
        )
    if int(rules.get("max_sources", 0)) != MAX_SOURCES or int(
        rules.get("min_quote_chars", 0)
    ) != MIN_QUOTE_CHARS:
        raise SystemExit(
            f"[deploy] the contract at {address} does not read the sources this pack "
            f"describes: {rules}"
        )
    if int(summary.get("grants", -1)) != 0:
        raise SystemExit(f"[deploy] the register at {address} is not empty: {summary}")

    write_deployment(address, revision)
    print(f"[deploy] Quorum is at {address}")
    print(f"[deploy] explorer: https://explorer-studio.genlayer.com/address/{address}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

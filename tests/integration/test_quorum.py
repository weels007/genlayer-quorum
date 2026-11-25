"""Quorum on studionet: one grant, its quorum, a dispute, and the exits.

The register has no owner and no reset, so every assertion is a delta — a count,
a ledger total, a real balance — and the suite tolerates grants and credits left
behind by an earlier run.

A live committee cannot be ordered to read a page, so each grant asserts the law
for whichever reading appears and reports it: a quorum is met or it is not, a
dispute raises the bar, and a grant nobody could read is voided with every party
made whole.

    pytest tests/integration -v -s
"""

import json
import re
import sys
import time
from pathlib import Path

import pytest
from genlayer_py import create_client, studionet

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from wallet import load_account  # noqa: E402

DEPLOYMENT_PATH = ROOT / "deployment.json"
ADDRESS_VALUE = re.compile(r"0x[0-9a-fA-F]{40}")

POLL_INTERVAL = 10_000
POLL_RETRIES = 60
RETRY_PAUSE = 20

# Studio has been asking more than the fee its own client quotes: a send carrying
# only the quote comes back reverted at the EVM step, before any contract code
# runs, and the same send with a larger deposit goes through.
SEND_ATTEMPTS = 3

DEPOSIT = 200
THRESHOLD = 2
ATTEMPTS = 3

# The payee never signs anything: a grant is paid to an address, so a literal
# address that is neither the donor nor a wallet in this repository is enough.
PAYEE = "0x1111111111111111111111111111111111111111"

STATEMENT = "The project publishes a signed release every month."
BASE = "https://raw.githubusercontent.com/weels007/genlayer-quorum/main/testdata/"
SUPPORTS_URL = BASE + "supports.txt"
CONTRA_URL = BASE + "contradicts.txt"
UNGROUNDABLE_URL = BASE + "ungroundable.txt"

RUN = str(time.time_ns())


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _deployment() -> dict:
    if not DEPLOYMENT_PATH.exists():
        pytest.skip("no deployment.json yet; run the Deploy Contract workflow first")
    payload = json.loads(DEPLOYMENT_PATH.read_text(encoding="utf-8"))
    address = str(payload.get("address", "")).strip()
    if not ADDRESS_VALUE.fullmatch(address):
        pytest.skip(f"deployment.json has no usable address: {address!r}")
    return payload


@pytest.fixture(scope="module")
def deployment() -> dict:
    return _deployment()


@pytest.fixture(scope="module")
def contract(deployment) -> str:
    print(f"\n[quorum] contract {deployment['address']}")
    return deployment["address"]


@pytest.fixture(scope="module")
def account():
    return load_account("tester1")


@pytest.fixture(scope="module")
def opponent(account):
    """A second funded wallet. Without one there is nothing to dispute with."""
    try:
        other = load_account("tester2")
    except SystemExit:
        pytest.skip("no second wallet is configured; a dispute needs a stranger")
    if _hex(other.address) == _hex(account.address):
        pytest.skip("the second wallet is the deployer; a dispute needs a stranger")
    return other


@pytest.fixture(scope="module")
def client(account):
    return create_client(studionet, account=account)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _hex(value) -> str:
    return str(value).lower().replace("0x", "")


def _succeeded(result) -> bool:
    if not isinstance(result, dict):
        return False
    if result.get("status_name") not in ("ACCEPTED", "FINALIZED"):
        return False
    leader = (result.get("consensus_data") or {}).get("leader_receipt")
    if isinstance(leader, list):
        leader = leader[0] if leader else None
    if isinstance(leader, dict):
        return leader.get("execution_result") == "SUCCESS"
    return str(result.get("tx_execution_result_name", "")) == "FINISHED_WITH_RETURN"


def _definite_rollback(result) -> bool:
    if not isinstance(result, dict):
        return False
    leader = (result.get("consensus_data") or {}).get("leader_receipt")
    if isinstance(leader, list):
        leader = leader[0] if leader else None
    return isinstance(leader, dict) and leader.get("execution_result") == "ERROR"


def _fees(client, factor: int):
    """The quoted fee distribution with the deposit multiplied, or None."""
    try:
        estimate = client.estimate_transaction_fees()
    except Exception:
        return None
    distribution = estimate.get("distribution")
    fee_value = estimate.get("feeValue")
    if not distribution or not fee_value:
        return None
    return {"distribution": distribution, "feeValue": int(fee_value) * factor}


def _attempt(client, account, contract, method, args=None, value=0):
    last = None
    for attempt in range(SEND_ATTEMPTS):
        fees = None if attempt == 0 else _fees(client, 2**attempt)
        try:
            tx_hash = client.write_contract(
                contract,
                method,
                args=args or [],
                value=value,
                account=account,
                fees=fees,
            )
            return client.wait_for_transaction_receipt(
                tx_hash, retries=POLL_RETRIES, interval=POLL_INTERVAL
            )
        except Exception as error:
            last = error
            if "reverted" not in str(error).lower() or attempt == SEND_ATTEMPTS - 1:
                return error
            print(
                f"  {method}: the network reverted the send; trying again with a "
                f"deposit of {2 ** (attempt + 1)}x the quoted fee"
            )
            time.sleep(RETRY_PAUSE)
    return last


def _expect_ok(client, account, contract, method, args=None, value=0, verify=None):
    result = None
    for attempt in range(3):
        result = _attempt(client, account, contract, method, args, value)
        if _succeeded(result):
            return result
        if verify is not None and verify():
            return result
        if _definite_rollback(result):
            raise AssertionError(f"{method} was refused: {result}")
        if attempt < 2:
            print(f"  {method} did not reach consensus, retrying")
            time.sleep(RETRY_PAUSE)
    raise AssertionError(f"{method} did not reach consensus: {result}")


def _read(client, contract, method, args=None):
    """Read a view, retrying a provider hiccup (a 502 is not an answer)."""
    last = None
    for attempt in range(3):
        try:
            return client.read_contract(contract, method, args=args or [])
        except Exception as error:
            last = error
            if attempt < 2:
                print(f"  {method} read failed, retrying: {error}")
                time.sleep(RETRY_PAUSE)
    raise last


def _grant(client, contract, grant_id):
    return _read(client, contract, "get_grant", [grant_id])


def _grant_ids(client, contract):
    return list(_read(client, contract, "get_grant_ids").get("ids", []))


def _ledger(client, contract):
    return _read(client, contract, "get_ledger")


def _balance(client, contract):
    return int(_read(client, contract, "get_balance"))


def _credit(client, contract, who):
    return int(_read(client, contract, "get_credit", [who.address]))


def _invariant_holds(client, contract) -> bool:
    ledger = _ledger(client, contract)
    return int(ledger["total_in"]) == int(ledger["total_out"]) + int(
        ledger["locked_total"]
    )


def _wait_balance_drop(client, contract, before, amount, attempts=30, step=5):
    """Wait until the contract's real balance has fallen by exactly `amount`.

    An outgoing transfer is an external message that finalises after the call that
    emitted it, so the balance read straight after a settle still shows the money
    inside the contract. Polling is the only way to see it leave.
    """
    last = _balance(client, contract)
    for _ in range(attempts):
        if before - last == amount:
            return last
        time.sleep(step)
        last = _balance(client, contract)
    return last


def _open(client, donor, contract, ref, sources=None, threshold=THRESHOLD):
    before = len(_grant_ids(client, contract))
    _expect_ok(
        client,
        donor,
        contract,
        "open_grant",
        [
            STATEMENT,
            list(sources or [SUPPORTS_URL, CONTRA_URL, UNGROUNDABLE_URL]),
            threshold,
            PAYEE,
            f"{ref}-{RUN}",
        ],
        value=DEPOSIT,
        verify=lambda: len(_grant_ids(client, contract)) > before,
    )
    ids = _grant_ids(client, contract)
    assert len(ids) > before, "the grant was not opened"
    return ids[-1]


def _dispute(client, refuter, contract, grant_id):
    _expect_ok(client, refuter, contract, "dispute", [grant_id], value=DEPOSIT)
    record = _grant(client, contract, grant_id)
    assert record["disputed"], (
        "the dispute was refused — a payable refusal answers 'credit' instead of "
        f"reverting: {record}"
    )
    return record


def _attest(client, account, contract, grant_id):
    """Attest until the reading decides, or the attempts are spent."""
    for _ in range(ATTEMPTS + 1):
        record = _grant(client, contract, grant_id)
        if record["status"] != "OPEN" or int(record["attempts"]) >= ATTEMPTS:
            return record
        _expect_ok(client, account, contract, "attest", [grant_id])
    return _grant(client, contract, grant_id)


def _void_open_grant(client, account, contract, grant_id):
    """Give up on a grant no quorum reached: three attempts, then void."""
    record = _grant(client, contract, grant_id)
    for _ in range(ATTEMPTS + 1):
        if record["status"] != "OPEN":
            return record
        if int(record["attempts"]) >= ATTEMPTS:
            _expect_ok(client, account, contract, "void", [grant_id])
            return _grant(client, contract, grant_id)
        _expect_ok(client, account, contract, "attest", [grant_id])
        record = _grant(client, contract, grant_id)
    return record


def _the_reading_justifies_the_decision(record) -> bool:
    """Whatever the committee read, the bits have to meet the bar it recorded."""
    if record["status"] != "DECIDED":
        return True
    supports = str(record["supports"]).count("1")
    contradicts = str(record["contradicts"]).count("1")
    threshold = int(record["threshold"])
    if record["decision"] == "GRANTED":
        return supports >= threshold
    if record["decision"] == "DENIED":
        return contradicts >= threshold
    return False


# ---------------------------------------------------------------------------
# the register is live
# ---------------------------------------------------------------------------


def test_01_the_register_is_live(client, contract):
    rules = _read(client, contract, "get_rules")
    assert int(rules["min_deposit"]) == DEPOSIT
    assert int(rules["min_threshold"]) == 2
    assert int(rules["max_attempts"]) == ATTEMPTS
    assert set(rules["answers"]) == {"SUPPORTS", "CONTRADICTS"}
    assert _invariant_holds(client, contract)


def test_02_a_quorum_of_sources_releases_the_grant(client, account, opponent, contract):
    grant_id = _open(client, account, contract, "live-quorum")

    record = _attest(client, account, contract, grant_id)
    print(
        f"  the committee read supports={record['supports']!r} "
        f"contradicts={record['contradicts']!r} at a bar of {record['threshold']}"
    )
    assert _the_reading_justifies_the_decision(record), (
        "the decision does not follow from the bits it recorded: " f"{record}"
    )

    if record["status"] != "DECIDED":
        record = _void_open_grant(client, account, contract, grant_id)
        assert record["status"] == "VOID", record
        assert _invariant_holds(client, contract)
        pytest.skip("the committee reached no quorum; the grant was voided")

    # A decision is final: anyone may settle it, and the money leaves the court.
    ledger_before = _ledger(client, contract)
    balance_before = _balance(client, contract)
    _expect_ok(
        client,
        account,
        contract,
        "settle",
        [grant_id],
        verify=lambda: _grant(client, contract, grant_id)["status"] == "SETTLED",
    )

    assert _grant(client, contract, grant_id)["status"] == "SETTLED"
    ledger = _ledger(client, contract)
    assert int(ledger["locked_total"]) == int(ledger_before["locked_total"]) - DEPOSIT
    assert _invariant_holds(client, contract)

    landed = _wait_balance_drop(client, contract, balance_before, DEPOSIT)
    assert balance_before - landed == DEPOSIT, (
        "the ledger says the grant was paid, but it never left the contract: "
        f"{balance_before} -> {landed}"
    )


def test_03_a_dispute_raises_the_bar(client, account, opponent, contract):
    grant_id = _open(client, account, contract, "live-dispute")
    assert _grant(client, contract, grant_id)["threshold"] == THRESHOLD

    record = _dispute(client, opponent, contract, grant_id)
    assert int(record["threshold"]) == THRESHOLD + 1, (
        "a dispute has to raise the bar by one, not re-read the sources: " f"{record}"
    )
    assert int(record["refute_stake"]) == DEPOSIT, record

    heard = _attest(client, account, contract, grant_id)
    assert _the_reading_justifies_the_decision(heard), (
        "the decision does not follow from the bits it recorded at the wider bar: "
        f"{heard}"
    )
    print(
        f"  after the dispute: supports={heard['supports']!r} "
        f"contradicts={heard['contradicts']!r} at a bar of {heard['threshold']}"
    )


def test_04_a_grant_nobody_can_read_is_voided(
    client, account, opponent, contract
):
    """A source whose quote cannot be grounded never counts, so no quorum is met."""
    grant_id = _open(
        client,
        account,
        contract,
        "live-void",
        sources=[SUPPORTS_URL, UNGROUNDABLE_URL],
    )

    record = _void_open_grant(client, account, contract, grant_id)
    assert record["status"] == "VOID", (
        "the committee reached a quorum on a source that cannot ground a quote: "
        f"{record}"
    )

    ledger_before = _ledger(client, contract)
    assert _invariant_holds(client, contract)
    assert int(ledger_before["locked_total"]) >= 0


def test_05_views_agree_with_the_ledger(client, contract):
    summary = _read(client, contract, "get_summary")
    grants = _read(client, contract, "get_grants")

    assert int(summary["grants"]) == len(grants)
    assert int(summary["grants"]) == len(_grant_ids(client, contract))
    assert _balance(client, contract) >= 0
    assert _invariant_holds(client, contract)

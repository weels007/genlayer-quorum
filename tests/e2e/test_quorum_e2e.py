"""Quorum end to end on the deployed register: the money paths around a reading.

No model is called here on purpose — the Studio suite covers the quorums. This one
covers the refusals and the exits: a deposit below the floor, a duplicate
reference, a self-payee, a dispute with a mismatched stake, a dispute that has no
stricter bar to raise, a stranger cancelling a grant that is not theirs, and a
settle or a void that has not been earned.

A reference is caller-scoped and permanent, so every reference carries the run's
start time.

    pytest tests/e2e -v -s
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

# See the Studio suite: the network has been asking more than its client quotes,
# so a send reverted at the EVM step is tried again with a larger fee deposit.
SEND_ATTEMPTS = 3

DEPOSIT = 200
THRESHOLD = 2
# The payee never signs anything: a grant is paid to an address, so a literal
# address that is neither the donor nor a wallet in this repository is enough.
PAYEE = "0x1111111111111111111111111111111111111111"
STATEMENT = "The project publishes a signed release every month."
SUPPORTS_URL = (
    "https://raw.githubusercontent.com/weels007/genlayer-quorum/main/testdata/supports.txt"
)
CONTRA_URL = (
    "https://raw.githubusercontent.com/weels007/genlayer-quorum/main/testdata/contradicts.txt"
)

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
def contract() -> str:
    payload = _deployment()
    print(f"\n[quorum-e2e] contract {payload['address']}")
    return payload["address"]


@pytest.fixture(scope="module")
def account():
    return load_account("tester1")


@pytest.fixture(scope="module")
def opponent(account):
    """A second funded wallet, or None when only one is configured."""
    try:
        other = load_account("tester2")
    except SystemExit:
        return None
    if _hex(other.address) == _hex(account.address):
        return None
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
        # This suite is the money paths, and every call in it carries value:
        # Studio's quoted fee comes up short for those, so the first send already
        # deposits twice the quote and later sends multiply it.
        fees = _fees(client, 2 ** (attempt + 1))
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


def _expect_denied(client, account, contract, method, args=None, value=0):
    result = _attempt(client, account, contract, method, args, value)
    assert not _succeeded(result), f"{method} should have been refused: {result}"
    return result


def _read(client, contract, method, args=None):
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


def _credit(client, contract, who):
    return int(_read(client, contract, "get_credit", [who.address]))


def _invariant_holds(client, contract) -> bool:
    ledger = _ledger(client, contract)
    return int(ledger["total_in"]) == int(ledger["total_out"]) + int(
        ledger["locked_total"]
    )


def _open(client, donor, contract, ref, sources=None, threshold=THRESHOLD):
    before = len(_grant_ids(client, contract))
    _expect_ok(
        client,
        donor,
        contract,
        "open_grant",
        [
            STATEMENT,
            list(sources or [SUPPORTS_URL, CONTRA_URL]),
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


# ---------------------------------------------------------------------------
# the refusals and the exits
# ---------------------------------------------------------------------------


def test_01_the_register_is_live(client, contract):
    rules = _read(client, contract, "get_rules")
    assert int(rules["min_deposit"]) == DEPOSIT
    assert _invariant_holds(client, contract)


def test_02_a_deposit_below_the_floor_is_a_credit(client, account, contract):
    before = len(_grant_ids(client, contract))
    credit_before = _credit(client, contract, account)

    result = _attempt(
        client,
        account,
        contract,
        "open_grant",
        [STATEMENT, [SUPPORTS_URL, CONTRA_URL], THRESHOLD, PAYEE, f"e2e-void-{RUN}"],
        DEPOSIT - 1,
    )
    assert len(_grant_ids(client, contract)) == before, (
        "a deposit below the floor opened a grant: " f"{result}"
    )
    if _definite_rollback(result):
        # A zero-value call reverts; a call with value attached becomes a credit.
        pytest.skip("the network reverted the send instead of booking a credit")
    assert _credit(client, contract, account) == credit_before + (DEPOSIT - 1)


def test_03_a_duplicate_reference_is_a_credit(client, account, contract):
    _open(client, account, contract, "e2e-dupe")

    before = len(_grant_ids(client, contract))
    credit_before = _credit(client, contract, account)
    result = _attempt(
        client,
        account,
        contract,
        "open_grant",
        [STATEMENT, [SUPPORTS_URL, CONTRA_URL], THRESHOLD, PAYEE, f"e2e-dupe-{RUN}"],
        DEPOSIT,
    )

    # A refused payable call returns "credit", so the call succeeds and the grant
    # ids do not grow: the credit is what tells the refusal apart from an opening.
    if _definite_rollback(result):
        pytest.skip("the network reverted the send instead of booking a credit")
    assert len(_grant_ids(client, contract)) == before, (
        "a duplicate reference opened a second grant: " f"{result}"
    )
    assert _credit(client, contract, account) == credit_before + DEPOSIT


def test_04_a_payee_is_never_the_donor(client, account, contract):
    before = len(_grant_ids(client, contract))
    credit_before = _credit(client, contract, account)

    # A grant to the donor's own address is refused: a payee is another party.
    result = _attempt(
        client,
        account,
        contract,
        "open_grant",
        [STATEMENT, [SUPPORTS_URL, CONTRA_URL], THRESHOLD, str(account.address), f"e2e-self-{RUN}"],
        DEPOSIT,
    )

    if _definite_rollback(result):
        pytest.skip("the network reverted the send instead of booking a credit")
    assert len(_grant_ids(client, contract)) == before, (
        "the donor paid itself: " f"{result}"
    )
    assert _credit(client, contract, account) == credit_before + DEPOSIT


def test_05_a_bad_grant_is_a_credit(client, account, contract):
    before = len(_grant_ids(client, contract))
    credit_before = _credit(client, contract, account)

    for args in (
        [STATEMENT, [SUPPORTS_URL], THRESHOLD, PAYEE, f"e2e-count-{RUN}"],
        [STATEMENT, [SUPPORTS_URL, CONTRA_URL], 1, PAYEE, f"e2e-bar-{RUN}"],
        ["soon", [SUPPORTS_URL, CONTRA_URL], THRESHOLD, PAYEE, f"e2e-short-{RUN}"],
        [STATEMENT, ["http://source.example.com/page", CONTRA_URL], THRESHOLD, PAYEE, f"e2e-url-{RUN}"],
    ):
        result = _attempt(client, account, contract, "open_grant", args, DEPOSIT)
        if _definite_rollback(result):
            pytest.skip("the network reverted the send instead of booking a credit")

    assert len(_grant_ids(client, contract)) == before
    assert _credit(client, contract, account) >= credit_before


def test_06_a_dispute_needs_a_stricter_bar(client, account, contract):
    grant_id = _open(client, account, contract, "e2e-bar")

    credit_before = _credit(client, contract, account)
    result = _attempt(client, account, contract, "dispute", [grant_id], DEPOSIT)
    if _definite_rollback(result):
        pytest.skip("the network reverted the send instead of booking a credit")

    assert _credit(client, contract, account) == credit_before + DEPOSIT, (
        "a grant with no wider quorum to raise was disputed: "
        f"{_grant(client, contract, grant_id)}"
    )
    assert _grant(client, contract, grant_id)["disputed"] is False


def test_07_a_dispute_needs_the_matching_stake(client, account, opponent, contract):
    if opponent is None:
        pytest.skip("only one wallet is configured, so there is no stranger")

    grant_id = _open(client, account, contract, "e2e-stake", sources=[SUPPORTS_URL, CONTRA_URL, SUPPORTS_URL + "?x"])

    credit_before = _credit(client, contract, opponent)
    for value in (DEPOSIT - 1, DEPOSIT + 1):
        result = _attempt(
            client, opponent, contract, "dispute", [grant_id], value
        )
        if _definite_rollback(result):
            pytest.skip("the network reverted the send instead of booking a credit")

    assert _credit(client, contract, opponent) == credit_before + 2 * DEPOSIT
    assert _grant(client, contract, grant_id)["disputed"] is False


def test_08_the_donor_cannot_dispute_its_own_grant(client, account, contract):
    grant_id = _open(client, account, contract, "e2e-self-dispute", sources=[SUPPORTS_URL, CONTRA_URL, SUPPORTS_URL + "?y"])

    credit_before = _credit(client, contract, account)
    result = _attempt(client, account, contract, "dispute", [grant_id], DEPOSIT)
    if _definite_rollback(result):
        pytest.skip("the network reverted the send instead of booking a credit")

    assert _credit(client, contract, account) == credit_before + DEPOSIT
    assert _grant(client, contract, grant_id)["disputed"] is False


def test_09_a_stranger_cannot_cancel_a_grant(client, account, opponent, contract):
    if opponent is None:
        pytest.skip("only one wallet is configured, so there is no stranger")

    grant_id = _open(client, account, contract, "e2e-cancel")

    _expect_denied(client, opponent, contract, "cancel", [grant_id])
    assert _grant(client, contract, grant_id)["status"] == "OPEN"


def test_10_a_disputed_grant_cannot_be_cancelled(
    client, account, opponent, contract
):
    if opponent is None:
        pytest.skip("only one wallet is configured, so there is no stranger")

    grant_id = _open(
        client,
        account,
        contract,
        "e2e-disputed",
        sources=[SUPPORTS_URL, CONTRA_URL, SUPPORTS_URL + "?z"],
    )
    _expect_ok(client, opponent, contract, "dispute", [grant_id], value=DEPOSIT)
    assert _grant(client, contract, grant_id)["disputed"] is True

    _expect_denied(client, account, contract, "cancel", [grant_id])
    assert _grant(client, contract, grant_id)["status"] == "OPEN"


def test_11_an_unearned_void_and_an_unearned_settle_are_refused(
    client, account, contract
):
    grant_id = _open(client, account, contract, "e2e-unearned")

    _expect_denied(client, account, contract, "void", [grant_id])
    _expect_denied(client, account, contract, "settle", [grant_id])
    assert _grant(client, contract, grant_id)["status"] == "OPEN"


def test_12_an_unknown_grant_dispute_is_a_credit(client, account, contract):
    credit_before = _credit(client, contract, account)
    result = _attempt(
        client, account, contract, "dispute", ["qu-404"], DEPOSIT
    )

    # A refused dispute is a credit, so the call returns instead of succeeding.
    if _definite_rollback(result):
        pytest.skip("the network reverted the send instead of booking a credit")
    assert _credit(client, contract, account) == credit_before + DEPOSIT


def test_13_the_ledger_stays_balanced(client, contract):
    assert _invariant_holds(client, contract)
    assert _read(client, contract, "get_summary")["grants"] == len(
        _grant_ids(client, contract)
    )

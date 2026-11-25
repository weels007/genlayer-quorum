"""The reading: one bit per source, a quorum, and a dispute that raises the bar.

No single source decides anything. The contract reads every source once, counts
the bits, and moves money only when a quorum agrees. A source whose quote cannot
be grounded in its own page does not count at all.
"""

import json

import pytest

STATEMENT = "The project publishes a signed release every month."
SUPPORTS_URL = "https://source.example.com/supports"
CONTRA_URL = "https://source.example.com/contradicts"
THIRD_URL = "https://source.example.com/third"
UNGROUNDABLE_URL = "https://source.example.com/ungroundable"

SUPPORTS_PAGE = (
    "The project publishes a signed release every month. Each release is written "
    "up in the release notes and the repository is public."
)
CONTRA_PAGE = (
    "The project has never published a signed release, and there are no release "
    "notes for it at all."
)
SUPPORTS_QUOTE = "The project publishes a signed release every month."
CONTRA_QUOTE = "The project has never published a signed release."

DEPOSIT = 200
THRESHOLD = 2
ATTEMPTS = 3


def _deployed(direct_deploy):
    return direct_deploy("contracts/Quorum.py")


def _mock_sources(
    direct_vm, supports=SUPPORTS_PAGE, contradict=CONTRA_PAGE, third=SUPPORTS_PAGE
):
    direct_vm.mock_web(
        r".*source\.example\.com/supports.*", {"status": 200, "body": supports}
    )
    direct_vm.mock_web(
        r".*source\.example\.com/contradicts.*", {"status": 200, "body": contradict}
    )
    direct_vm.mock_web(
        r".*source\.example\.com/third.*", {"status": 200, "body": third}
    )
    direct_vm.mock_web(
        r".*source\.example\.com/ungroundable.*", {"status": 200, "body": "ok."}
    )


def _mock_answer(direct_vm, fragment, answer, quote):
    direct_vm.mock_llm(
        r"(?s).*\[\[QUORUM_READING\]\].*" + fragment + r".*",
        json.dumps({"answer": answer, "quote": quote, "reason": "the page says so"}),
    )


def _mock_three(direct_vm, first, second, third):
    """Mock one answer per source, in the order the contract reads them."""
    _mock_sources(direct_vm)
    for fragment, (answer, quote) in (
        (r"source\.example\.com/supports", first),
        (r"source\.example\.com/contradicts", second),
        (r"source\.example\.com/third", third),
    ):
        _mock_answer(direct_vm, fragment, answer, quote)


def _opened(direct_vm, contract, donor, payee, sources, threshold=THRESHOLD):
    direct_vm.sender = donor
    direct_vm.value = DEPOSIT
    grant_id = contract.open_grant(
        STATEMENT, list(sources), threshold, payee, "ref-1"
    )
    direct_vm.value = 0
    return grant_id


def _disputed(direct_vm, contract, refuter, grant_id, value=DEPOSIT):
    direct_vm.sender = refuter
    direct_vm.value = value
    contract.dispute(grant_id)
    direct_vm.value = 0


def _attested(direct_vm, contract, caller, grant_id):
    direct_vm.sender = caller
    return contract.attest(grant_id)


def test_a_quorum_grants_the_payee(
    direct_vm, direct_deploy, direct_alice, direct_bob, direct_owner
):
    direct_vm.warp("2025-01-01T00:00:00Z")
    contract = _deployed(direct_deploy)
    grant_id = _opened(
        direct_vm, contract, direct_alice, direct_bob, [SUPPORTS_URL, CONTRA_URL, THIRD_URL]
    )

    _mock_three(
        direct_vm,
        ("SUPPORTS", SUPPORTS_QUOTE),
        ("SUPPORTS", SUPPORTS_QUOTE),
        ("CONTRADICTS", CONTRA_QUOTE),
    )
    assert _attested(direct_vm, contract, direct_owner, grant_id) == "GRANTED"

    record = contract.get_grant(grant_id)
    assert record["status"] == "DECIDED"
    assert record["decision"] == "GRANTED"
    assert record["supports"] == "110"
    assert record["contradicts"] == "001"
    assert contract.get_summary()["granted"] == 1

    direct_vm.sender = direct_owner
    contract.settle(grant_id)

    ledger = contract.get_ledger()
    assert ledger["total_out"] == DEPOSIT
    assert ledger["locked_total"] == 0
    assert ledger["total_in"] == ledger["total_out"] + ledger["locked_total"]


def test_a_quorum_sends_the_grant_back(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    direct_vm.warp("2025-01-01T00:00:00Z")
    contract = _deployed(direct_deploy)
    grant_id = _opened(
        direct_vm, contract, direct_alice, direct_bob, [CONTRA_URL, UNGROUNDABLE_URL]
    )

    _mock_sources(direct_vm)
    _mock_answer(direct_vm, r"source\.example\.com/contradicts", "CONTRADICTS", CONTRA_QUOTE)
    _mock_answer(direct_vm, r"source\.example\.com/ungroundable", "CONTRADICTS", CONTRA_QUOTE)

    assert _attested(direct_vm, contract, direct_bob, grant_id) == "DENIED"

    direct_vm.sender = direct_bob
    contract.settle(grant_id)

    ledger = contract.get_ledger()
    assert ledger["total_out"] == DEPOSIT
    assert ledger["locked_total"] == 0


def test_no_quorum_books_an_attempt(
    direct_vm, direct_deploy, direct_alice, direct_bob, direct_owner
):
    contract = _deployed(direct_deploy)
    grant_id = _opened(
        direct_vm, contract, direct_alice, direct_bob, [SUPPORTS_URL, CONTRA_URL]
    )

    _mock_three(
        direct_vm,
        ("SUPPORTS", SUPPORTS_QUOTE),
        ("CONTRADICTS", CONTRA_QUOTE),
        ("SILENT", ""),
    )

    assert _attested(direct_vm, contract, direct_owner, grant_id) == ""

    record = contract.get_grant(grant_id)
    assert record["status"] == "OPEN"
    assert record["decision"] == ""
    assert record["attempts"] == 1
    assert record["supports"] == "10"
    assert record["contradicts"] == "01"


def test_an_ungrounded_quote_does_not_count(
    direct_vm, direct_deploy, direct_alice, direct_bob, direct_owner
):
    """A page whose quote is not in it does not count towards a quorum."""
    contract = _deployed(direct_deploy)
    grant_id = _opened(
        direct_vm, contract, direct_alice, direct_bob, [SUPPORTS_URL, UNGROUNDABLE_URL]
    )

    _mock_sources(direct_vm)
    _mock_answer(direct_vm, r"source\.example\.com/supports", "SUPPORTS", SUPPORTS_QUOTE)
    # The model claims support, but the sentence is not in that page at all.
    _mock_answer(
        direct_vm,
        r"source\.example\.com/ungroundable",
        "SUPPORTS",
        "the project publishes a signed release every month",
    )

    assert _attested(direct_vm, contract, direct_owner, grant_id) == ""

    record = contract.get_grant(grant_id)
    assert record["supports"] == "10"
    assert record["contradicts"] == "00"
    assert record["attempts"] == 1


def test_a_dispute_raises_the_bar(
    direct_vm, direct_deploy, direct_alice, direct_bob, direct_owner
):
    """A dispute makes the quorum one page wider, and a clear quorum survives."""
    contract = _deployed(direct_deploy)
    grant_id = _opened(
        direct_vm, contract, direct_alice, direct_bob, [SUPPORTS_URL, CONTRA_URL, THIRD_URL]
    )

    _disputed(direct_vm, contract, direct_owner, grant_id)

    record = contract.get_grant(grant_id)
    assert record["disputed"] is True
    assert record["threshold"] == 3
    assert record["refute_stake"] == DEPOSIT
    assert contract.get_summary()["disputes"] == 1

    _mock_three(
        direct_vm,
        ("SUPPORTS", SUPPORTS_QUOTE),
        ("SUPPORTS", SUPPORTS_QUOTE),
        ("CONTRADICTS", CONTRA_QUOTE),
    )

    # Two pages of three are not enough at the wider bar.
    assert _attested(direct_vm, contract, direct_owner, grant_id) == ""
    record = contract.get_grant(grant_id)
    assert record["attempts"] == 1
    assert record["status"] == "OPEN"


def test_a_disputed_quorum_can_still_grant(
    direct_vm, direct_deploy, direct_alice, direct_bob, direct_owner
):
    contract = _deployed(direct_deploy)
    grant_id = _opened(
        direct_vm, contract, direct_alice, direct_bob, [SUPPORTS_URL, CONTRA_URL, THIRD_URL]
    )
    _disputed(direct_vm, contract, direct_owner, grant_id)

    _mock_three(
        direct_vm,
        ("SUPPORTS", SUPPORTS_QUOTE),
        ("SUPPORTS", SUPPORTS_QUOTE),
        ("SUPPORTS", SUPPORTS_QUOTE),
    )
    assert _attested(direct_vm, contract, direct_owner, grant_id) == "GRANTED"

    direct_vm.sender = direct_alice
    contract.settle(grant_id)

    ledger = contract.get_ledger()
    assert ledger["total_out"] == 2 * DEPOSIT
    assert ledger["locked_total"] == 0


def test_a_unanimous_denial_pays_the_refuter(
    direct_vm, direct_deploy, direct_alice, direct_bob, direct_owner
):
    contract = _deployed(direct_deploy)
    grant_id = _opened(
        direct_vm, contract, direct_alice, direct_bob, [CONTRA_URL, UNGROUNDABLE_URL, THIRD_URL]
    )
    _disputed(direct_vm, contract, direct_owner, grant_id)

    _mock_sources(direct_vm, third=CONTRA_PAGE)
    for fragment in (
        r"source\.example\.com/contradicts",
        r"source\.example\.com/ungroundable",
        r"source\.example\.com/third",
    ):
        _mock_answer(direct_vm, fragment, "CONTRADICTS", CONTRA_QUOTE)

    assert _attested(direct_vm, contract, direct_owner, grant_id) == "DENIED"

    direct_vm.sender = direct_alice
    contract.settle(grant_id)

    ledger = contract.get_ledger()
    assert ledger["total_out"] == 2 * DEPOSIT
    assert ledger["locked_total"] == 0


def test_a_grant_nobody_can_read_is_voided_with_every_party_made_whole(
    direct_vm, direct_deploy, direct_alice, direct_bob, direct_owner
):
    contract = _deployed(direct_deploy)
    grant_id = _opened(
        direct_vm, contract, direct_alice, direct_bob, [SUPPORTS_URL, CONTRA_URL, THIRD_URL]
    )
    _disputed(direct_vm, contract, direct_owner, grant_id)

    _mock_three(
        direct_vm,
        ("SUPPORTS", SUPPORTS_QUOTE),
        ("CONTRADICTS", CONTRA_QUOTE),
        ("CONTRADICTS", CONTRA_QUOTE),
    )

    for _ in range(ATTEMPTS):
        assert _attested(direct_vm, contract, direct_owner, grant_id) == ""

    record = contract.get_grant(grant_id)
    assert record["attempts"] == ATTEMPTS
    assert record["status"] == "OPEN"

    with direct_vm.expect_revert("[EXPECTED]:too_many_attempts"):
        contract.attest(grant_id)

    direct_vm.sender = direct_owner
    contract.void(grant_id)

    record = contract.get_grant(grant_id)
    assert record["status"] == "VOID"
    assert contract.get_summary()["void"] == 1

    ledger = contract.get_ledger()
    assert ledger["total_out"] == 2 * DEPOSIT
    assert ledger["locked_total"] == 0
    assert ledger["total_in"] == ledger["total_out"] + ledger["locked_total"]


def test_void_is_refused_before_the_attempts_are_spent(
    direct_vm, direct_deploy, direct_alice, direct_bob, direct_owner
):
    contract = _deployed(direct_deploy)
    grant_id = _opened(
        direct_vm, contract, direct_alice, direct_bob, [SUPPORTS_URL, CONTRA_URL]
    )

    direct_vm.sender = direct_owner
    with direct_vm.expect_revert("[EXPECTED]:not_voidable"):
        contract.void(grant_id)


def test_settle_waits_for_the_reading(
    direct_vm, direct_deploy, direct_alice, direct_bob, direct_owner
):
    contract = _deployed(direct_deploy)
    grant_id = _opened(
        direct_vm, contract, direct_alice, direct_bob, [SUPPORTS_URL, CONTRA_URL]
    )

    direct_vm.sender = direct_owner
    with direct_vm.expect_revert("[EXPECTED]:grant_not_decided"):
        contract.settle(grant_id)


def test_a_dispute_needs_a_stricter_bar_to_raise(
    direct_vm, direct_deploy, direct_alice, direct_bob, direct_owner
):
    contract = _deployed(direct_deploy)
    grant_id = _opened(
        direct_vm, contract, direct_alice, direct_bob, [SUPPORTS_URL, CONTRA_URL]
    )

    # Two sources and a quorum of two leave no room for a wider quorum.
    _disputed(direct_vm, contract, direct_owner, grant_id)

    assert contract.get_credit(direct_owner) == DEPOSIT
    assert contract.get_grant(grant_id)["disputed"] is False


def test_a_bad_dispute_becomes_a_credit(
    direct_vm, direct_deploy, direct_alice, direct_bob, direct_owner
):
    contract = _deployed(direct_deploy)
    grant_id = _opened(
        direct_vm, contract, direct_alice, direct_bob, [SUPPORTS_URL, CONTRA_URL, THIRD_URL]
    )

    for value in (DEPOSIT - 1, DEPOSIT + 1):
        _disputed(direct_vm, contract, direct_owner, grant_id, value=value)
    assert contract.get_credit(direct_owner) == 2 * DEPOSIT

    # The donor cannot dispute their own grant.
    direct_vm.sender = direct_alice
    direct_vm.value = DEPOSIT
    contract.dispute(grant_id)
    direct_vm.value = 0
    assert contract.get_credit(direct_alice) == DEPOSIT

    direct_vm.sender = direct_owner
    direct_vm.value = DEPOSIT
    contract.dispute("qu-404")
    direct_vm.value = 0
    assert contract.get_credit(direct_owner) == 3 * DEPOSIT

    _disputed(direct_vm, contract, direct_owner, grant_id)
    assert contract.get_grant(grant_id)["disputed"] is True

    # One dispute only.
    _disputed(direct_vm, contract, direct_owner, grant_id)
    assert contract.get_credit(direct_owner) == 4 * DEPOSIT
    assert contract.get_grant(grant_id)["refute_stake"] == DEPOSIT


def test_a_zero_dispute_stake_is_a_plain_revert(
    direct_vm, direct_deploy, direct_alice, direct_bob, direct_owner
):
    contract = _deployed(direct_deploy)
    grant_id = _opened(
        direct_vm, contract, direct_alice, direct_bob, [SUPPORTS_URL, CONTRA_URL, THIRD_URL]
    )

    direct_vm.sender = direct_owner
    direct_vm.value = 0
    with direct_vm.expect_revert("[EXPECTED]:zero_stake"):
        contract.dispute(grant_id)

    assert contract.get_credit(direct_owner) == 0


def test_an_unknown_grant_is_never_read(direct_vm, direct_deploy, direct_owner):
    contract = _deployed(direct_deploy)

    direct_vm.sender = direct_owner
    with direct_vm.expect_revert("[EXPECTED]:unknown_grant"):
        contract.attest("qu-404")

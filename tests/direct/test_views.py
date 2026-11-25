"""Views: what the register admits about itself, and what a reader can check."""

import json

import pytest

STATEMENT = "The project publishes a signed release every month."
SUPPORTS_URL = "https://source.example.com/supports"
CONTRA_URL = "https://source.example.com/contradicts"
THIRD_URL = "https://source.example.com/third"

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


def _deployed(direct_deploy):
    return direct_deploy("contracts/Quorum.py")


def _opened(direct_vm, contract, donor, payee, ref="ref-1"):
    direct_vm.sender = donor
    direct_vm.value = DEPOSIT
    grant_id = contract.open_grant(
        STATEMENT, [SUPPORTS_URL, CONTRA_URL, THIRD_URL], THRESHOLD, payee, ref
    )
    direct_vm.value = 0
    return grant_id


def _mock_reading(direct_vm):
    direct_vm.mock_web(
        r".*source\.example\.com/supports.*", {"status": 200, "body": SUPPORTS_PAGE}
    )
    direct_vm.mock_web(
        r".*source\.example\.com/contradicts.*", {"status": 200, "body": CONTRA_PAGE}
    )
    direct_vm.mock_web(
        r".*source\.example\.com/third.*", {"status": 200, "body": SUPPORTS_PAGE}
    )
    direct_vm.mock_llm(
        r"(?s).*\[\[QUORUM_READING\]\].*source\.example\.com/supports.*",
        json.dumps({"answer": "SUPPORTS", "quote": SUPPORTS_QUOTE, "reason": "it says so"}),
    )
    direct_vm.mock_llm(
        r"(?s).*\[\[QUORUM_READING\]\].*source\.example\.com/contradicts.*",
        json.dumps({"answer": "CONTRADICTS", "quote": CONTRA_QUOTE, "reason": "it says so"}),
    )
    direct_vm.mock_llm(
        r"(?s).*\[\[QUORUM_READING\]\].*source\.example\.com/third.*",
        json.dumps({"answer": "SUPPORTS", "quote": SUPPORTS_QUOTE, "reason": "it says so"}),
    )


def test_a_fresh_register_is_empty(direct_deploy):
    contract = _deployed(direct_deploy)

    assert contract.get_grant_ids()["ids"] == []
    assert contract.get_grants() == {}
    assert contract.get_ledger() == {"total_in": 0, "total_out": 0, "locked_total": 0}

    summary = contract.get_summary()
    assert summary["grants"] == 0
    assert summary["settled"] == 0
    assert summary["void"] == 0


def test_grants_are_listed_in_order(
    direct_vm, direct_deploy, direct_alice, direct_bob, direct_owner
):
    contract = _deployed(direct_deploy)

    _opened(direct_vm, contract, direct_alice, direct_bob, ref="a")
    _opened(direct_vm, contract, direct_owner, direct_bob, ref="b")

    assert contract.get_grant_ids()["ids"] == ["qu-1", "qu-2"]

    grants = contract.get_grants()
    assert len(grants) == 2
    assert grants["qu-1"]["status"] == "OPEN"
    assert grants["qu-1"]["sources"] == [SUPPORTS_URL, CONTRA_URL, THIRD_URL]


def test_the_reading_reports_the_bits_and_the_quotes(
    direct_vm, direct_deploy, direct_alice, direct_bob, direct_owner
):
    contract = _deployed(direct_deploy)
    grant_id = _opened(direct_vm, contract, direct_alice, direct_bob)

    _mock_reading(direct_vm)
    direct_vm.sender = direct_owner
    contract.attest(grant_id)

    reading = contract.get_reading(grant_id)
    assert reading["supports"] == "101"
    assert reading["contradicts"] == "010"
    assert reading["quotes"] == [SUPPORTS_QUOTE, CONTRA_QUOTE, SUPPORTS_QUOTE]
    assert len(reading["digests"]) == 3
    assert reading["threshold"] == THRESHOLD
    assert reading["quorum_met"] is True

    # The reading is behind its own view: a listing of grants stays small.
    assert "quotes" not in contract.get_grant(grant_id)


def test_unknown_grants_are_never_disputed_or_read(direct_deploy):
    contract = _deployed(direct_deploy)

    assert contract.is_disputed("qu-404") is False

    with pytest.raises(Exception):
        contract.get_reading("qu-404")


def test_the_rules_are_public(direct_deploy):
    contract = _deployed(direct_deploy)
    rules = contract.get_rules()

    assert rules["min_deposit"] == 200
    assert rules["min_sources"] == 2
    assert rules["max_sources"] == 4
    assert rules["min_threshold"] == 2
    assert rules["max_attempts"] == 3
    assert set(rules["answers"]) == {"SUPPORTS", "CONTRADICTS"}
    assert set(rules["decisions"]) == {"GRANTED", "DENIED"}

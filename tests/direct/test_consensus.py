"""Consensus: the two bit strings are the whole decision.

A quorum is counted from the bits, never from prose. The quotes are stored for a
reader and no later call reads them as a decision, so two validators that read
the same sources must produce the same bits, and a validator that cannot read a
source refuses rather than guessing.
"""

import json

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


def _mock_sources(direct_vm):
    direct_vm.mock_web(
        r".*source\.example\.com/supports.*", {"status": 200, "body": SUPPORTS_PAGE}
    )
    direct_vm.mock_web(
        r".*source\.example\.com/contradicts.*", {"status": 200, "body": CONTRA_PAGE}
    )
    direct_vm.mock_web(
        r".*source\.example\.com/third.*", {"status": 200, "body": SUPPORTS_PAGE}
    )


def _mock_answers(direct_vm, third="SUPPORTS"):
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
        json.dumps({"answer": third, "quote": SUPPORTS_QUOTE, "reason": "it says so"}),
    )


def _read(direct_vm, direct_deploy, donor, payee, caller):
    contract = _deployed(direct_deploy)
    direct_vm.sender = donor
    direct_vm.value = DEPOSIT
    grant_id = contract.open_grant(
        STATEMENT, [SUPPORTS_URL, CONTRA_URL, THIRD_URL], THRESHOLD, payee.address, "ref-1"
    )
    direct_vm.value = 0

    _mock_sources(direct_vm)
    _mock_answers(direct_vm)

    direct_vm.sender = caller
    contract.attest(grant_id)
    return contract, grant_id


def test_a_validator_accepts_the_same_bit_strings(
    direct_vm, direct_deploy, direct_alice, direct_bob, direct_owner
):
    contract, grant_id = _read(
        direct_vm, direct_deploy, direct_alice, direct_bob, direct_owner
    )

    assert contract.get_grant(grant_id)["status"] == "DECIDED"
    assert direct_vm.run_validator() is True


def test_a_validator_rejects_a_different_bit(
    direct_vm, direct_deploy, direct_alice, direct_bob, direct_owner
):
    _read(direct_vm, direct_deploy, direct_alice, direct_bob, direct_owner)

    direct_vm.clear_mocks()
    _mock_sources(direct_vm)
    _mock_answers(direct_vm, third="CONTRADICTS")

    assert direct_vm.run_validator() is False


def test_a_validator_refuses_when_it_cannot_read_a_source(
    direct_vm, direct_deploy, direct_alice, direct_bob, direct_owner
):
    _read(direct_vm, direct_deploy, direct_alice, direct_bob, direct_owner)

    # No page and no model: the validator cannot reproduce the bits.
    direct_vm.clear_mocks()

    assert direct_vm.run_validator() is False

# Quorum — submission pack

**A grant released only when a quorum of independent sources agrees.**

## The idea

One donor locks a grant behind one statement and a frozen list of independent
pages. No single page decides it: the committee reads every page once, and the
grant is released only when at least `threshold` of them back the statement. The
same threshold of pages contradicting it returns the grant to the donor.

A stranger who believes the quorum is wrong matches the deposit, and **the dispute
raises the bar** — the quorum that settles the grant is one page wider — instead
of re-reading the pages or re-aiming the question. Nothing is ever re-chosen: the
statement, the sources and the payee are frozen at `open_grant`.

The whole decision is two bit strings, one bit per source. The quotes are stored
so a reader can check what each page said, and no later call reads them as a
decision.

## Deployed facts

| | |
|---|---|
| Network | GenLayer Studio — chainId `61999` (`0xf22f`) |
| Contract | _recorded by the Deploy Contract workflow_ |
| Source revision | _the first twelve characters of the SHA-256 of `contracts/Quorum.py`_ |
| Owner | none: no pause, no setter, no upgrade path |
| Money | test GEN only |

`python scripts/verify_deployment.py` recomputes that revision from the checkout
and fails if the contract has drifted from the recorded address.

## What is proven, and where

| Suite | Proves |
|---|---|
| **Direct**, in memory | opening, every refusal as a credit, the quorum, the per-source grounding, the wider bar after a dispute, the denial, the void, the cancel, the validator's agreement on the bits, and the ledger invariant |
| **Studio**, live | a real grant with three sources, a quorum that releases it, a dispute that raises the bar, a grant nobody could read that is voided, the settlement, and the views agreeing with the ledger. The payout is checked twice: the ledger moves, and the contract's real balance falls |
| **Money paths**, live | a deposit below the floor as a credit and the credit withdrawn, a duplicate reference, a self-payee, a stake that does not match, a dispute with no wider bar, a stranger cancelling, and an unearned settle or void |
| **Lint** | `genvm-lint check contracts/Quorum.py` |

Every public method — seven writes and ten views — is exercised against the
deployed address. The evidence is the
[Actions](https://github.com/weels007/genlayer-quorum/actions) history.

The live suites sign with throwaway Studio accounts that live in Actions secrets —
one donor and one stranger — so no key material is committed.

## Limits, stated plainly

- **A quorum is a majority, not a proof.** A page that backs a statement for the
  wrong reason still counts, because the contract checks that the sentence is in
  the page, not that the page is right.
- **A page that changes while the committee reads it cannot be counted.** Two
  validators that read different text produce different bits, consensus writes
  nothing, and the grant books an attempt rather than settle on a reading nobody
  agreed on. Three attempts, and anyone may void it.
- **A transaction is retried with a larger fee deposit.** Studio has been
  reverting sends that carry only the fee its client quotes, at the EVM step and
  before any contract code runs. The deploy script and both live suites retry with
  more. A reverted send never landed, so the retry is the same call rather than a
  second one.
- **Studio only.** No mainnet, no audited token, no upgrade path — by design.

## Try it

```bash
pip install -r requirements.txt

python scripts/verify_deployment.py     # the address is this source
pytest tests/direct -v                  # the whole register, in memory, milliseconds
pytest tests/integration -v -s          # the deployed register, live
pytest tests/e2e -v -s                  # the deployed register's money paths
```

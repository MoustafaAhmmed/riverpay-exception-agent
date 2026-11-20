import pytest

from riverpay_agent.data import Bank, load_events
from riverpay_agent.policy import decide

# Worked out by hand from ops_policy.md
EXPECTED = {
    "E001": ("AUTO_RESOLVE", "R4"),             # balance 12 < 80
    "E002": ("ESCALATE_RECONCILIATION", "R4"),  # balance 1800 >= 500: stale event
    "E003": ("AUTO_RESOLVE", "R3"),             # real duplicate of TXN-2003
    "E004": ("ESCALATE_TECH_OPS", "R5"),        # fake duplicate (different payee), TIMEOUT
    "E005": ("AUTO_BLOCK", "R1"),               # fraud 0.93 + new device + 2500
    "E006": ("ESCALATE_FRAUD_OPS", "R2"),       # fraud 0.75
    "E007": ("ESCALATE_FRAUD_OPS", "R2"),       # amount 8000
    "E008": ("AUTO_RESOLVE", "R6"),             # payee not found
    "E009": ("AUTO_RESOLVE", "R7"),             # really KYC0, not KYC1
    "E010": ("AUTO_NOTIFY", "R8"),              # float 80 < 500
    "E011": ("ESCALATE_AGENT_NETWORK", "R8"),   # float 0, queue 9
    "E012": ("ESCALATE_COMPLIANCE", "R9"),      # KYC mismatch
    "E013": ("AUTO_RESOLVE", "R10"),            # PIN, 400 < 2000
    "E014": ("ESCALATE_FRAUD_OPS", "R10"),      # PIN, 3000 >= 2000
    "E015": ("AUTO_BLOCK", "R11"),              # on the watchlist
    "E016": ("ESCALATE_TECH_OPS", "R5"),        # TIMEOUT, never retry
    "E017": ("ESCALATE_DATA_QUALITY", "R13"),   # txn_id missing
    "E018": ("ESCALATE_RECONCILIATION", "R12"), # says SUCCESS, ledger FAILED
    "E019": ("ESCALATE_FRAUD_OPS", "R2"),       # cross-border
}

bank = Bank()
events = {e["event_id"]: e for e in load_events()}


@pytest.mark.parametrize("event_id", sorted(EXPECTED))
def test_policy_decision(event_id):
    result = decide(events[event_id], bank)
    assert (result["decision"], result["rules"][0]) == EXPECTED[event_id]


def test_every_event_is_covered():
    assert set(events) == set(EXPECTED)


def test_watchlist_matches_name_without_id():
    # Viktor Malenkov has no customer_id on the watchlist; a customer with that name must still match.
    bank.customers["C900"] = {"customer_id": "C900", "full_name": "VIKTOR MALENKOV", "kyc_tier": "KYC1",
                              "available_balance_kbr": 0.0}
    try:
        assert bank.check_sanctions("C900")["on_watchlist"]
        assert not bank.check_sanctions("C001")["on_watchlist"]
    finally:
        del bank.customers["C900"]


def test_sanctions_beat_everything():
    event = dict(events["E001"], customer_id="C005")   # same event, but a watchlisted customer
    assert decide(event, bank)["decision"] == "AUTO_BLOCK"

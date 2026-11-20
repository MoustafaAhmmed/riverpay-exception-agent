from riverpay_agent.checks import finalize
from riverpay_agent.data import Bank, load_events
from riverpay_agent.policy import decide

bank = Bank()
events = {e["event_id"]: e for e in load_events()}


def proposal(decision, message="We could not complete your transfer.", confidence=0.9):
    return {"decision": decision, "reason_codes": ["R4"], "customer_message": message,
            "internal_case_note": "note", "confidence": confidence}


def run(event_id, prop):
    event = events[event_id]
    return finalize(event, prop, decide(event, bank), ["lookup_customer"], bank)


def test_agreement_keeps_agent_text():
    record, check = run("E001", proposal("AUTO_RESOLVE"))
    assert check["agrees"]
    assert record["decision"] == "AUTO_RESOLVE"
    assert record["customer_message"] == "We could not complete your transfer."
    assert record["requires_human"] is False


def test_policy_wins_when_agent_disagrees():
    # The E002 trap: an agent that trusts the event would say AUTO_RESOLVE.
    record, check = run("E002", proposal("AUTO_RESOLVE"))
    assert not check["agrees"]
    assert record["decision"] == "ESCALATE_RECONCILIATION"
    assert record["requires_human"] is True
    assert "Policy check" in record["internal_case_note"]


def test_never_mention_sanctions_to_customer():
    record, _ = run("E015", proposal("AUTO_BLOCK", message="Your wallet is blocked due to sanctions."))
    assert "sanction" not in record["customer_message"].lower()


def test_no_success_claim_unless_ledger_says_success():
    record, _ = run("E018", proposal("ESCALATE_RECONCILIATION", message="Good news, your transfer succeeded!"))
    assert "succeed" not in record["customer_message"].lower()


def test_low_confidence_goes_to_human():
    record, _ = run("E001", proposal("AUTO_RESOLVE", confidence=0.4))
    assert record["requires_human"] is True


def test_agent_failure_falls_back_to_policy():
    record, check = run("E001", None)
    assert record["decision"] == "AUTO_RESOLVE"
    assert record["requires_human"] is True
    assert record["customer_message"]


def test_negated_success_is_not_a_success_claim():
    msg = "Your transfer was not successful. We are checking it."
    record, check = run("E002", proposal("ESCALATE_RECONCILIATION", message=msg))
    assert record["customer_message"] == msg
    assert check["flags"] == []


def test_duplicate_can_say_the_first_transfer_went_through():
    # E003: TXN-2003 really succeeded, so telling the customer is true.
    msg = "Your first transfer went through, so we did not send it twice."
    record, _ = run("E003", proposal("AUTO_RESOLVE", message=msg))
    assert record["customer_message"] == msg


def test_timeout_message_does_not_say_failed():
    # E016: after a timeout we don't know if money moved, so "did not go through" is a guess.
    msg = "Your 200 KBR transfer did not go through. Please don't resend it yet."
    record, check = run("E016", proposal("ESCALATE_TECH_OPS", message=msg))
    assert "did not go through" not in record["customer_message"]
    assert "do not resend" in record["customer_message"]
    assert any("outcome is unknown" in f for f in check["flags"])


def test_timeout_message_saying_we_are_checking_is_kept():
    msg = "We are checking the status of your 80 KBR transfer. Please don't resend it yet."
    record, check = run("E004", proposal("ESCALATE_TECH_OPS", message=msg))
    assert record["customer_message"] == msg


def test_replaced_message_is_kept_in_the_eval_flags():
    _, check = run("E016", proposal("ESCALATE_TECH_OPS", message="Your money was sent successfully."))
    assert any("Your money was sent successfully." in f for f in check["flags"])

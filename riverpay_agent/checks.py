"""Guardrails: check the agent's proposal before it becomes the final decision.

1. The decision must match the written policy. If the agent disagrees with
   policy.decide(), the policy wins and the case goes to a human.
2. The customer message must be safe (no rule IDs, no "fraud score", no mention
   of sanctions, no success claim unless the ledger says SUCCESS).
3. Low confidence (< 0.6) always goes to a human.
"""
import re

from .data import TOOL_NAMES
from .policy import HUMAN_DECISIONS

# Used when the agent's message is missing or unsafe.
SAFE_MESSAGE = {
    "AUTO_RESOLVE": "Your transfer did not go through and no money was taken from your wallet. Please check the details and try again.",
    "AUTO_NOTIFY": "Your float is running low. Please top up soon so customers can cash out.",
    "AUTO_BLOCK": "Your wallet is temporarily on hold while we review recent activity. Our team will contact you.",
    "ESCALATE_FRAUD_OPS": "Your transfer is being reviewed. We will update you shortly.",
    "ESCALATE_COMPLIANCE": "We need to confirm some details on your account. Our team will contact you.",
    "ESCALATE_TECH_OPS": "We are checking the status of your transfer. Please do not resend it; we will update you shortly.",
    "ESCALATE_RECONCILIATION": "We are checking the status of your transfer and will update you shortly.",
    "ESCALATE_AGENT_NETWORK": "We know about the cash shortage at this outlet and our team is on it.",
    "ESCALATE_DATA_QUALITY": "We are looking into your request and will update you shortly.",
}

# What the analyst should do next, used when there is no agent note.
NEXT_STEP = {
    "AUTO_RESOLVE": "No action needed; customer notified.",
    "AUTO_NOTIFY": "Agent and territory manager notified to rebalance float.",
    "AUTO_BLOCK": "Wallet frozen; review in the fraud/compliance case.",
    "ESCALATE_FRAUD_OPS": "Fraud analyst to review the transfer.",
    "ESCALATE_COMPLIANCE": "Compliance to verify the customer's identity.",
    "ESCALATE_TECH_OPS": "Confirm the transfer outcome with the switch before any manual retry. Do not auto-retry.",
    "ESCALATE_RECONCILIATION": "Reconcile the event against the ledger before telling the customer anything.",
    "ESCALATE_AGENT_NETWORK": "Agent network team to send float to the outlet.",
    "ESCALATE_DATA_QUALITY": "Fix the missing/unknown identifiers at the source and re-run.",
}

FORBIDDEN_WORDS = re.compile(r"sanction|watch ?list|fraud score|\bR(?:[1-9]|1[0-3])\b", re.I)
SUCCESS_WORDS = re.compile(r"\b(succeeded|succeed|successful|successfully|success|went through|"
                           r"(was|has been) (sent|delivered|completed))\b", re.I)
NEGATION = re.compile(r"\b(not|never|no)\b|n't\b", re.I)
# After a timeout the outcome is unknown, so "it failed" is a guess too.
FAILURE_WORDS = re.compile(r"(did not|didn't|could not|couldn't) (go through|complete|work)|"
                           r"\b(failed|unsuccessful|not successful|no money was taken)\b", re.I)


def claims_success(message):
    """True if the message says a transfer worked. "was not successful" does not count."""
    for match in SUCCESS_WORDS.finditer(message):
        just_before = message[max(0, match.start() - 25):match.start()]
        if not NEGATION.search(just_before):
            return True
    return False


def message_problem(message, ledger_status, success_ok=False, outcome_unknown=False):
    """Return why a customer message is unsafe, or None if it is fine."""
    if not message or not message.strip():
        return "empty message"
    if FORBIDDEN_WORDS.search(message):
        return "mentions internal terms"
    if ledger_status != "SUCCESS" and not success_ok and claims_success(message):
        return "claims success but ledger is not SUCCESS"
    if outcome_unknown and FAILURE_WORDS.search(message):
        return "says the transfer failed but the outcome is unknown"
    return None


def finalize(event, proposal, policy, tools_used, bank, agent_used=True):
    """Combine the agent proposal and the policy check into the final record.

    Returns (record, check). `record` matches output_schema.json; `check` is for the eval table.
    agent_used=False means a rules-only run (no LLM), so there is no proposal by design.
    """
    final = policy["decision"]
    agent_decision = proposal["decision"] if proposal else None
    disagrees = proposal is not None and agent_decision != final
    flags = []

    txn_id = event.get("txn_id")
    txn = bank.lookup_transaction(txn_id) if txn_id else None
    ledger_status = txn["status"] if txn and txn["found"] else None

    if proposal:
        note = proposal["internal_case_note"].strip()
        message = proposal["customer_message"].strip()
        confidence = float(proposal["confidence"])
    else:
        note = f"{policy['rules'][0]}: {policy['reason']}. Next: {NEXT_STEP[final]}"
        message = ""
        if agent_used:   # the agent failed to decide: fall back to the policy, but have a human look
            confidence = 0.5
            flags.append("no agent decision, policy used")
        else:
            confidence = 1.0

    if disagrees:
        note += (f"\n[Policy check] Agent proposed {agent_decision}; policy requires {final} "
                 f"({policy['rules'][0]}: {policy['reason']}). Policy decision applied; please review.")
        confidence = min(confidence, 0.5)
        flags.append(f"agent said {agent_decision}, policy says {final}")

    # R3: the original transfer really did succeed in the ledger, so saying so is true.
    success_ok = policy["rules"][0] == "R3"
    # R5: timeout/unknown, nobody knows yet if money moved.
    outcome_unknown = policy["rules"][0] == "R5"
    problem = "decision changed" if disagrees else message_problem(message, ledger_status, success_ok,
                                                                   outcome_unknown)
    if problem:
        if proposal:
            flags.append(f'customer message replaced ({problem}): "{message[:120]}"')
        message = SAFE_MESSAGE[final]

    confidence = max(0.0, min(1.0, confidence))
    requires_human = final in HUMAN_DECISIONS or disagrees or confidence < 0.6
    if confidence < 0.6 and final not in HUMAN_DECISIONS:
        note += "\n[Low confidence] Please review before closing."
        flags.append("low confidence")

    record = {
        "event_id": event["event_id"],
        "decision": final,
        "reason_codes": policy["rules"],
        "customer_message": message,
        "internal_case_note": note,
        "tools_used": [t for t in dict.fromkeys(tools_used) if t in TOOL_NAMES],
        "confidence": round(confidence, 2),
        "requires_human": requires_human,
    }
    check = {"event_id": event["event_id"], "agent": agent_decision, "policy": final,
             "rule": policy["rules"][0], "agrees": not disagrees, "flags": flags}
    return record, check

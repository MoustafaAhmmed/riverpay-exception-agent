"""The ops policy (R1-R13) written as plain code.

This is the deterministic check. The agent proposes a decision, and this
function says what the written policy requires. Rules are checked in the
policy's precedence order: R11, R1, R13, R12, R9, R2, R10, then the rest.
The first rule that applies wins.
"""

HUMAN_DECISIONS = {
    "AUTO_BLOCK",
    "ESCALATE_FRAUD_OPS",
    "ESCALATE_COMPLIANCE",
    "ESCALATE_TECH_OPS",
    "ESCALATE_RECONCILIATION",
    "ESCALATE_AGENT_NETWORK",
    "ESCALATE_DATA_QUALITY",
}

# event types that are about a money transfer (they need a txn_id)
TRANSFER_EVENTS = {"transfer_failed", "duplicate_suspected", "transfer_completed", "pin_locked"}


def result(decision, rule, reason):
    return {"decision": decision, "rules": [rule], "reason": reason}


def decide(event, bank):
    kind = event["event_type"]
    customer_id = event.get("customer_id")
    txn_id = event.get("txn_id")
    agent_id = event.get("agent_id")
    payload = event.get("payload") or {}
    fraud = payload.get("fraud_score") or 0

    # Look things up first. Never decide from the event alone.
    customer = bank.lookup_customer(customer_id) if customer_id else None
    txn = bank.lookup_transaction(txn_id) if txn_id else None
    agent = bank.lookup_agent(agent_id) if agent_id else None

    # Prefer the ledger over the event for amount, failure code, cross-border.
    in_ledger = txn is not None and txn["found"]
    amount = txn["amount_kbr"] if in_ledger else (event.get("amount_kbr") or 0)
    failure = txn["failure_code"] if in_ledger and txn["failure_code"] else payload.get("failure_reason")
    cross_border = payload.get("cross_border") or (in_ledger and txn["cross_border"])

    # R11 - sanctions
    if customer_id and bank.check_sanctions(customer_id)["on_watchlist"]:
        return result("AUTO_BLOCK", "R11", f"{customer_id} is on the internal watchlist")

    # R1 - hard fraud block
    new_device_or_payee = payload.get("new_device") or payload.get("new_beneficiary")
    if kind in TRANSFER_EVENTS and fraud >= 0.90 and new_device_or_payee and amount >= 1000:
        return result("AUTO_BLOCK", "R1", f"fraud {fraud}, new device/payee, amount {amount:g}")

    # R13 - data quality
    missing = []
    if kind in TRANSFER_EVENTS or kind == "kyc_mismatch":
        if not customer_id:
            missing.append("customer_id missing")
        elif not customer["found"]:
            missing.append(f"customer {customer_id} not in customer file")
    if kind in TRANSFER_EVENTS:
        if not txn_id:
            missing.append("txn_id missing")
        elif not in_ledger:
            missing.append(f"{txn_id} not in ledger")
    if kind == "agent_float_low":
        if not agent_id:
            missing.append("agent_id missing")
        elif not agent["found"]:
            missing.append(f"agent {agent_id} not in agent file")
    if missing:
        return result("ESCALATE_DATA_QUALITY", "R13", "; ".join(missing))

    # R12 - the event must agree with the ledger, and never claim a success the ledger does not show
    if in_ledger:
        if kind == "transfer_completed" and txn["status"] != "SUCCESS":
            return result("ESCALATE_RECONCILIATION", "R12",
                          f"event says SUCCESS, ledger says {txn['status']} ({txn['failure_code']})")
        if txn["payer_id"] != customer_id or txn["amount_kbr"] != event.get("amount_kbr"):
            return result("ESCALATE_RECONCILIATION", "R12", "event payer/amount does not match the ledger")

    # R9 - KYC mismatch
    if kind == "kyc_mismatch":
        return result("ESCALATE_COMPLIANCE", "R9", "identity mismatch, never auto-approved")

    # R2 - fraud / high value review
    if kind in TRANSFER_EVENTS:
        if fraud >= 0.70:
            return result("ESCALATE_FRAUD_OPS", "R2", f"fraud score {fraud} >= 0.70")
        if amount >= 5000:
            return result("ESCALATE_FRAUD_OPS", "R2", f"amount {amount:g} >= 5000")
        if cross_border:
            return result("ESCALATE_FRAUD_OPS", "R2", "cross-border transfer")

    # R10 - PIN locked
    if kind == "pin_locked":
        if amount < 2000:
            return result("AUTO_RESOLVE", "R10", f"amount {amount:g} < 2000, keep locked, send reset steps")
        return result("ESCALATE_FRAUD_OPS", "R10", f"amount {amount:g} >= 2000, do not unlock")

    # R8 - agent float
    if kind == "agent_float_low":
        fl, warn, queue = agent["float_kbr"], agent["float_warning_kbr"], agent["pending_cashout_queue"]
        if fl == 0 and queue >= 5:
            return result("ESCALATE_AGENT_NETWORK", "R8", f"float 0, {queue:g} cash-outs waiting")
        if 0 < fl < warn:
            return result("AUTO_NOTIFY", "R8", f"float {fl:g} below warning {warn:g}")
        return result("ESCALATE_AGENT_NETWORK", "R8", f"float {fl:g}, queue {queue:g}: not a clear R8 case")

    # R3 - duplicate of a completed transfer
    if kind == "duplicate_suspected":
        recent = bank.lookup_recent_transfers(customer_id, event["received_at"])["transfers"]
        for r in recent:
            if (r["txn_id"] != txn_id and r["status"] == "SUCCESS"
                    and r["payee_id"] == txn["payee_id"] and r["amount_kbr"] == amount):
                return result("AUTO_RESOLVE", "R3", f"duplicate of {r['txn_id']} which succeeded")
        # no matching success -> not a duplicate, fall through to the real failure code

    # R4 - insufficient funds
    if failure == "INSUFFICIENT_FUNDS":
        balance = customer["available_balance_kbr"]
        if balance < amount:
            return result("AUTO_RESOLVE", "R4", f"balance {balance:g} < amount {amount:g}")
        return result("ESCALATE_RECONCILIATION", "R4", f"balance {balance:g} >= amount {amount:g}, event looks stale")

    # R5 - timeout / unknown (R2 already caught amount >= 5000 and fraud >= 0.70)
    if failure in ("TIMEOUT", "UNKNOWN"):
        return result("ESCALATE_TECH_OPS", "R5", f"{failure}: outcome unknown, manual check before any retry")

    # R6 - beneficiary not found
    if failure == "BENEFICIARY_NOT_FOUND":
        return result("AUTO_RESOLVE", "R6", "payee not found, no money moved")

    # R7 - limit exceeded (limit from the customer file, not the event)
    if failure == "LIMIT_EXCEEDED":
        tier = customer["kyc_tier"]
        return result("AUTO_RESOLVE", "R7", f"{tier} daily limit is {customer['daily_limit_kbr']} KBR")

    # Nothing matched clearly: if unsure, escalate.
    if failure in ("FRAUD_HOLD", "MANUAL_REVIEW"):
        return result("ESCALATE_FRAUD_OPS", "R2", f"{failure} with no automatic rule")
    return result("ESCALATE_TECH_OPS", "R5", f"no rule covers {kind}/{failure}")

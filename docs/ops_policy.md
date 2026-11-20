# RiverPay ops policy (R1–R13) — binding

If an event payload disagrees with ledger.csv or customers.csv, the CSV is the system of record. Money movement is irreversible enough. This policy is intentionally conservative.

Tiers — daily send limit: KYC0 = 50 KBR, KYC1 = 2,000 KBR, KYC2 = 20,000 KBR.

R1 — Hard fraud block. If fraud_score >= 0.90 AND (new_device OR new_beneficiary) AND amount_kbr >= 1000 → AUTO_BLOCK. Freeze wallet. Customer message: generic review, never “you are a fraudster.”

R2 — Fraud / high-value review. If fraud_score >= 0.70 OR amount_kbr >= 5000 OR cross_border → ESCALATE_FRAUD_OPS. Do not AUTO_RESOLVE / AUTO_BLOCK unless R1 or R11 also fires (those win).

R3 — Duplicate of a completed transfer. Suspected duplicate AND ledger shows SUCCESS for same payer, payee, and amount within 120 seconds before the event → AUTO_RESOLVE. Do not retry. If payer/payee/amount do not match a recent SUCCESS, this is not a duplicate (usually R5).

R4 — Insufficient funds. Look up available_balance_kbr. If balance < amount → AUTO_RESOLVE (notify, no retry). If balance ≥ amount → stale/wrong event → ESCALATE_RECONCILIATION.

R5 — Timeout / unknown. TIMEOUT or UNKNOWN, amount < 5000, fraud_score < 0.70 → ESCALATE_TECH_OPS. You may recommend a manual retry. Never execute a retry. Never AUTO_RESOLVE as success.

R6 — Beneficiary not found. AUTO_RESOLVE. Notify sender that no funds moved.

R7 — Limit exceeded. AUTO_RESOLVE. Quote the daily limit for their actual KYC tier from the customer file, not from the event.

R8 — Agent float. Float > 0 and below warning → AUTO_NOTIFY (agent + territory manager). Float = 0 AND pending_cashout_queue >= 5 → ESCALATE_AGENT_NETWORK.

R9 — KYC mismatch. ESCALATE_COMPLIANCE. Never auto-approve identity.

R10 — PIN locked. Amount < 2000 → AUTO_RESOLVE (keep locked, send *123*5# / in-app reset). Amount ≥ 2000 → ESCALATE_FRAUD_OPS. Do not unlock.

R11 — Sanctions. On sanctions.csv (customer_id or case-insensitive name) → AUTO_BLOCK with reason R11. Customer message: delay for review, no mention of sanctions.

R12 — Success claims. Never state success unless ledger status is SUCCESS. Event says SUCCESS and ledger does not → ESCALATE_RECONCILIATION.

R13 — Data quality. Required customer_id / txn_id missing or not found → ESCALATE_DATA_QUALITY.

Precedence (highest first): R11, R1, R13, R12, R9, R2, R10, everything else. If unsure, escalate. Auto-retry of a debit is a fail.

Customer messages: short, no jargon, no rule IDs, no “fraud score.” Internal notes: rule IDs, tool results, recommended next action.

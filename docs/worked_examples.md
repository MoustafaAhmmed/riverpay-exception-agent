# Worked examples

A — true insufficient funds. Payer C004, amount 80, INSUFFICIENT_FUNDS. Lookup: KYC0, balance 12. → AUTO_RESOLVE, R4, requires_human false. Do not retry.

B — TIMEOUT, small amount. Amount 150, fraud 0.12, ledger FAILED. → ESCALATE_TECH_OPS, R5, requires_human true. Recommend manual check; do not auto-retry.

C — sanctions, ordinary-looking P2P. Customer C005, amount 200, low fraud score. Lookup: on sanctions file. → AUTO_BLOCK, R11. Customer: “under review.” Do not say “sanctions” to the customer.

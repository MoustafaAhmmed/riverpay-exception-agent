# RiverPay exception agent

An agent that triages RiverPay's exception queue (`data/events.json`). For each event it looks up the
systems of record, applies the ops policy (R1–R13) and either closes the safe cases or hands the case
to the right team with a note an analyst can act on. Output: `output/decisions.json`.

## Run it

```powershell
python -m venv .venv
.venv\Scripts\activate                 # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
$env:ANTHROPIC_API_KEY = "sk-ant-..."   # macOS/Linux: export ANTHROPIC_API_KEY=...
python run.py                          # agent + policy check -> output/
python run.py --no-llm                 # rules only, no API calls (baseline)
pytest -q                              # 35 tests, no API calls
```

`run.py` writes `output/decisions.json` (validated against `schema/output_schema.json`),
`output/eval.md` (agent vs. written policy, per event) and `output/report.txt` (counts, AUTO_BLOCK ids,
cost, time).

## How it works

```
events.json ──> agent (Claude) ──tool calls──> lookup tools ──> CSV files (mocked core banking)
                    │ proposes decision + messages
                    v
              policy check (R1–R13 in code) ──> decisions.json ──> human queue (ESCALATE_* / AUTO_BLOCK)
```

| Part | File | Real or mocked |
|---|---|---|
| Lookup tools: `lookup_customer`, `lookup_transaction`, `lookup_recent_transfers`, `lookup_agent`, `check_sanctions`, `get_policy` | `riverpay_agent/data.py` | Mocked (CSV files stand in for bank APIs) |
| Agent loop: Claude picks the lookups, then calls `submit_decision` | `riverpay_agent/agent.py` | Real (Claude API, `claude-opus-5-5`) |
| Policy as code: R1–R13 in precedence order | `riverpay_agent/policy.py` | Real, deterministic |
| Guardrails: final decision, safe customer message, human flag | `riverpay_agent/checks.py` | Real, deterministic |
| Notifications / freezing / case creation | not built | The output only says what should happen |

## Design choices

**1. A plain tool loop, no framework (and why not n8n).**
> "The most successful implementations weren't using complex frameworks or specialized libraries."
> (Anthropic, *Building effective agents*, Dec 2024)

The loop is about 50 lines on the Claude API, so every step can be read and tested. I skipped n8n because
the policy decides where money goes: I wanted every rule covered by a unit test and kept in version
control, and a Python script is easier to test, diff and run (two commands) than an exported graph.
Rejected: LangGraph / n8n (extra layers for a single agent with six tools).

**2. The agent decides, a deterministic check reviews.**
Claude does the investigation: it chooses which records to look up, proposes a decision and writes the
customer message and the analyst note. Before anything is released, `policy.decide()` computes what the
written policy requires from the same records. If they disagree, the policy decision is applied and the
case goes to a human with both answers in the note. Rejected: letting the LLM decide alone (one wrong
TIMEOUT retry or "your transfer succeeded" is a real loss) and a rules-only system (no useful notes, and it
breaks on anything the rules did not foresee). It also gives a measurable number: how often the agent
matches the policy.

## Guardrails (what the system can never do)

- No tool moves money, retries a transfer, unlocks a wallet, approves an identity or clears a watchlist hit.
  The agent can only read and propose. Unknown tool names are rejected.
- The final decision always follows the written policy; a disagreement is flagged for a human.
- Customer messages are checked: no rule IDs, no "fraud score", no mention of sanctions/watchlist, and no
  success claim unless the ledger status is SUCCESS. Unsafe messages are replaced with a safe template.
- Confidence below 0.6, an agent error, or no decision after 8 turns → the case goes to a human.
- `tools_used` is recorded by the code that runs the tools, not taken from the model's own claim.

## Results

Full run with `claude-opus-5-5` (details in `output/eval.md` and `output/report.txt`):

| What I checked | Result |
|---|---|
| Agent decision matches the written policy | **19 / 19**, no mismatches |
| Rules-only baseline vs. my hand-worked answers | 19 / 19 (`tests/test_policy.py`) |
| AUTO_BLOCK only on the dangerous cases | E005 (fraud 0.93 + new device) and E015 (watchlist); nothing else |
| Traps caught | E002 stale funds, E004 fake duplicate, E009 wrong tier, E015 sanctions, E016 timeout, E017 missing id, E018 false success |
| Watchlist checked whenever there is a customer | yes, every event with a `customer_id` |
| Output valid against the schema | yes, all 19 |
| Sent to a human | 13 / 19 (6 closed automatically: 5 AUTO_RESOLVE, 1 AUTO_NOTIFY) |

**A miss I found and fixed:** on the first run the guardrail replaced 3 customer messages (E002, E003,
E014). The guardrail was wrong, not the agent: it flagged "was not successful" as a success claim, and
it blocked the true statement in E003 that the first payment went through. I made it ignore negations
and allow that statement for R3 duplicates. Second run: 0 replacements. The guardrail now also logs the
original message whenever it replaces one.

**A second miss, fixed:** for TIMEOUT events (E004, E016) the agent told the customer the transfer
"did not go through". The ledger says FAILED, but after a timeout the debit outcome is really unknown,
so that is a guess. The prompt now says so, and the guardrail replaces any "failed / did not go through"
message on an R5 case with "we are checking, please don't resend". Re-run: both messages now say that.

**What I would not ship yet:** an eval of only 19 events. I would want 200+ before trusting it in production.

## Policy interpretations

Places where the policy was not explicit and I chose the conservative reading:
- Event payer or amount different from the ledger → `ESCALATE_RECONCILIATION` (R12, "trust the ledger").
- E019: the payee (C099) is not in the customer file. R13 is about the event's own `customer_id`/`txn_id`,
  so I applied R2 (cross-border) and mention the unknown payee in the note.
- R8 cases the policy does not describe (e.g. float 0 with a short queue) → `ESCALATE_AGENT_NETWORK`.
- `FRAUD_HOLD` / `MANUAL_REVIEW` with no other rule firing → `ESCALATE_FRAUD_OPS` ("if unsure, escalate").
- The watchlist is checked for the paying customer, not the payee.
- Duplicate window (R3) is measured back from the event's `received_at`.

## What I did not do

- No real notifications, wallet freezes or case tickets; no Slack/email mock.
- Confidence < 0.6 sends the case to a human but does not change the decision code.
- `output_schema.json` was not attached, so `schema/output_schema.json` is rebuilt from the field list on the brief.
- Evaluated on the 19 provided events only; no extra or adversarial events.

## Cost and latency

Measured on the full run: **about $0.60 for all 19 events (~3 cents per event), 40 seconds** with 4
events in parallel, **2 model calls per event** on average (one to look things up, one to decide).
Opus 5.5 at low effort; the policy and tool definitions are prompt-cached, so almost all input tokens are
cache reads. With 5 more days I would run a larger set of synthetic events (including adversarial notes)
and compare a cheaper model (Haiku) on the same eval before choosing one for production.

## AI assistance

I used Claude (Claude Code) as a coding assistant for parts of the code and this README; I reviewed,
ran and tested everything.

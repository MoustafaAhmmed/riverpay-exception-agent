"""Run the exception agent on every event in data/events.json.

    python run.py            # agent (Claude) + policy check   -> needs ANTHROPIC_API_KEY
    python run.py --no-llm   # policy rules only, no API calls (baseline)

Writes output/decisions.json, output/eval.md and output/report.txt.
"""
import argparse
import json
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import jsonschema

from riverpay_agent.agent import MODEL, handle_event
from riverpay_agent.checks import finalize
from riverpay_agent.data import Bank, load_events
from riverpay_agent.policy import decide

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "output"

# USD per million tokens for claude-opus-5-5
PRICE = {"input": 4.00, "output": 20.00, "cache_write": 5.00, "cache_read": 0.20}


def process(event, bank, client):
    policy = decide(event, bank)
    agent = {"proposal": None, "tools_used": [], "turns": 0, "usage": {}, "error": None}
    if client is not None:
        try:
            agent.update(handle_event(client, bank, event))
        except Exception as e:  # one bad event should not stop the run
            agent["error"] = f"{type(e).__name__}: {e}"
    record, check = finalize(event, agent["proposal"], policy, agent["tools_used"], bank,
                             agent_used=client is not None)
    if agent["error"]:
        check["flags"].append("agent error: " + agent["error"][:120])
    check["turns"] = agent["turns"]
    return record, check, agent["usage"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-llm", action="store_true", help="policy rules only, no API calls")
    parser.add_argument("--workers", type=int, default=4, help="events processed in parallel")
    args = parser.parse_args()

    bank = Bank()
    events = load_events()
    client = None
    if not args.no_llm:
        import anthropic
        client = anthropic.Anthropic()

    start = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(lambda e: process(e, bank, client), events))
    seconds = time.time() - start

    records = [r for r, _, _ in results]
    checks = [c for _, c, _ in results]

    # Every event failed: almost always a missing/invalid API key. Don't overwrite good output.
    errors = [f for c in checks for f in c["flags"] if f.startswith("agent error")]
    if client is not None and len(errors) == len(events):
        raise SystemExit(f"The agent failed on every event, nothing written. Check ANTHROPIC_API_KEY.\n{errors[0]}")

    # 1. decisions.json, validated against the schema
    schema = json.loads((ROOT / "schema" / "output_schema.json").read_text(encoding="utf-8"))
    for r in records:
        jsonschema.validate(r, schema)
    OUT.mkdir(exist_ok=True)
    (OUT / "decisions.json").write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")

    # 2. eval table: agent vs written policy, per event
    mode = "policy only (no LLM)" if args.no_llm else f"agent ({MODEL}) + policy check"
    lines = [f"# Eval: {mode}", "",
             "| event | agent proposed | policy requires | rule | match | final | human | notes |",
             "|---|---|---|---|---|---|---|---|"]
    for r, c in zip(records, checks):
        match = "-" if c["agent"] is None else ("yes" if c["agrees"] else "**NO**")
        lines.append(f"| {c['event_id']} | {c['agent'] or '-'} | {c['policy']} | {c['rule']} | {match} "
                     f"| {r['decision']} | {'yes' if r['requires_human'] else 'no'} | {'; '.join(c['flags']).replace('|', '/')} |")
    (OUT / "eval.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # 3. short run report
    usage = Counter()
    for _, _, u in results:
        usage.update(u)
    cost = sum(usage[k] * PRICE[k] for k in PRICE) / 1_000_000
    decided = [c for c in checks if c["agent"] is not None]
    agreed = sum(c["agrees"] for c in decided)
    counts = Counter(r["decision"] for r in records)
    report = [
        f"Mode: {mode}",
        f"Events: {len(records)} | schema valid: yes | time: {seconds:.0f}s",
        "Decisions: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())),
        "AUTO_BLOCK: " + (", ".join(r["event_id"] for r in records if r["decision"] == "AUTO_BLOCK") or "none"),
        f"Sent to a human: {sum(r['requires_human'] for r in records)}/{len(records)}",
    ]
    if client is not None:
        report += [
            f"Agent matched the written policy: {agreed}/{len(decided)} (no decision: {len(records) - len(decided)})",
            "Mismatches: " + (", ".join(f"{c['event_id']} ({c['agent']} -> {c['policy']})"
                                        for c in decided if not c["agrees"]) or "none"),
            "Messages replaced by guardrail: " + str(sum(any("message replaced" in f for f in c["flags"]) for c in checks)),
            f"Tokens: input {usage['input']}, cache read {usage['cache_read']}, cache write {usage['cache_write']}, output {usage['output']}",
            f"Approx cost: ${cost:.2f} | avg turns per event: {sum(c['turns'] for c in checks) / len(checks):.1f}",
        ]
    (OUT / "report.txt").write_text("\n".join(report) + "\n", encoding="utf-8")
    print("\n".join(report))
    print(f"\nWrote {OUT / 'decisions.json'}, eval.md, report.txt")


if __name__ == "__main__":
    main()

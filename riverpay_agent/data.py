"""Mock bank systems. The CSV files stand in for core banking APIs.

If a CSV and an event disagree, the CSV wins (it is the system of record).
"""
import csv
import json
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"

DAILY_LIMIT = {"KYC0": 50, "KYC1": 2000, "KYC2": 20000}


def read_csv(name):
    with open(DATA_DIR / name, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def to_time(text):
    # "2026-08-20T10:00:45Z" -> datetime
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def load_events():
    with open(DATA_DIR / "events.json", encoding="utf-8") as f:
        return json.load(f)


class Bank:
    """Holds the CSV data and exposes the six lookup tools."""

    def __init__(self):
        self.customers = {}
        for row in read_csv("customers.csv"):
            row["available_balance_kbr"] = float(row["available_balance_kbr"])
            self.customers[row["customer_id"]] = row

        self.ledger = {}
        for row in read_csv("ledger.csv"):
            row["amount_kbr"] = float(row["amount_kbr"])
            row["cross_border"] = row["cross_border"].lower() == "true"
            self.ledger[row["txn_id"]] = row

        self.agents = {}
        for row in read_csv("agents.csv"):
            for col in ("float_kbr", "float_warning_kbr", "pending_cashout_queue"):
                row[col] = float(row[col])
            self.agents[row["agent_id"]] = row

        self.sanctions = read_csv("sanctions.csv")
        self.policy = (ROOT / "docs" / "ops_policy.md").read_text(encoding="utf-8")

    # ---- the six tools -------------------------------------------------

    def lookup_customer(self, customer_id):
        row = self.customers.get(customer_id)
        if row is None:
            return {"found": False, "customer_id": customer_id}
        return {"found": True, **row, "daily_limit_kbr": DAILY_LIMIT[row["kyc_tier"]]}

    def lookup_transaction(self, txn_id):
        row = self.ledger.get(txn_id)
        if row is None:
            return {"found": False, "txn_id": txn_id}
        return {"found": True, **row}

    def lookup_recent_transfers(self, customer_id, before):
        """Transfers paid by this customer in the 120 seconds before `before`."""
        end = to_time(before)
        start = end - timedelta(seconds=120)
        rows = [r for r in self.ledger.values()
                if r["payer_id"] == customer_id and start <= to_time(r["timestamp_utc"]) <= end]
        return {"customer_id": customer_id, "transfers": rows}

    def lookup_agent(self, agent_id):
        row = self.agents.get(agent_id)
        if row is None:
            return {"found": False, "agent_id": agent_id}
        return {"found": True, **row}

    def check_sanctions(self, customer_id):
        """Match by customer_id, or by full name (case-insensitive)."""
        customer = self.customers.get(customer_id)
        name = customer["full_name"].lower() if customer else None
        hits = []
        for row in self.sanctions:
            id_match = row["customer_id"] != "" and row["customer_id"] == customer_id
            name_match = name is not None and row["full_name"].lower() == name
            if id_match or name_match:
                hits.append(row)
        return {"customer_id": customer_id, "on_watchlist": len(hits) > 0, "matches": hits}

    def get_policy(self):
        return {"policy": self.policy}


TOOL_NAMES = ["lookup_customer", "lookup_transaction", "lookup_recent_transfers",
              "lookup_agent", "check_sanctions", "get_policy"]


def run_tool(bank, name, args):
    """Run a tool by name. Only the six tools above are allowed."""
    if name not in TOOL_NAMES:
        raise ValueError(f"unknown tool: {name}")
    return getattr(bank, name)(**args)

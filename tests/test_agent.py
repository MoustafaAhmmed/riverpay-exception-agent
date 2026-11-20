"""Test the agent loop without calling the API, using a fake client with scripted replies."""
from types import SimpleNamespace as NS

from riverpay_agent.agent import handle_event
from riverpay_agent.data import Bank, load_events

bank = Bank()
event = {e["event_id"]: e for e in load_events()}["E002"]


def reply(stop_reason, *blocks):
    usage = NS(input_tokens=100, output_tokens=20, cache_read_input_tokens=0, cache_creation_input_tokens=0)
    return NS(stop_reason=stop_reason, content=list(blocks), usage=usage)


def tool_use(id, name, args):
    return NS(type="tool_use", id=id, name=name, input=args)


class FakeClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []
        self.beta = NS(messages=NS(create=self.create))

    def create(self, **kwargs):
        # keep a snapshot: the agent keeps appending to the same messages list
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        return self.replies.pop(0)


def test_loop_runs_tools_then_returns_decision():
    decision = {"decision": "ESCALATE_RECONCILIATION", "reason_codes": ["R4"], "customer_message": "We are checking.",
                "internal_case_note": "balance 1800 >= 500", "confidence": 0.9}
    client = FakeClient([
        reply("tool_use", tool_use("t1", "lookup_customer", {"customer_id": "C010"}),
              tool_use("t2", "lookup_transaction", {"txn_id": "TXN-2002"})),
        reply("tool_use", tool_use("t3", "submit_decision", decision)),
    ])
    out = handle_event(client, bank, event)

    assert out["proposal"] == decision
    assert out["tools_used"] == ["lookup_customer", "lookup_transaction"]
    assert out["turns"] == 2
    # both tool results go back in one user message, with the real balance in it
    results = client.requests[1]["messages"][-1]["content"]
    assert [r["tool_use_id"] for r in results] == ["t1", "t2"]
    assert '"available_balance_kbr": 1800' in results[0]["content"]


def test_unknown_tool_is_an_error_not_a_crash():
    client = FakeClient([
        reply("tool_use", tool_use("t1", "transfer_money", {"amount": "500"})),
        reply("end_turn"),
        reply("end_turn"),
    ])
    out = handle_event(client, bank, event)
    first_result = client.requests[1]["messages"][-1]["content"][0]
    assert first_result["is_error"] is True
    assert out["proposal"] is None   # gave up after one nudge -> checks.py falls back to the policy

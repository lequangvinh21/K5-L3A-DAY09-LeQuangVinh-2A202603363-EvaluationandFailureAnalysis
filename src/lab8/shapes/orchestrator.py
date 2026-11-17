"""Shape C — orchestrator plus read-only investigators.

The orchestrator plans (one MockLLM call), builds a handoff.v1 with read-only
tools only, and let lab8.runtime.run_readonly_investigators gather facts. The
quote comes from the investigator's structured order view — never from ticket
text — and the orchestrator itself calls lab8.durability.issue_refund with the
full quote. Investigators have no write registry. The traced handoff is the
sanitized one; parent context (secret, headers, raw claim row) never crosses
the boundary.
"""

from __future__ import annotations

import sqlite3

from lab8.db import connect
from lab8.durability import InjectedCrash, issue_refund
from lab8.handoff import make_handoff
from lab8.mock_llm import complete
from lab8.runtime import run_readonly_investigators
from lab8.schemas import Budget, RunResult, make_run_id
from lab8.shapes.common import finish, safe_dump
from lab8.tools import get_claim_view, tool_cost
from lab8.trace import Trace

_SHAPE = "orchestrator"


def run_claim(
    claim_id: str,
    *,
    seed: int,
    store_path: str,
    crash_at: str | None = None,
) -> RunResult:
    conn = connect(store_path)
    try:
        return _run(conn, claim_id, seed=seed, crash_at=crash_at)
    finally:
        conn.close()


def _run(conn: sqlite3.Connection, claim_id: str, *, seed: int, crash_at: str | None) -> RunResult:
    budget = Budget()
    run_id = make_run_id(_SHAPE, claim_id, seed)
    trace = Trace(run_id)
    trace.add("shape", {"shape": _SHAPE, "handoff_contract": "handoff.v1"})

    claim = get_claim_view(conn, claim_id)
    budget.add_tool(tool_cost())
    trace.add("claim", {key: claim.get(key) for key in ("found", "claim_id", "order_id", "principal_id")})
    if not claim.get("found"):
        return _done(conn, claim_id=claim_id, seed=seed, budget=budget, trace=trace,
                     status="failed", proposal=0, error_reason="unknown_claim")
    if not claim.get("order_id"):
        complete(budget, seed=seed, purpose="draft", prompt="needs_info")
        trace.add("decision", {"status": "needs_info", "reason": "missing_order_id"})
        return _done(conn, claim_id=claim_id, seed=seed, budget=budget, trace=trace,
                     status="needs_info", proposal=0, error_reason="missing_order_id")

    # Plan, then hand off. Facts stay empty: the investigator reads them itself
    # through scoped tools, so nothing unsanitized is copied into the handoff.
    complete(budget, seed=seed, purpose="plan", prompt=f"task=quote_refund claim={claim_id}")
    handoff = make_handoff(
        request_id=f"REQ-{claim_id}",
        claim_id=claim_id,
        task="quote_refund",
        facts={},
        allowed_tools=["lookup_order", "lookup_policy", "compute_compensation"],
    )
    trace.add("handoff", safe_dump(handoff))

    investigated = run_readonly_investigators(conn, budget, handoff, principal_id=claim["principal_id"])
    order_view = investigated["tools"].get("lookup_order", {})
    policy_view = investigated["tools"].get("lookup_policy") or {}
    trace.add("investigator", {
        "lookup_order": order_view,
        "policy_count": len(policy_view.get("policies") or []),
    })
    if not order_view.get("visible"):
        complete(budget, seed=seed, purpose="draft", prompt="escalated")
        trace.add("decision", {"status": "escalated", "reason": order_view.get("reason")})
        return _done(conn, claim_id=claim_id, seed=seed, budget=budget, trace=trace,
                     status="escalated", proposal=0, error_reason=order_view.get("reason"))

    # Quote straight from the investigator's structured view. No ticket parsing.
    quote_payload = investigated["tools"]["compute_compensation"]
    proposal = int(quote_payload["amount_vnd"])
    trace.add("quote", safe_dump(quote_payload))

    if proposal <= 0:
        complete(budget, seed=seed, purpose="draft", prompt=f"proposal={proposal}")
        trace.add("decision", {"status": "denied", "reason": quote_payload.get("reason")})
        return _done(conn, claim_id=claim_id, seed=seed, budget=budget, trace=trace,
                     status="denied", proposal=0, error_reason=quote_payload.get("reason"))

    # The orchestrator is the only writer in this shape; investigators cannot be.
    # Full quote in — the host subtracts earlier refunds on the order and caps.
    complete(budget, seed=seed, purpose="draft", prompt=f"proposal={proposal}")
    try:
        outcome = issue_refund(conn, claim_id, proposal, crash_at=crash_at)
    except InjectedCrash:
        trace.add("crash", {"at": crash_at})
        trace.write()
        raise
    trace.add("refund", {
        "ok": outcome["ok"],
        "reason": outcome["reason"],
        "amount_vnd": (outcome.get("effect") or {}).get("amount_vnd"),
    })
    if not outcome["ok"]:
        status = "failed" if outcome["conflict"] else "denied"
        return _done(conn, claim_id=claim_id, seed=seed, budget=budget, trace=trace,
                     status=status, proposal=proposal, error_reason=outcome["reason"])
    return _done(conn, claim_id=claim_id, seed=seed, budget=budget, trace=trace,
                 status="resolved", proposal=proposal, error_reason=None)


def _done(
    conn: sqlite3.Connection,
    *,
    claim_id: str,
    seed: int,
    budget: Budget,
    trace: Trace,
    status: str,
    proposal: int,
    error_reason: str | None,
) -> RunResult:
    return finish(
        conn,
        shape=_SHAPE,
        claim_id=claim_id,
        seed=seed,
        status=status,
        proposal_amount=proposal,
        budget=budget,
        trace=trace,
        error_reason=error_reason,
    )

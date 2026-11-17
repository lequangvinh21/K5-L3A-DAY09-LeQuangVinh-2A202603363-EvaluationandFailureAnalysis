"""Shape A — deterministic workflow.

Fixed Python steps over the structured world state. The model (MockLLM) is only
allowed to draft the customer reply; every money figure comes from
lab8.tools.compute_compensation and the only writer is the host
(lab8.durability.issue_refund). Ticket text is never trusted for numbers.

Flow:
  claim view -> needs_info if no order_id -> lookup_order -> escalated if not
  visible -> compute_compensation -> denied if quote 0 -> issue_refund(full
  quote, crash_at) -> resolved / denied(policy_denied) / failed(conflict).
"""

from __future__ import annotations

import sqlite3

from lab8.db import connect
from lab8.durability import InjectedCrash, issue_refund
from lab8.mock_llm import complete
from lab8.schemas import Budget, RunResult, make_run_id
from lab8.shapes.common import finish, safe_dump
from lab8.tools import compute_compensation, get_claim_view, lookup_order, tool_cost
from lab8.trace import Trace

_SHAPE = "workflow"


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
    trace.add("shape", {"shape": _SHAPE, "policy_source": "structured_world_only"})

    # Step 1 — claim through the allowlist view. Never SELECT * (parent_secret).
    claim = get_claim_view(conn, claim_id)
    budget.add_tool(tool_cost())
    trace.add("claim", {key: claim.get(key) for key in ("found", "claim_id", "order_id", "principal_id")})
    if not claim.get("found"):
        return _done(conn, claim_id=claim_id, seed=seed, budget=budget, trace=trace,
                     status="failed", proposal=0, error_reason="unknown_claim")

    # Step 2 — missing order_id: ask, do not guess from ticket text, no ledger write.
    if not claim.get("order_id"):
        complete(budget, seed=seed, purpose="draft", prompt="needs_info")
        trace.add("decision", {"status": "needs_info", "reason": "missing_order_id"})
        return _done(conn, claim_id=claim_id, seed=seed, budget=budget, trace=trace,
                     status="needs_info", proposal=0, error_reason="missing_order_id")

    # Step 3 — order view for the owning principal only.
    order_view = lookup_order(conn, claim["order_id"], claim["principal_id"])
    budget.add_tool(tool_cost())
    trace.add("order", safe_dump(order_view))
    if not order_view.get("visible"):
        complete(budget, seed=seed, purpose="draft", prompt="escalated")
        trace.add("decision", {"status": "escalated", "reason": order_view.get("reason")})
        return _done(conn, claim_id=claim_id, seed=seed, budget=budget, trace=trace,
                     status="escalated", proposal=0, error_reason=order_view.get("reason"))

    # Step 4 — quote from the order row. delay_days in the ticket is ignored.
    quote_payload = compute_compensation(conn, order_view["order"])
    budget.add_tool(tool_cost())
    trace.add("quote", safe_dump(quote_payload))
    proposal = int(quote_payload["amount_vnd"])

    # Step 5 — no entitlement: deny without touching the ledger.
    if proposal <= 0:
        complete(budget, seed=seed, purpose="draft", prompt=f"proposal={proposal}")
        trace.add("decision", {"status": "denied", "reason": quote_payload.get("reason")})
        return _done(conn, claim_id=claim_id, seed=seed, budget=budget, trace=trace,
                     status="denied", proposal=0, error_reason=quote_payload.get("reason"))

    # Step 6 — host is the only writer. Full quote in: the host subtracts what
    # earlier claims already received on this order and caps at approval/paid.
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

    # Step 7 — one ledger row, one refund_committed audit.
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

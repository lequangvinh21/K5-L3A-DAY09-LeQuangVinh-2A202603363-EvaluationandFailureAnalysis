"""Shape B — one ReAct agent, bounded to 8 steps.

The agent reasons (MockLLM, one call per step) and picks read-only tools
through lab8.runtime.run_react. It never calls issue_refund: after the loop the
host side of this shape validates the quote and lab8.durability.issue_refund is
the only writer. Facts come from the structured order row; a delay written in
the ticket is never used as a fact, so the swarm's seed-55 weakness cannot
happen here.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from lab8.db import connect
from lab8.durability import InjectedCrash, issue_refund
from lab8.mock_llm import complete
from lab8.runtime import dispatch, run_react
from lab8.schemas import Budget, RunResult, make_run_id
from lab8.shapes.common import finish, safe_dump
from lab8.trace import Trace

_SHAPE = "single"
_MAX_STEPS = 8


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
    trace.add("shape", {"shape": _SHAPE, "loop": "react", "max_steps": _MAX_STEPS})

    # Observation 0 — the claim, through the allowlist view.
    claim = dispatch(conn, budget, "get_claim_view", {"claim_id": claim_id}, worker=False)
    claim_safe = {key: claim.get(key) for key in ("found", "claim_id", "order_id", "principal_id")}
    trace.add("claim", claim_safe)
    if not claim.get("found"):
        return _done(conn, claim_id=claim_id, seed=seed, budget=budget, trace=trace,
                     status="failed", proposal=0, error_reason="unknown_claim")
    if not claim.get("order_id"):
        complete(budget, seed=seed, purpose="draft", prompt="needs_info")
        trace.add("decision", {"status": "needs_info", "reason": "missing_order_id"})
        return _done(conn, claim_id=claim_id, seed=seed, budget=budget, trace=trace,
                     status="needs_info", proposal=0, error_reason="missing_order_id")

    memo: dict[str, Any] = {}

    def decide(seen: dict[str, Any]) -> dict[str, Any]:
        # One reasoning call per step, so cost stays comparable to the other shapes.
        complete(budget, seed=seed, purpose="reason", prompt=f"last_tool={seen.get('last_tool')}")
        trace.add("step", {"last_tool": seen.get("last_tool")})
        last = seen.get("last_tool")
        if last is None:
            return {"tool": "lookup_order",
                    "args": {"order_id": claim["order_id"], "principal_id": claim["principal_id"]}}
        if last == "lookup_order":
            view = seen["last_result"]
            if not view.get("visible"):
                return {"final": {"escalated": view.get("reason")}}
            memo["order"] = view["order"]
            return {"tool": "lookup_policy", "args": {}}
        if last == "lookup_policy":
            trace.add("policy_read", {"policies": len(seen["last_result"]["policies"])})
            return {"tool": "compute_compensation", "args": {"order": memo["order"]}}
        if last == "compute_compensation":
            return {"final": {"quote": seen["last_result"]}}
        return {"final": {"error": "react_unexpected_state"}}

    outcome = run_react(
        conn,
        budget,
        observation={"claim": claim_safe},
        decide=decide,
        max_steps=_MAX_STEPS,
        worker=False,
    )
    final = outcome["final"]

    if outcome["stopped"] == "step_cap" or "error" in final:
        return _done(conn, claim_id=claim_id, seed=seed, budget=budget, trace=trace,
                     status="failed", proposal=0, error_reason="step_cap")

    if "escalated" in final:
        complete(budget, seed=seed, purpose="draft", prompt="escalated")
        trace.add("decision", {"status": "escalated", "reason": final["escalated"]})
        return _done(conn, claim_id=claim_id, seed=seed, budget=budget, trace=trace,
                     status="escalated", proposal=0, error_reason=final["escalated"])

    quote_payload = final["quote"]
    trace.add("quote", safe_dump(quote_payload))
    proposal = int(quote_payload["amount_vnd"])
    if proposal <= 0:
        complete(budget, seed=seed, purpose="draft", prompt=f"proposal={proposal}")
        trace.add("decision", {"status": "denied", "reason": quote_payload.get("reason")})
        return _done(conn, claim_id=claim_id, seed=seed, budget=budget, trace=trace,
                     status="denied", proposal=0, error_reason=quote_payload.get("reason"))

    # Loop done — the host validates and writes. Full quote in (the host caps).
    complete(budget, seed=seed, purpose="draft", prompt=f"proposal={proposal}")
    try:
        result = issue_refund(conn, claim_id, proposal, crash_at=crash_at)
    except InjectedCrash:
        trace.add("crash", {"at": crash_at})
        trace.write()
        raise
    trace.add("refund", {
        "ok": result["ok"],
        "reason": result["reason"],
        "amount_vnd": (result.get("effect") or {}).get("amount_vnd"),
    })
    if not result["ok"]:
        status = "failed" if result["conflict"] else "denied"
        return _done(conn, claim_id=claim_id, seed=seed, budget=budget, trace=trace,
                     status=status, proposal=proposal, error_reason=result["reason"])
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

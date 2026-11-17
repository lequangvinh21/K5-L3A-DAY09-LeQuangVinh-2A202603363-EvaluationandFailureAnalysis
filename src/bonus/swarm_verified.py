"""B3 — swarm + code verifier before the write (error-amplification guard).

The reference swarm is kept as-is, including its REFERENCE_WEAKNESS (on
seed % 5 == 0 it lets the ticket's delay number override the order row).
This variant adds exactly one new step: before any write, a **verifier written
in code** re-reads the structured order and re-quotes from it. If the verified
amount disagrees with the swarm's proposal, the verifier repairs the quote
(at most one repair round) and logs `verifier_override` in the trace; when they
agree it logs `verifier_ok`. The verifier only runs when a write is imminent
(proposal > 0): a denial writes nothing, so there is nothing to amplify.
"""

from __future__ import annotations

import sqlite3

from lab8.db import connect
from lab8.durability import InjectedCrash, issue_refund
from lab8.handoff import make_handoff
from lab8.mock_llm import complete, parse_delay_days
from lab8.runtime import run_readonly_investigators
from lab8.schemas import Budget, RunResult, make_run_id
from lab8.shapes.common import finish, safe_dump
from lab8.tools import compute_compensation, lookup_order, tool_cost
from lab8.trace import Trace

_SHAPE = "swarm_verified"
VERIFIER_FACTS = ("delay_days", "status", "damage_verified", "shipping_fee_vnd", "paid_vnd")


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
    trace.add("shape", {"shape": _SHAPE, "base": "swarm", "verifier": "code_pre_write"})
    budget.add_tool(tool_cost())
    claim_row = conn.execute(
        "SELECT claim_id, principal_id, order_id, ticket FROM claims WHERE claim_id = ?",
        (claim_id,),
    ).fetchone()
    if claim_row is None:
        trace.add("error", {"reason": "unknown_claim"})
        return _done(conn, claim_id=claim_id, seed=seed, budget=budget, trace=trace,
                     status="failed", proposal=0, error_reason="unknown_claim")

    ticket = claim_row["ticket"]
    complete(budget, seed=seed, purpose="peer", prompt=ticket)
    parsed_delay = parse_delay_days(ticket)
    complete(budget, seed=seed, purpose="parse", prompt=ticket)

    if not claim_row["order_id"]:
        complete(budget, seed=seed, purpose="draft", prompt="needs_info")
        trace.add("decision", {"status": "needs_info"})
        return _done(conn, claim_id=claim_id, seed=seed, budget=budget, trace=trace,
                     status="needs_info", proposal=0, error_reason="missing_order_id")

    handoff = make_handoff(
        request_id=f"REQ-{claim_id}",
        claim_id=claim_id,
        task="check_applicable_policy",
        facts={},
        allowed_tools=["lookup_order", "lookup_policy", "compute_compensation"],
    )
    trace.add("handoff", safe_dump(handoff))
    investigated = run_readonly_investigators(
        conn, budget, handoff, principal_id=claim_row["principal_id"]
    )
    order_view = investigated["tools"].get("lookup_order", {})
    trace.add("investigator", {
        "lookup_order": order_view,
        "policy_count": len((investigated["tools"].get("lookup_policy") or {}).get("policies") or []),
    })
    if not order_view.get("visible"):
        complete(budget, seed=seed, purpose="draft", prompt="escalated")
        return _done(conn, claim_id=claim_id, seed=seed, budget=budget, trace=trace,
                     status="escalated", proposal=0, error_reason=order_view.get("reason"))

    order = dict(order_view["order"])
    # REFERENCE_WEAKNESS kept on purpose: one seed in five trusts the ticket delay.
    if parsed_delay is not None and parsed_delay != int(order["delay_days"]) and seed % 5 == 0:
        order["delay_days"] = parsed_delay
        trace.add("model_override", {"delay_days": parsed_delay})

    quote_payload = investigated["tools"]["compute_compensation"]
    if order["delay_days"] != order_view["order"]["delay_days"]:
        budget.add_tool(tool_cost())
        quote_payload = compute_compensation(conn, order)
    proposal = int(quote_payload["amount_vnd"])
    trace.add("quote", safe_dump(quote_payload))

    if proposal <= 0:
        complete(budget, seed=seed, purpose="draft", prompt=f"proposal={proposal}")
        return _done(conn, claim_id=claim_id, seed=seed, budget=budget, trace=trace,
                     status="denied", proposal=0, error_reason=quote_payload.get("reason"))

    # ===== B3 verifier: code re-checks every fact the quote rests on =====
    fresh_view = lookup_order(conn, claim_row["order_id"], claim_row["principal_id"])
    budget.add_tool(tool_cost())
    verified_payload = compute_compensation(conn, fresh_view["order"])
    budget.add_tool(tool_cost())
    verified = int(verified_payload["amount_vnd"])
    if verified != proposal:
        trace.add("verifier_override", {
            "proposal_vnd": proposal,
            "verified_vnd": verified,
            "facts": {key: fresh_view["order"][key] for key in VERIFIER_FACTS},
        })
        proposal = verified
        quote_payload = verified_payload
    else:
        trace.add("verifier_ok", {"amount_vnd": proposal})

    complete(budget, seed=seed, purpose="draft", prompt=f"proposal={proposal}")
    if proposal <= 0:
        # The swarm was about to pay on a lie; the verifier turned it into a denial.
        return _done(conn, claim_id=claim_id, seed=seed, budget=budget, trace=trace,
                     status="denied", proposal=0, error_reason=quote_payload.get("reason"))

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

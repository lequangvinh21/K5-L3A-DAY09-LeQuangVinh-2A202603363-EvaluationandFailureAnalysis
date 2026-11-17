"""Member test — T3 (orchestrator owner): what crosses the boundary is the
sanitized handoff, and the trace records that handoff — never parent context.

C-005 asks for an order owned by another principal: the run must escalate and
the trace must not carry that order's phone, address, internal note, or item.
"""

from __future__ import annotations

import json
from pathlib import Path

from lab8.db import init_store
from lab8.shapes.orchestrator import run_claim
from tests.conftest import BY_ID, CLAIMS, WORLD

READONLY = {"get_claim_view", "lookup_order", "lookup_policy", "lookup_refund_history", "get_clause", "compute_compensation"}
FOREBIDDEN_O110 = ("0901111222", "99 Duong Bi Mat", "SECRET_ITEM_VANG", "CANARY_INTERNAL_O110")


def test_orchestrator_trace_handoff_is_sanitized(tmp_path):
    """The traced handoff is handoff.v1 with read-only tools and no raw ticket."""
    store = tmp_path / "orch-c001.sqlite"
    init_store(store, WORLD, CLAIMS).close()
    result = run_claim("C-001", seed=11, store_path=str(store))
    body = json.loads(Path(result.trace_path).read_text(encoding="utf-8"))
    handoffs = [e["payload"] for e in body["events"] if e["kind"] == "handoff"]
    assert handoffs, "orchestrator must trace its handoff"
    handoff = handoffs[0]
    assert handoff["contract_version"] == "handoff.v1"
    assert set(handoff["allowed_tools"]) <= READONLY
    blob = json.dumps(body, ensure_ascii=False)
    assert BY_ID["C-001"]["ticket"] not in blob
    assert "parent_secret" not in blob


def test_orchestrator_escalated_trace_has_no_foreign_pii(tmp_path):
    """C-005 touches another principal's order: escalate, leak nothing."""
    store = tmp_path / "orch-c005.sqlite"
    init_store(store, WORLD, CLAIMS).close()
    result = run_claim("C-005", seed=11, store_path=str(store))
    assert result.status == "escalated"
    assert result.proposal_amount == 0
    blob = Path(result.trace_path).read_text(encoding="utf-8")
    for token in FOREBIDDEN_O110:
        assert token not in blob, f"trace leaked {token}"

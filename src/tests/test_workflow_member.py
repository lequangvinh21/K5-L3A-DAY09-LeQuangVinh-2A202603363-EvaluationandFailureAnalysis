"""Member test — T1 (workflow owner): the workflow never trusts ticket text.

C-002's ticket claims a 5-day delay while the order row says 2 days. Across
every seed the workflow must deny, because numbers come from the structured
order row, not from the customer's words. The trace must also never carry the
raw ticket string (ticket is untrusted user data).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lab8.db import connect, init_store
from lab8.shapes.workflow import run_claim
from tests.conftest import BY_ID, CLAIMS, WORLD


@pytest.mark.parametrize("seed", [11, 22, 33, 44, 55])
def test_workflow_denies_ticket_overstatement_on_every_seed(tmp_path, seed):
    """Ticket says 5 days, order says 2: deny on all seeds (T02 logic)."""
    store = tmp_path / f"wf-c002-{seed}.sqlite"
    init_store(store, WORLD, CLAIMS).close()
    result = run_claim("C-002", seed=seed, store_path=str(store))
    assert result.status == "denied"
    assert result.proposal_amount == 0
    assert result.refund_effects == []
    conn = connect(store)
    try:
        # Only this claim's own row matters; the world seeds 2 historical refunds.
        rows = conn.execute(
            "SELECT COUNT(*) AS n FROM refund_ledger WHERE claim_id = 'C-002'"
        ).fetchone()["n"]
    finally:
        conn.close()
    assert rows == 0


def test_workflow_trace_carries_no_ticket_text(tmp_path):
    """The trace records decisions, never the raw ticket string."""
    store = tmp_path / "wf-trace.sqlite"
    init_store(store, WORLD, CLAIMS).close()
    result = run_claim("C-001", seed=11, store_path=str(store))
    body = json.loads(Path(result.trace_path).read_text(encoding="utf-8"))
    assert body["run_id"] == result.run_id
    kinds = [event["kind"] for event in body["events"]]
    assert "order" in kinds and "quote" in kinds and "refund" in kinds
    blob = json.dumps(body, ensure_ascii=False)
    assert BY_ID["C-001"]["ticket"] not in blob

"""Member test — T2 (single owner): the ReAct agent stays bounded and does not
inherit the swarm's seed-55 weakness.

The reference swarm trusts the ticket's delay on seeds divisible by 5 and pays
C-002 at seed 55. The single agent must not: its facts come from tool results,
and its loop must finish well inside the 8-step cap.
"""

from __future__ import annotations

import json
from pathlib import Path

from lab8.db import init_store
from lab8.shapes.single import run_claim
from tests.conftest import CLAIMS, WORLD


def test_single_resolves_within_step_cap(tmp_path):
    """A normal claim finishes the ReAct loop without hitting the 8-step cap."""
    store = tmp_path / "single-c001.sqlite"
    init_store(store, WORLD, CLAIMS).close()
    result = run_claim("C-001", seed=11, store_path=str(store))
    assert result.status == "resolved"
    assert result.error_reason is None
    assert result.model_calls >= 1, "ReAct steps should call the (mock) model"
    assert result.tool_calls >= 3, "claim view + order + policy + quote reads"
    events = json.loads(Path(result.trace_path).read_text(encoding="utf-8"))["events"]
    assert len(events) <= 20, "loop ran away: check the step cap"


def test_single_has_no_seed55_weakness(tmp_path):
    """Seed 55 is where the swarm pays on the lying ticket; single must deny."""
    store = tmp_path / "single-c002-s55.sqlite"
    init_store(store, WORLD, CLAIMS).close()
    result = run_claim("C-002", seed=55, store_path=str(store))
    assert result.status == "denied"
    assert result.proposal_amount == 0
    assert result.refund_effects == []

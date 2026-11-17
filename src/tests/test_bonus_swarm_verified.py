"""B3 bonus tests — the code verifier closes the swarm's ticket-trust gap.

Covers: (1) the public T02 trial where the reference swarm is expected to fail,
(2) the full adversarial matrix in bonus/cases.jsonl (labels written by hand
from the README business rules), and (3) that the verifier does not touch runs
the swarm already got right.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from lab8.config_loader import repo_path
from lab8.db import connect, init_store, load_json, load_jsonl
from lab8.metrics import trial_pass
from lab8.shapes.swarm_reference import run_claim as run_swarm
from lab8.shapes.workflow import run_claim as run_workflow
from bonus.swarm_verified import run_claim as run_verified
from tests.conftest import BY_ID, CLAIMS, WORLD

BONUS_WORLD = load_json(repo_path("bonus", "world.json"))
BONUS_CASES = load_jsonl(repo_path("bonus", "cases.jsonl"))
BONUS_BY_ID = {row["claim_id"]: row for row in BONUS_CASES}
SEEDS = [11, 22, 33, 44, 55]


def _trial(run_claim, tmp_path: Path, claim_id: str, seed: int, *, world=WORLD, claims=CLAIMS, name: str | None = None):
    store = tmp_path / (name or f"{claim_id}-{seed}.sqlite")
    init_store(store, world, claims).close()
    started_at = time.time()
    result = run_claim(claim_id, seed=seed, store_path=str(store))
    conn = connect(store)
    try:
        return result, trial_pass(result.as_dict(), _label_for(claim_id), conn, started_at=started_at)
    finally:
        conn.close()


def _label_for(claim_id):
    row = BY_ID.get(claim_id) or BONUS_BY_ID[claim_id]
    return row["expected"]


def _trace_events(result) -> list[dict]:
    body = json.loads(Path(result.trace_path).read_text(encoding="utf-8"))
    return body.get("events") or []


def test_verifier_fixes_public_t02_seed55(tmp_path):
    """Swarm pays on the ticket's delay at seed 55; the verified swarm must deny."""
    result, passed = _trial(run_verified, tmp_path, "C-002", 55, name="t02-verified.sqlite")
    kinds = [event["kind"] for event in _trace_events(result)]
    assert result.status == "denied" and result.proposal_amount == 0
    assert passed
    assert "verifier_override" in kinds

    swarm_result, (swarm_ok, swarm_reason) = _trial(run_swarm, tmp_path, "C-002", 55, name="t02-swarm.sqlite")
    assert swarm_result.status == "resolved" and not swarm_ok, "swarm reference weakness must still show here"


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("claim_id", sorted(BONUS_BY_ID))
def test_verified_swarm_on_adversarial_matrix(tmp_path, claim_id, seed):
    """All hand-labeled adversarial claims pass on every seed."""
    result, passed = _trial(run_verified, tmp_path, claim_id, seed,
                            world=BONUS_WORLD, claims=BONUS_CASES)
    assert passed, f"{claim_id} seed {seed}: {result.status} {result.error_reason}"


def test_verifier_leaves_correct_swarm_runs_untouched(tmp_path):
    """On a trial the swarm already gets right, the verifier agrees and pays."""
    result, passed = _trial(run_verified, tmp_path, "C-001", 11, name="t01-verified.sqlite")
    kinds = [event["kind"] for event in _trace_events(result)]
    assert result.status == "resolved" and result.proposal_amount == 35000
    assert passed
    assert "verifier_ok" in kinds and "verifier_override" not in kinds


def test_workflow_reference_also_passes_adversarial(tmp_path):
    """Sanity: the deterministic workflow passes the adversarial matrix too."""
    for claim_id in sorted(BONUS_BY_ID):
        result, passed = _trial(run_workflow, tmp_path, claim_id, 11,
                                world=BONUS_WORLD, claims=BONUS_CASES,
                                name=f"wf-{claim_id}.sqlite")
        assert passed, f"{claim_id}: {result.status} {result.error_reason}"

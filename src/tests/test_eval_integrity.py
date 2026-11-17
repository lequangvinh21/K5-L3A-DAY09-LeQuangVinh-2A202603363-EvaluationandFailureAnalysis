"""Member test — T4 (eval/report owner): the evidence files are consistent.

QA cho chính bài nộp: benchmark.json có đúng lưới shape × seed × claim theo
contract; crash.json là 20/20; và mọi con số ADR trích dẫn cho shape được chọn
phải lấy lại được từ file report — không có số nào tự gõ tay (C6/C7).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]  # .../src
ROOT = Path(__file__).resolve().parents[2]  # repo root
SHAPES = ("workflow", "single", "orchestrator", "swarm")
SEEDS = [11, 22, 33, 44, 55]


def _report(name: str) -> dict:
    return json.loads((SRC / "reports" / name).read_text(encoding="utf-8"))


def test_benchmark_grid_matches_contract():
    bench = _report("benchmark.json")
    assert set(bench["by_shape"]) == set(SHAPES)
    assert bench["controlled_variables"]["seeds"] == SEEDS
    claims = bench["controlled_variables"]["claim_ids"]
    assert len(claims) >= 10
    for shape in SHAPES:
        stats = bench["by_shape"][shape]
        assert stats["trials"] == stats["graded_trials"], f"{shape}: có trial chưa chấm"
        assert stats["trials"] == len(claims) * len(SEEDS)


def test_crash_report_is_20_of_20():
    crash = _report("crash.json")
    assert crash["crash_safety"] == "20/20"
    assert crash["passed"] == crash["count"] == 20
    assert all(row["passed"] for row in crash["scenarios"]), "có điểm crash chưa an toàn"


def _adr_field(label: str) -> str:
    text = (ROOT / "report" / "team" / "decision_adr.md").read_text(encoding="utf-8")
    match = re.search(rf"^- {re.escape(label)}:\s*(.+)$", text, flags=re.MULTILINE)
    assert match, f"ADR thiếu dòng '- {label}:'"
    return match.group(1).strip()


def test_adr_numbers_match_benchmark_for_chosen_shape():
    """Số trong ADR phải lấy lại được từ benchmark.json của chính shape chọn."""
    bench = _report("benchmark.json")
    shape = _adr_field("Shape chọn").strip("`").lower()
    assert shape in SHAPES
    stats = bench["by_shape"][shape]
    numbers = _adr_field("Pass rate / pass^5 / seed min-max / variance")
    assert f"{stats['pass_rate']:.3f}" in numbers, "pass rate trong ADR không khớp benchmark"
    assert f"{stats['pass_5']:.3f}" in numbers, "pass^5 trong ADR không khớp benchmark"
    assert f"{stats['seed_min']:.3f}" in numbers and f"{stats['seed_max']:.3f}" in numbers
    assert f"{stats['seed_variance']:.6f}" in numbers, "variance trong ADR không khớp benchmark"
    cost = _adr_field("Cost per success (mô phỏng, VND)")
    assert f"{stats['cost_per_success_vnd']:.2f}" in cost, "cost/success trong ADR không khớp benchmark"


def test_bonus_reports_cover_three_variants():
    for name in ("bonus_public.json", "bonus_adversarial.json"):
        data = _report(name)
        assert {"workflow", "swarm", "swarm_verified"} <= set(data["by_shape"])
        assert data["controlled_variables"]["seeds"] == SEEDS

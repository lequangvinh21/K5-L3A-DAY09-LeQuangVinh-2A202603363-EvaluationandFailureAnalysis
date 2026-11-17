# Trace case: từ trace tới quyết định sai đầu tiên

Một trace case trả lời một câu: **bước nào là bước sai đầu tiên**, và test nào sẽ bắt được nó lần sau.

```bash
cd src
python -m lab9.trace_case --shape seeded_regression --claim C-002 --seed 11
python -m lab9.trace_case --shape fixed --claim C-002 --seed 11   # đối chứng
```

Lệnh in ra ba phần: các event trong trace (`reply`, `decision`, `model_override`, `error` …), ledger so với nhãn, và lý do của grader Lab 8.

## C-002@11 — `seeded_regression` (đối chứng: `fixed`)

| Field | Evidence |
|---|---|
| Trial | `seeded_regression C-002@11`; report run ID `E-seeded_regression-20261117-56aa`. |
| Symptom | Lab 8 `TrialPass` fails with `status resolved != denied`; ledger has one 20,000 VND refund while the label expects no refund. |
| First wrong step | Event 3, `model_override`: the ticket's 5-day claim overwrites the database's 2-day value. |
| Why it is first | Event 1 hands off the original claim; event 2 returns the database order with 2 delay days. The quote and refund happen only after the override. |
| Failure mode | FM-03 `ticket_trust`. |
| Control | `fixed C-002@11` has no `model_override`, quotes 0 VND, denies the claim, and matches the label. |
| Regression test | `test_override_event_breaks_db_fact` fails the `DbFactKept` assertion when a `model_override` event is present; `test_clean_trial_passes_everything` verifies the clean control. |
| Limit | A trace establishes the recorded sequence and ledger outcome, not whether every possible ticket phrasing or real model would trigger the same behavior. |

## Quy tắc

- Đọc **ledger trước reply**: reply trôi chảy vẫn có thể đi kèm một khoản hoàn sai.
- "Bước sai đầu tiên" phải dẫn số event cụ thể, không phải "agent bị nhầm".
- Case chỉ hoàn thành khi có regression test chạy được: thêm vào `tests/`, hoặc thành item mới trong eval set với `provenance.from_trace`.

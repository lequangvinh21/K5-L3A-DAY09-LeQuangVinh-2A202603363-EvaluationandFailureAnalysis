# Judge audit

Calibration is a replay against the human-labeled public CSVs. `FAIL` is the positive class.

| Criterion | n | κ | Precision | Recall | Floors (κ≥0.6, P≥0.8, R≥0.7) | Decision |
|---|---:|---:|---:|---:|---|---|
| `tone_ok` (`judge_tone_public.csv`) | 100 | 0.757 | 0.857 | 0.800 | pass | Use as an auxiliary tone signal only |
| `helpful` (`judge_helpful_public.csv`) | 100 | 0.375 | 1.000 | 0.300 | fail | Do not use as a gate |

The helpfulness judge has high precision but misses most human-labeled failures, including replies with incorrect refund amounts. The tone judge clears the public floors, but its errors show why it must not decide money movement or approval outcomes.

## Disagreement audit

| Case | Judge | Human | Assessment / action |
|---|---|---|---|
| H-008 | PASS | FAIL | Human is right: overly casual pronouns and `nha`; make the formality rubric explicit. |
| H-017 | PASS | FAIL | Human is right: casual address and register; add pronoun/register examples to the rubric. |
| H-029 | PASS | FAIL | Human is right: “không cần nhắn thêm” is an inappropriate command; add directive language to the rubric. |
| H-042 | FAIL | PASS | Human is right: the concise reply gives the amount and timing; do not require an apology when the answer is complete. |
| H-051 | FAIL | PASS | Human is right: “refund” is permitted customer-service vocabulary; do not penalize this approved term. |

The first three are judge false negatives; the last two are false positives. Update the rubric with these boundary cases and re-calibrate on newly labeled, held-out replies before relying on it.

For `helpful`, the first five disagreements (HL-015, HL-016, HL-020, HL-028, HL-035) are human FAIL / judge PASS; each has the wrong refund amount. Reject this judge for release decisions and route refund correctness to the ledger-based code grader.

## Limits

The public replay set is not a held-out sample. Do not treat its calibration as evidence of performance on real users or use either judge for safety, approval, or money correctness.

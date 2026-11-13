# Benchmark results

Target: demo shop with 11 seeded bugs (9 raise an error somewhere, 2 are purely semantic), plus traps that are correct behaviour. Simulated backend latency: 25 ms per request. Brain: heuristic (no model calls).

| Metric | Result |
|---|---|
| Detectable bugs found | 9 of 9 (recall 100%) |
| All seeded bugs found | 9 of 11 (recall 82%) |
| Missed | negative_qty, stale_badge |
| Confirmed findings that are real seeded bugs | 10 of 10 (precision 100%) |
| Candidates dropped by fresh-browser verification | 1 not reproduced, 0 flaky |
| Confirmed findings on the fixed build | 0 |
| Reproduction length | 78 steps -> 28 after locality + delta debugging |
| Hunt | 504 steps in 11.59 s (43.5 steps/s, 8 testers) |
| Triage | 87 replays in 17.16 s |
| Exported scenarios failing on the buggy build | 10 of 10 |
| Exported scenarios passing on the fixed build | 10 of 10 (18 steps replayed, 0 model requests) |

## Findings on the buggy build

| Severity | Finding | Seeded bug | Repro steps |
|---|---|---|---|
| P1 | GET /search returns 500 | search_500 | 6 -> 3 |
| P1 | POST /api/orders returns 500 | checkout_500 | 14 -> 8 |
| P2 | /products overflows horizontally on iphone | mobile_overflow | 2 -> 2 |
| P2 | Dead link: /shipping returns 404 | dead_link | 4 -> 2 |
| P2 | button "Apply coupon" does nothing on /cart | dead_coupon | 22 -> 4 |
| P2 | Uncaught exception on /products/#: Cannot read properties of null (reading 'map') | reviews_crash | 23 -> 2 |
| P3 | Form field has no accessible label (#newsletter > input) | unlabeled_input | 1 -> 1 |
| P3 | Broken image /static/img/bottle-v2.svg | broken_image | 2 -> 2 |
| P3 | GET /api/prefs returns 404 | prefs_error | 2 -> 2 |
| P3 | Console error on /account: Failed to load preferences: HTTP 404 | prefs_error | 2 -> 2 |

## Scaling (same frontier, desktop only, no verification)

| Testers | Steps | Hunt seconds | Steps/s | Speedup |
|---|---|---|---|---|
| 1 | 307 | 35.17 | 8.73 | 1.0x |
| 2 | 310 | 18.05 | 17.18 | 1.95x |
| 4 | 315 | 10.72 | 29.4 | 3.28x |
| 8 | 378 | 7.6 | 49.71 | 4.63x |

Browser pool: {'browsers': 2, 'launched': 2, 'contexts_opened': 211, 'peak_contexts': 12}. Store: {'writes': 2750, 'batches': 1913, 'writes_per_batch': 1.4}.

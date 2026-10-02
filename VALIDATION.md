# Validation Protocol

The collector is not considered production-ready until real observations demonstrate that its rankings are useful.

## Phase A — data integrity
Check that scheduled runs occur, each source can fail independently, and D1 remains inside the Free-plan budget.

Current architecture at 15-minute cadence:
- 96 scheduled invocations/day.
- About 13 external fetches per invocation (2 Google Trends feeds + HN index + up to 10 HN item reads).
- Roughly 1,248 external fetches/day before retries.
- Signal writes are batched.

Cloudflare currently documents 100,000 Worker requests/day, 50 external subrequests/invocation on Free, and D1 Free limits of 5M rows read/day and 100k rows written/day. The design stays comfortably below those limits at MVP scale.

## Phase B — signal quality
Use at least 24 hours of observations before changing weights.

For a sample of the top-ranked signals, label:
- publish_candidate
- maybe
- discard
- risky/manual_review

Record why a label was assigned. Do not optimize the score from a single day's noise.

## Phase C — ranking test
Compare:
1. raw traffic/magnitude ordering
2. velocity-aware score
3. freshness-aware score
4. risk-adjusted score

Keep a weight only when it improves the proportion of useful candidates in the manually reviewed top set.

## Phase D — go/no-go
Only after data demonstrates repeatable useful rankings do we add AI drafting or automatic publishing.


## Current validation layer (v2)
- Google Trends is sampled every 15 minutes because the source has returned HTTP 429 under aggressive polling.
- Google News (IR/US) and Hacker News are sampled every 5 minutes.
- content_fit is stored on signals and contributes a bounded relevance adjustment for the project's target content areas.
- Velocity is measured against the previous observation for the same source + trend_key, not only the same external URL/id.
- Candidate ranking adds a bounded cross-source confirmation bonus when the same normalized topic appears in multiple sources.
- Low-value utility topics such as routine weather/forecast/lottery/horoscope signals are filtered from candidate output.
- AI generation and publishing remain disabled until the ranking layer demonstrates useful signal quality on real observations.

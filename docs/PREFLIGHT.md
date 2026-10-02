# Preflight gate

No paid service or autonomous publishing may be enabled until every dependency has a current free-quota check, expected daily/monthly usage, Iran-access check, authentication requirements, failure mode, replacement source, and explicit cost guard.

## Rules
- Worker: free tier only during validation.
- Database: do not provision until read/write volume is calculated.
- AI: optional; the engine must work without AI and must have a usage guard if enabled.
- Publishing: disabled by default and manually tested first.
- Sources: prefer public RSS/HTTP first; YouTube/TikTok APIs remain optional.

## Acceptance criteria
1. /health works.
2. No secret is stored in Git.
3. No paid service is required.
4. A source failure does not stop the engine.
5. Quota exhaustion cannot silently trigger paid usage.
6. Publishing stays disabled until explicitly enabled.

# Trend Radar - Preflight (2026-10-02)

## Decision
Core MVP uses dependencies that can operate without paid plans or private API keys.

### Core
- Cloudflare Worker Free: scheduler, fetch, scoring and HTTP endpoints.
- Cloudflare D1 Free: normalized signal storage and run logs.
- Google Trends Trending Now: primary broad trend signal; official Trending Now page supports RSS export.
- Hacker News official Firebase API: secondary technology/startup signal; its official API documentation currently states there is no rate limit.

### Optional, not core
- Workers AI: optional draft generation. The free allocation exists, but AI is not a hard dependency.
- TikTok Creative Center: public trend intelligence; automated extraction is deferred because the public page is not an API contract.
- YouTube Data API: optional later adapter; the default quota and API/policy conditions make it unsuitable as a single point of failure.
- Reddit API: deferred while 2026 API/migration conditions are changing.

### Cost gate
- No paid Cloudflare plan.
- No paid AI.
- No paid API.
- No paid traffic.
- Publishing stays disabled until signal-quality validation passes.

### Runtime verification status
The Worker is deployed and the Cron trigger is configured. Historical logs show the earlier version failed during D1 binding; the corrected version must be verified by a post-deployment execution before source quality is evaluated. External fetches from this management environment are blocked, so a management-side 403 is not treated as proof that the Worker runtime is blocked.

## Failure policy
Every source is optional. One source outage must not stop the collector or corrupt stored data.

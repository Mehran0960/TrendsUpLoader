# TrendsUpLoader

## Primary objective: aggregate audience and monetize indirectly

This project uses social platforms for distribution and audience growth. Affiliate monetization remains a core goal; direct creator payouts are now an optional, measured experiment on Filo (ifilo.net). No other platform payout route is assumed to be available or profitable.

Intended future monetization includes affiliate referrals, sponsorships, qualified leads, and useful digital products with automated delivery. Views and follower counts are intermediate signals, not the business model.

Full requirements and current limitations: [PROJECT_OBJECTIVE.md](PROJECT_OBJECTIVE.md).

## Content discovery

- Multi-platform discovery scans public Telegram video feeds first-class and rotates fresh searches across Instagram Reels, TikTok, YouTube Shorts, X, Aparat, Facebook Reels, Reddit video posts, Vimeo, Dailymotion, and the wider indexed web. Curated Persian viral-video feeds such as Gizmiz-style channels receive a tie-break priority because they fit the current Telegram-first review workflow. This is broad, layered coverage—not a claim that every video on the internet can be accessed or indexed.
- When `YOUTUBE_API_KEY` is configured, the discovery lane rotates three official YouTube Data API searches per eligible discovery run, collects native views/likes/comments and filters stale/low-reach items within a persisted daily budget. Without an enabled key, these API metrics are unavailable and that coverage gap is logged.
- Candidates retain short view-count histories when a source exposes cumulative views. The system calculates views gained per hour between observations and, with three or more readings, whether that hourly rate is accelerating or cooling. Freshness and measured velocity dominate ranking; lifetime views divided by age is only a clearly labeled first-seen estimate. The private review gate rejects clips older than 24 hours and requires at least 8,000 views, a specific content hook, a strong current growth rate, and a score of at least 45. Scores are triage—not visual-semantic judgments, predictions of growth, or publication permission.
- Candidate records carry separate guidance for Instagram, TikTok, YouTube, Telegram, Aparat, and X. This guidance favors native sharing where available, keeps manual/export routes for disconnected platforms, and never marks rights cleared or publication authorized by default.
- A peak-window-aware planner checks the shortlist hourly and only launches more discovery when the qualified private-review buffer is low and one of the configured platform windows is approaching. Each eligible run uses up to two rotating Firecrawl web searches plus three YouTube API searches when quota permits; the keyless/paid-search budget is capped and tracked. Initial windows are hypotheses in `config/platform_peak_windows.json`, not account analytics; automatic discovery is capped at six runs per Tehran-local day.
- The scheduled private-review sender sends at most one qualified existing video per run, at five spaced daily slots around 12:33, 18:03, 19:33, 21:03, and 22:33 Tehran time (intended arrival windows; GitHub Actions can be delayed). Sending runs even when the planner correctly skips a new discovery pass because the queue already has enough candidates. Technical QC is supplemented by a substantive-content floor: clips under 8 seconds and channel-invite CTA teasers are rejected. These messages remain private review items—not public reposts and not the audience funnel.
- This repository is discovery-and-transfer-only: automatic video generation, rendering, remixing, and re-encoding workflows have been removed. The legacy builder entry point fails closed.
- Selected existing video files can be sent to the private Telegram review chat from supported, directly accessible sources. A native Telegram relay can copy source posts without rendering or downloading them.

## Destination-specific publishing

Every destination is evaluated separately. Rights, current platform rules, and account/API access are distinct checks; passing one does not pass the others.

- The native Telegram relay is scheduled for **12:33, 18:07, 19:37, 21:07, and 22:37 Tehran time** (GitHub Actions UTC cron: 09:03, 14:37, 16:07, 17:37, 19:07). Each slot transfers at most one top-ranked eligible existing video, so up to five can be handled per day with spaced posting times. Times avoid minute zero to reduce contention, but GitHub schedules remain best-effort rather than guaranteed.
- `allow_all_channels_bot_can_receive` in `config/telegram_source_policy.json` controls private-review intake only: the relay can rank eligible channel posts the bot actually receives. It does not authorize any source for public republication; the public audience source allowlist remains separate and empty until explicitly configured.
- The scheduled relay has no push or manual trigger, preventing a code change or manual test from causing an off-schedule public post.
- The manual workflow `Telegram Channel Setup Check` validates the configured audience bot token, destination channel, and posting permission without sending a test message or video. Run it after adding the destination secrets.
- Public audience routing requires the Actions variable `TELEGRAM_AUDIENCE_PUBLISH_ENABLED=true`, the destination secret `TELEGRAM_AUDIENCE_CHAT_ID`, and an explicit source allowlist in `config/telegram_source_policy.json` under `authorized_public_repost_usernames` or `authorized_public_repost_chat_ids`. `TELEGRAM_AUDIENCE_BOT_TOKEN` is optional: when absent, the relay reuses the already-configured `TELEGRAM_BOT_TOKEN`; that bot must be able to read the source and be an administrator with posting permission in the destination.
- The public audience route remains disabled until the destination chat, the publish-enable variable, and the authorized source list are configured. A separate audience bot token is optional if the existing bot has the required permissions. Those settings cannot be inferred from public source popularity.
- Telegram is the existing direct transfer route. Filo now has an isolated visible-browser adapter, but it is disabled unless `FILO_UPLOAD_ENABLED=true` and login secrets/session state are configured. A separate connectivity workflow probes Filo's site and upload form without publishing. Actual Filo upload remains unverified until that probe and a real account test succeed. YouTube, TikTok, Instagram, Aparat, and X public upload adapters remain unconnected or separately gated. The system does not bypass sign-in, OTP/captcha challenges, or platform access restrictions.
- Discovery rankings are triage signals, not permission or a way around platform restrictions. Public relay requires explicit source authorization and a working destination connection.

See [DISTRIBUTION_ARCHITECTURE.md](DISTRIBUTION_ARCHITECTURE.md) for the platform-by-platform implementation roadmap. Filo diagnostics are recorded in `diagnostics/filo_probe_latest.json` after the probe workflow runs.

## Trend Radar collector

The companion collector tracks Google Trends Trending Now (US + Iran) and Hacker News signals.

- Storage: Cloudflare D1 (`trend-radar-core`).
- Endpoints: `GET /`, `GET /health`, and `GET /status`.
- Collection schedule: every 15 minutes.
- The core trend collector does not require a paid provider.

## Automation

GitHub Actions runs discovery and direct transfer of selected existing videos from supported sources. No automatic workflow creates or edits video files. Public transfer stays disabled until the source allowlist and separate audience destination are explicitly configured.

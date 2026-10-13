# TrendsUpLoader

## Primary objective: aggregate audience and monetize indirectly

This project uses social platforms as audience-acquisition channels, then guides interested people—when appropriate and voluntary—to a central destination that the project controls as much as practical. Direct creator payouts and platform advertising revenue are explicitly out of scope.

Intended future monetization includes affiliate referrals, sponsorships, qualified leads, and useful digital products with automated delivery. Views and follower counts are intermediate signals, not the business model.

Full requirements and current limitations: [PROJECT_OBJECTIVE.md](PROJECT_OBJECTIVE.md).

## Content discovery

- Multi-platform public discovery searches for video-post URLs from Instagram, TikTok, YouTube, X, Aparat, and public Telegram channels. Public search is a lead source, not access to a user's private feed.
- When `YOUTUBE_API_KEY` is configured, the discovery lane uses the official YouTube Data API for fresh videos and native public metrics.
- Candidates receive a heuristic priority score, confidence label, content lane, and safety/news/weapon/compilation/promotional review flags. The score is for triage only and does not predict actual growth or establish publication rights.
- Candidate records carry separate guidance for Instagram, TikTok, YouTube, Telegram, Aparat, and X. This guidance favors native sharing where available, keeps manual/export routes for disconnected platforms, and never marks rights cleared or publication authorized by default.
- A peak-window-aware planner checks the shortlist hourly and only launches more discovery when the qualified private-review buffer is low and one of the configured platform windows is approaching. Initial windows are hypotheses in `config/platform_peak_windows.json`, not account analytics; automatic discovery is capped at six runs per Tehran-local day.
- A scheduled workflow can send up to three deduplicated, recent candidate videos to the private internal Telegram review chat. These messages are operational review items—not public reposts and not the audience funnel.
- This repository is discovery-and-transfer-only: automatic video generation, rendering, remixing, and re-encoding workflows have been removed. The legacy builder entry point fails closed.
- Selected existing video files can be sent to the private Telegram review chat from supported, directly accessible sources. A native Telegram relay can copy source posts without rendering or downloading them.

## Destination-specific publishing

Every destination is evaluated separately. Rights, current platform rules, and account/API access are distinct checks; passing one does not pass the others.

- The native Telegram relay is scheduled for **12:33, 18:07, 19:37, 21:07, and 22:37 Tehran time** (GitHub Actions UTC cron: 09:03, 14:37, 16:07, 17:37, 19:07). Each slot transfers at most one top-ranked eligible existing video, so up to five can be handled per day with spaced posting times. Times avoid minute zero to reduce contention, but GitHub schedules remain best-effort rather than guaranteed.
- `allow_all_channels_bot_can_receive` in `config/telegram_source_policy.json` controls private-review intake only: the relay can rank eligible channel posts the bot actually receives. It does not authorize any source for public republication; the public audience source allowlist remains separate and empty until explicitly configured.
- The scheduled relay has no push or manual trigger, preventing a code change or manual test from causing an off-schedule public post.
- Public audience routing requires the Actions variable `TELEGRAM_AUDIENCE_PUBLISH_ENABLED=true`, secrets `TELEGRAM_AUDIENCE_BOT_TOKEN` and `TELEGRAM_AUDIENCE_CHAT_ID`, and an explicit source allowlist in `config/telegram_source_policy.json` under `authorized_public_repost_usernames` or `authorized_public_repost_chat_ids`. Only sources authorized for republication belong in these lists; the audience bot must have access to the source and destination.
- The public audience route remains disabled until the separate audience destination credentials and authorized source list are configured. Those settings cannot be inferred from public source popularity.
- Telegram is the only direct post-transfer route currently implemented. YouTube uploads remain paused while OAuth is unresolved. TikTok, Instagram, Aparat, and X public upload adapters are not connected. The system does not bypass sign-in, anti-bot challenges, or platform access restrictions.
- Discovery rankings are triage signals, not permission or a way around platform restrictions. Public relay requires explicit source authorization and a working destination connection.

See [DISTRIBUTION_ARCHITECTURE.md](DISTRIBUTION_ARCHITECTURE.md) for the platform-by-platform implementation roadmap.

## Trend Radar collector

The companion collector tracks Google Trends Trending Now (US + Iran) and Hacker News signals.

- Storage: Cloudflare D1 (`trend-radar-core`).
- Endpoints: `GET /`, `GET /health`, and `GET /status`.
- Collection schedule: every 15 minutes.
- The core trend collector does not require a paid provider.

## Automation

GitHub Actions runs discovery and direct transfer of selected existing videos from supported sources. No automatic workflow creates or edits video files. Public transfer stays disabled until the source allowlist and separate audience destination are explicitly configured.

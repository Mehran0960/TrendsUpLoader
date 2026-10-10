# TrendsUpLoader

## Primary objective: grow followers on-platform

The content pipeline is designed to discover already compelling short-form videos and grow followers on the same platform where each post appears. Platform ad revenue is not a goal, and directing viewers to an external destination is not required.

Project requirements and remaining stages are documented in [PROJECT_OBJECTIVE.md](PROJECT_OBJECTIVE.md).

## Content pipeline

- Multi-platform public discovery looks for video-post URLs from Instagram, TikTok, YouTube, X, Aparat, and public Telegram channels. Public search is a lead source, not direct access to a user's Instagram Explore feed.
- When `YOUTUBE_API_KEY` is configured, the discovery lane uses the official YouTube Data API to find fresh videos within a rolling 36-hour window and record native views, likes, and comments.
- Candidates receive a heuristic follower-growth priority score, confidence label, content lane, and separate safety/news/weapon/compilation review flags. Generic roundups are deprioritized in favor of one clear moment and payoff. The score is for triage only; it does not verify media ownership, publication rights, visual quality, or actual follower gains.
- A scheduled workflow can send up to three deduplicated, recent candidate links to the configured internal Telegram chat for review. These messages are private review alerts, not instructions to funnel an audience to Telegram and not automatic public reposts.
- The viral video hunter and a rights-gated original-video prototype are separate workflows. Automatic publication is not yet a universal multi-platform publisher; account/API access and platform-specific analytics still need to be connected where authorized.
- Reuse or cross-posting is allowed only after platform-specific rights, safety, media quality, and format checks. A high discovery score alone never grants permission to publish.

## Trend Radar data collector

The companion collector tracks trends and signals using Google Trends Trending Now (US + Iran) and the Hacker News official Firebase API.

- Storage: Cloudflare D1 (`trend-radar-core`).
- Endpoints: `GET /`, `GET /health`, and `GET /status`.
- Collection schedule: every 15 minutes.
- No paid provider is required for the core trend collector.

## Automation

GitHub Actions workflows run discovery, quality and rights tests, and eligible publishing tasks. See the Actions tab for the current status of individual runs.

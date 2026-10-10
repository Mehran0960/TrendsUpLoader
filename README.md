# TrendsUpLoader

## Primary objective: aggregate audience and monetize indirectly

This project uses social platforms as audience-acquisition channels, then guides interested people—when appropriate and voluntary—to a central destination that the project controls as much as practical. Direct creator payouts and platform advertising revenue are explicitly out of scope.

Intended future monetization includes affiliate referrals, sponsorships, qualified leads, and useful digital products with automated delivery. Views and follower counts are intermediate signals, not the business model.

Full requirements and current limitations: [PROJECT_OBJECTIVE.md](PROJECT_OBJECTIVE.md).

## Content discovery

- Multi-platform public discovery searches for video-post URLs from Instagram, TikTok, YouTube, X, Aparat, and public Telegram channels. Public search is a lead source, not access to a user's private feed.
- When `YOUTUBE_API_KEY` is configured, the discovery lane uses the official YouTube Data API for fresh videos and native public metrics.
- Candidates receive a heuristic priority score, confidence label, content lane, and safety/news/weapon/compilation/promotional review flags. The score is for triage only and does not predict actual growth or establish publication rights.
- Candidate records now carry separate guidance for Instagram, TikTok, YouTube, Telegram, Aparat, and X. This guidance favors native sharing where available, keeps manual/export routes for disconnected platforms, and never marks rights cleared or publication authorized by default. A stricter rule on one destination does not globally discard an otherwise useful lead.
- A scheduled workflow can send up to three deduplicated, recent candidate links to the private internal Telegram review chat. These messages are operational alerts—not public reposts and not the audience funnel.
- Candidate discovery and original-video creation are separate. Reusing a third-party video requires a license, permission, or platform-native reuse route that supports the intended use.

## Destination-specific publishing

Every destination is evaluated separately. Rights, current platform rules, and account/API access are distinct checks; passing one does not pass the others.

- `content_pipeline/distribution_plan.py` creates a per-platform readiness manifest next to the generated video's metadata.
- Telegram has an official Bot API publishing adapter, but public audience publishing is **off by default** and requires separate channel credentials plus explicit opt-in.
- YouTube, TikTok, Instagram, Aparat, and X publishing adapters are not yet connected in this repository. Until an authorized, sustainable route is verified, the workflow packages an MP4 for export instead of pretending automatic publishing is ready.
- The original-short workflow no longer depends on Make or a 5 MB relay. It builds a portable distribution bundle and does not publish to any public destination.
- No discovered third-party clip is automatically republished based only on its ranking score.

See [DISTRIBUTION_ARCHITECTURE.md](DISTRIBUTION_ARCHITECTURE.md) for the platform-by-platform implementation roadmap.

## Trend Radar collector

The companion collector tracks Google Trends Trending Now (US + Iran) and Hacker News signals.

- Storage: Cloudflare D1 (`trend-radar-core`).
- Endpoints: `GET /`, `GET /health`, and `GET /status`.
- Collection schedule: every 15 minutes.
- The core trend collector does not require a paid provider.

## Automation

GitHub Actions runs discovery, quality/rights checks, distribution-manifest creation, and opt-in publishing workflows. Check the Actions tab for individual run status.

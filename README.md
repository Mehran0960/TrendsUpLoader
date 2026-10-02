# Trend Radar MVP

Trend -> Signal -> Score -> Store.

Current phase: signal collection only. Publishing and AI generation are disabled.

Core sources: Google Trends Trending Now RSS and Hacker News official Firebase API.

Storage: Cloudflare D1 (trend-radar-db).

Endpoints: GET /, GET /health, POST /run.

No secrets are stored in source and no paid provider is required by the core collector.

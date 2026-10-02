# Source matrix

## Core sources
1. Google Trends RSS
- Public RSS export documented by Google Trends.
- No API key.
- Primary broad trend signal.
- Poll target: every 15 minutes.

2. Hacker News API
- Official public API.
- No API authentication.
- No rate limit currently documented.
- Technology/AI/startup signal.

## Supplemental/manual sources
3. TikTok Creative Center
- Free public trend discovery surface.
- Useful for hashtag trendlines, regional popularity and related videos.
- Not a hard automated dependency in MVP because automated access/API availability can change.

4. YouTube Data API
- Optional future adapter.
- Current docs show a separate 100-call/day quota for search.list and a 10,000-unit/day bucket for other endpoints.
- Never make YouTube API a single point of failure.

5. Reddit
- Excluded from core design because public RSS/API availability is changing.

## Cost policy
The detector must remain functional without paid APIs. Provider additions require a fresh quota/cost check.

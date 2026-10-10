# Project objective: grow followers on the platform where content is posted

## North star

The primary outcome is **new followers on each native platform** (Instagram, YouTube, TikTok, Telegram, Aparat, or another suitable platform). Platform ad revenue is not a goal, and directing viewers to a separate destination is not a requirement.

Evaluate each publishing experiment by:
1. Net new followers attributed to that post or publishing window, where analytics permit.
2. Views-to-follow conversion and repeat viewing/sharing as supporting signals.
3. Hands-on time and any cost required to acquire, prepare, and publish the post.
4. Account safety, rights clearance, and platform-policy compliance.

Raw view count alone is not success.

## Content strategy

- Prefer discovering already compelling, ready-made short videos over generating videos from scratch.
- Search broadly; do not lock the project to one topic. Favor a clear opening hook, one main event/payoff, replay value, and reasons to share.
- Treat public search, public creator posts, public Telegram channels, and platform-native discovery/remix tools as leads—not proof of permission or guaranteed virality.
- Reject search results that do not match the platform requested by the query, and distinguish native engagement metrics from views observed on third-party reposts.
- Use a new heuristic score only to prioritize human/visual review. It is neither an actual forecast of follower growth nor a publication-rights decision.

## Publishing rules

- Reuse existing media only when the relevant permission/license or a platform-native reuse feature supports the intended use.
- Check each platform independently. A file suitable for one platform is not automatically suitable for another.
- Do not strip watermarks, mirror, crop, or otherwise cosmetically alter a video as a substitute for permission or meaningful original value.
- Do not auto-publish simply because a candidate has a high score. Visual quality, safety, source attribution, rights, format, and destination-account access remain separate gates.

## Current implementation and limitations

- content_pipeline/social_discovery.py gathers publicly discoverable video-post URLs and persists candidate metadata.
- content_pipeline/follower_growth.py gives candidates a transparent heuristic priority score and flags confidence, content lane, news/sensitive risk, and review action.
- When `YOUTUBE_API_KEY` is configured, discovery uses the official YouTube Data API for fresh videos from a rolling 36-hour window and captures native views/likes/comments. The search query rotates among single-event-oriented topics; each candidate still needs visual and rights review.
- The no-key fallback uses public Bing results and public Telegram pages. These are imperfect sources; direct personal Instagram Explore access is **not** provided by this workflow.
- Multi-clip rankings/compilations and geopolitically driven current affairs are downranked or omitted from the routine curator digest; this is designed to favor one clear moment/payoff rather than a generic roundup.
- Current automated publication is not a universal multi-platform publisher. Platform-specific publishing and analytics require a suitable account/API or a permitted native workflow; account credentials and access have not been assumed.
- Scores do not verify video contents, ownership, licenses, or actual follower gains. Actual growth measurement must use per-account post insights where available.

## Next sequence

1. Improve precision of fresh source discovery and visually inspect the best candidates.
2. Maintain a candidate ledger with source URL, platform, native versus repost metrics, rights evidence, and review outcome.
3. Connect publishing only to platforms/accounts where an authorized, stable route exists.
4. Record each post's views, shares, profile visits, follows gained, and manual effort when available.
5. Re-rank sources and formats based on observed follower conversion, not guesses or view totals alone.

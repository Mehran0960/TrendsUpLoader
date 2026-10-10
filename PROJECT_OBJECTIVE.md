# Project objective: audience aggregation and indirect monetization

## North star

Build a low-cost, mostly automated system that acquires relevant audiences across suitable platforms, then makes it easy for interested people to find and revisit a central destination controlled by the project as much as practicable.

**Direct platform payouts and platform advertising revenue are explicitly out of scope.** Social platforms are distribution and discovery channels, not the business model. The intended monetization paths are indirect: affiliate referrals, sponsorships, leads, and eventually useful digital products where delivery can be automated.

## What success means

1. Relevant reach and follower growth on several suitable platforms.
2. Click-through to a clear, useful central destination when the user chooses to visit it.
3. A repeatable way to reach the audience again without depending entirely on one social account.
4. Clicks, qualified leads, affiliate conversions, and revenue relative to cost and hands-on effort.
5. Low operating cost, account safety, rights clearance, and recovery options if one provider changes its rules.

Raw views alone are not success. Nor is a large follower count useful if the audience is irrelevant or cannot be reached again.

## Content discovery and selection

- Search broadly across public sources and platform-specific discovery surfaces; do not lock the project to one topic.
- Prefer a strong opening hook, one main event or idea, a clear payoff, replay value, and reasons to share.
- Use discovery metrics only to prioritize review. Scores are not predictions of follower growth and do not grant permission to reuse media.
- Treat public posts as leads, not as proof of ownership, a license, or permission to download and republish.
- Keep discovery of third-party clips separate from generation of original content. A third-party clip is eligible for reuse only when the relevant rights, license, permission, or intended platform-native reuse feature supports that exact use.

## Destination-specific distribution

- Treat every destination independently. There is no single global rule such as “non-original content is okay” or “all platforms reject it.”
- Separate three checks: legal/right-to-use evidence, the destination's current content/recommendation rules, and whether the account has an authorized publishing route.
- A clip eligible for one platform is not automatically eligible for another. Adjust format, caption, attribution, disclosures, and publishing method per destination.
- Never strip watermarks, mirror, crop, or cosmetically edit a clip as a substitute for permission or meaningful original value.
- Publishing stays off unless the relevant destination adapter, credentials, rights gate, and explicit enablement are all present.

## Sustainable architecture

- Keep discovery, selection, rights evidence, scripts, metadata, and distribution manifests in this repository.
- Prefer official APIs and portable files; avoid making one commercial automation service a mandatory relay for all platforms.
- Keep platform adapters replaceable. If a platform or service becomes unavailable, discovery and packaging should still work, and the project should offer an export/manual fallback instead of losing the whole pipeline.
- Store credentials only as GitHub Actions secrets or appropriate service-side secrets; never commit them or paste them into chat.
- Preserve a source/license record and a per-destination readiness manifest with each generated video.

## Current implementation

- `content_pipeline/social_discovery.py` gathers public post URLs and metadata leads across Instagram, TikTok, YouTube, X, Aparat, and Telegram. It does not publish or download candidate media.
- `content_pipeline/follower_growth.py` provides a transparent heuristic triage score with freshness, topic-lane, and safety flags. It does not verify rights or forecast actual audience growth.
- The scheduled discovery workflow can send a small private shortlist to an internal Telegram chat. That channel is an operations alert, not the public audience destination.
- `content_pipeline/publication_rights.py` records basic asset license evidence and blocks unknown or unresolved rights from automated publication.
- `content_pipeline/distribution_plan.py` records destination-specific readiness. In this repository, Telegram has an opt-in Bot API route; YouTube, TikTok, Instagram, Aparat, and X publishing adapters remain unconnected.
- The original-short workflow now builds a portable MP4 and metadata bundle without Make or any publishing webhook.
- Public Telegram audience publishing is opt-in and requires a separate audience-channel bot/token, a repository variable, and an affirmative workflow input. It must not reuse the internal discovery-alert bot credentials.

## Next sequence

1. Improve discovery precision and inspect high-potential candidates, including sources that are currently underrepresented.
2. Keep a rights/attribution ledger and destination-specific readiness for every asset.
3. Connect the next official publishing adapter only after current access, permissions, and platform requirements are verified.
4. Maintain a central audience destination with clear, voluntary paths from distribution platforms.
5. Measure visits, follows, saves/shares, and indirect conversions; improve the system based on observed results rather than assumptions.

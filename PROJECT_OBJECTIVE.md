# Project objective: audience growth plus direct and indirect monetization

## North star

Build a low-cost, mostly automated system that finds high-potential videos and reaches audiences across suitable platforms, while pursuing both direct creator payouts where the platform supports them and indirect monetization through affiliate referrals, sponsorships, leads, and automated digital products.

**Direct platform payouts are now in scope as a supplementary revenue channel, beginning with FiLo.** They must be measured against actual verified views, RPM, payout eligibility, operating cost, and account stability; no fixed earnings are assumed.

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
- Video generation, rendering, remixing, and re-encoding are out of scope. Select existing videos and transfer them directly through a supported platform-native route whenever possible.
- Public republication requires the relevant permission/license or an explicitly authorized platform-native use. Do not evade sign-in, anti-bot controls, or destination access restrictions.

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
- The scheduled discovery workflow can send selected existing video files to the private Telegram review chat when direct media acquisition is supported. The native relay can copy posts from explicitly allowed Telegram source channels.
- `content_pipeline/publication_rights.py` records basic asset license evidence and blocks unknown or unresolved rights from automated publication.
- `content_pipeline/distribution_plan.py` records destination-specific readiness. Telegram has an opt-in Bot API route; the FiLo browser adapter and isolated workflow have been added, but require an authenticated form probe and a verified first upload. YouTube, TikTok, Instagram, Aparat, and X routes remain separately gated.
- The old video-builder entry point now fails closed, and automatic generation/remix workflows have been removed.
- Public Telegram audience publishing is opt-in and requires a separate audience-channel bot/token, a repository variable, and an affirmative workflow input. It must not reuse the internal discovery-alert bot credentials.

## Next sequence

1. Complete the authenticated FiLo upload-form probe and verify one manually triggered test upload before enabling scheduled uploads.
2. Measure FiLo views, RPM, balances, payout eligibility, and upload failure rate; treat earnings as unproven until dashboard data confirms them.
3. Improve discovery precision and inspect high-potential candidates, including sources that are currently underrepresented.
4. Keep destination-specific readiness, provenance, deduplication, and recovery options for every asset.
5. Maintain the indirect-monetization funnel and measure direct FiLo revenue alongside affiliate conversions and other observed results.

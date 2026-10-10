# Distribution architecture

## Business objective

Use platforms to acquire attention and relevant followers, then build a portable audience relationship and monetize indirectly. Direct platform ad revenue and creator payouts are not targets.

## Current implementation state

| Destination | Route in this repository | Current state |
|---|---|---|
| Telegram audience channel | Official Telegram Bot API `sendVideo` | Implemented but disabled by default; requires a separate audience bot and channel |
| YouTube | Official YouTube Data API | Not connected; current workflow exports an MP4 bundle only |
| TikTok | Official Content Posting API | Not connected; account/app permissions and current access must be verified |
| Instagram | Official publishing route | Not connected; account eligibility and permissions must be verified |
| Aparat | Official route to be verified | Not connected; package can be exported for a manual upload |
| X | Official API or manual export | Not connected |

This table describes the repository, not a guarantee that a platform's API is available from every country or account. Do not bypass a platform's access, sanctions, or authorization restrictions.

## Three independent gates

1. **Rights/provenance:** The project must have permission or an applicable license for every reused asset, plus appropriate attribution. A discovery score never clears rights.
2. **Destination policy:** Re-check the current policy of the specific platform, including reused-content, spam, AI/synthetic-media disclosure, safety, and formatting requirements that apply to this post.
3. **Account route:** The destination needs a current, authorized publishing method. If its API is unavailable or unsuitable, keep the content package and use a permitted manual/native feature instead.

A successful check for one destination does not imply eligibility for another.

## Portable distribution package

The workflow `Original Short Export Bundle (No External Relay)` manually builds one original, rights-gated short and stores these files as a GitHub Actions artifact for seven days:

- The original generated MP4.
- A normalized 720 × 1280 H.264/AAC MP4 for downstream platform-specific encoding.
- `metadata.json`, `rights_manifest.json`, attribution information when required, and `distribution_manifest.json`.

The export workflow does not upload the video to YouTube or any other public destination. There is no relay-provider account, webhook secret, or 5 MB intermediary limit in this workflow.

## Telegram publishing safety

The direct Telegram publisher is separated from the private internal discovery-alert bot. To configure a future public audience-channel test, repository settings must contain:

- Secret `TELEGRAM_AUDIENCE_BOT_TOKEN`: the bot token for the intended audience channel.
- Variable `TELEGRAM_AUDIENCE_CHAT_ID`: the destination channel identifier.
- Variable `TELEGRAM_AUDIENCE_PUBLISH_ENABLED=true`: explicit repository-level enablement.

The channel's bot must have the appropriate posting permission. Even with those settings, each run also requires `publish_to_telegram=true` in the manual content-pipeline workflow. That extra per-run gate is intentional until the destination and content quality have been validated. The publisher rechecks the editorial and rights manifest before upload, and records a content key to reduce accidental duplicate posting.

Do **not** reuse `TELEGRAM_BOT_TOKEN` or `TELEGRAM_CHAT_ID`; those names are reserved for private operational notifications.

## Why automation stays modular

No third-party automation service should be a mandatory relay for all destinations. Each adapter should be replaceable without changing discovery, rights records, or video generation. Secrets stay out of code. If a provider is unavailable, the system should still discover content, generate a rights-cleared asset, preserve the manifest, and provide an export fallback.

## Next build order

1. Verify the actual audience destination and its access from the user's account.
2. Test Telegram in opt-in mode only after a separate public channel is configured.
3. Add and verify native publishing adapters one at a time, starting with destinations that are both accessible and authorized.
4. Add per-platform post metrics where the account API supports them.
5. Connect a central destination and track voluntary click-through and indirect conversions.

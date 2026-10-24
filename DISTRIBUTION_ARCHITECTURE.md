# Distribution architecture

## Business objective

Use social platforms to acquire relevant followers while pursuing both direct creator payouts where practical (initially FiLo) and indirect monetization through a portable audience relationship. Direct payout is supplementary, not assumed income: verify real views, RPM, withdrawal eligibility, and operating effort.

## Current implementation state

| Destination | Route in this repository | Current state | Strategic posture |
|---|---|---|---|
| Telegram audience channel | Official Bot API `sendVideo` | Implemented but disabled by default | Candidate for an early opt-in test after a separate channel is ready |
| YouTube | Official YouTube Data API private-upload route | Optional private upload is wired into the manual content workflow; OAuth secrets are still required. Public scheduled publishing is not enabled. | Start with a private test upload, then schedule manually in YouTube Studio until public API authorization is verified. |
| TikTok | Official Content Posting API | Not connected; app/user authorization and audit may be required | Keep as a growth target, but don't assume unattended public posting is available |
| Instagram | Official publishing route | Not connected; account eligibility and permissions must be verified | Prefer original content and platform-native sharing/remix features; don't rely on simple copied reposts for recommendations |
| Aparat | Official route to be verified | Not connected | Keep in the candidate rollout; verify supported publishing route and account requirements before automating |
| FiLo | Playwright browser automation against the upload UI; no public upload API found in this review | Adapter + isolated workflow implemented; authenticated form probe and real upload still unverified. Manual runs default to probe-only; scheduled publishing is disabled until `IFILO_AUTOPUBLISH_ENABLED=true`. | Start with a no-upload form probe, then one manually triggered upload, then enable one-video-per-slot scheduling only after observing a confirmed success |
| X | Official API or manual export | Not connected | Use distinct, contextualized posts; avoid repeated identical or near-identical automated posts |

This table describes the repository, not a guarantee that a platform's API is available from every country or account. Do not bypass a platform's access, sanctions, or authorization restrictions.

## FiLo publishing adapter

- The standalone workflow is `.github/workflows/ifilo-publish.yml`; it does not change or block discovery, the private Telegram review, or any other destination.
- `content_pipeline/publish_ifilo.py` currently uses the publicly visible FiLo web panel at `https://dashboard.tablighgram.com/upload`. This review did not find a public upload API, so the current approach uses Playwright and an authenticated browser context.
- The workflow runs a safe form probe by default. A manual run must explicitly set `publish=true` to attempt one video. Scheduled upload runs remain disabled unless the repository variable `IFILO_AUTOPUBLISH_ENABLED` is explicitly set to `true` after a verified first upload.
- Configure GitHub Actions secrets `IFILO_LOGIN_ID` and `IFILO_PASSWORD` for identifier/password sign-in. If FiLo requires OTP/CAPTCHA or another sign-in method, the current script fails closed and emits form diagnostics; it does not bypass those steps. An optional `IFILO_STORAGE_STATE_B64` secret can supply a Playwright storage-state JSON if managed securely.
- The adapter currently considers one new, top-ranked, native Telegram video per run and keeps an attempt ledger in `attention_state/ifilo_upload_state.json`. It checks the file and duration/visual technical gates, tries to avoid duplicates, and does not auto-retry an ambiguous submission because the server may already have accepted it.
- First upload success has not yet been verified against an authenticated FiLo account. Treat the code as an implementation awaiting that real-account test, not as an already-running publisher.

## Peak-window-aware preparation

- The discovery workflow no longer treats a fixed four-hour cadence as the operating policy. It checks the preparation plan hourly; actual discovery is triggered only when the eligible private-review shortlist is below its target and a configured platform window is approaching.
- Initial test windows are in `config/platform_peak_windows.json`, in Asia/Tehran time. They are explicitly hypotheses, not verified peak analytics for this account. Replace them with each platform's own audience/activity analytics after the accounts are available.
- The planner targets six qualified, unreviewed candidates and caps automatic discovery runs at six per Tehran-local day. This keeps recurring searches bounded while allowing the candidate buffer to refill before more than one daily window.
- Critical distinction: this is a **private-review candidate buffer**, not a queue of rights-cleared, platform-ready assets. Discovered clips are not automatically authorized for reposting. Public publishing still requires original or appropriately licensed content and a connected, authorized destination adapter.

## Different platform rules — no one-size-fits-all filter

“Originality” is not a single universal switch. We evaluate each destination on its actual rules and supported features rather than applying the strictest platform's workflow to every destination.

- **Instagram:** Meta's published guidance says identical copies may be replaced by the original in recommendation surfaces, while meaningful remixes/commentary can be treated differently. Instagram's native repost feature shares eligible public posts/reels with attribution to the original creator. When available, native reposting is a lower-copying-risk sharing option, but this repository does not assume an authorized public API for automating it. [Meta: helping creators reach new audiences](https://about.fb.com/ltam/news/2024/05/ayudando-a-los-creadores-a-encontrar-nuevas-audiencias/) · [Meta: Instagram reposts](https://about.fb.com/news/2025/08/new-instagram-features-help-you-connect/)
- **TikTok:** Eligibility for the For You feed is distinct from permission to remain on the platform. Direct public posting requires the Content Posting API product, approved `video.publish` scope, user authorization, and an audit before an unaudited client can publish publicly. [For You recommendations](https://support.tiktok.com/en/using-tiktok/exploring-videos/how-tiktok-recommends-content) · [Content Posting API: Direct Post](https://developers.tiktok.com/docs/en/content-posting-api-get-started/)
- **YouTube:** Eligibility and copyright are distinct. Copyright, Community Guidelines, spam, and deceptive practices still matter; repeated uploads designed to manipulate engagement should be avoided. Prefer native Remix when available, or media cleared for the exact use with useful context. The YouTube Partner Program’s reused-content policy concerns monetization and is not a universal ban on posting, so it is not used as a blanket filter in this project. [YouTube Spam Policy](https://support.google.com/youtube/answer/2801973) · [Community Guidelines](https://support.google.com/youtube/answer/9288567) · [YPP reused-content policy (monetization context only)](https://support.google.com/youtube/answer/1311392)
- **X:** Its authenticity and automation rules prohibit spammy bulk/duplicative content, including repeated identical or nearly identical posts and prohibited automated activity. A cross-post should be relevant and, where appropriate, localized or rewritten for the destination. [Authenticity rules](https://help.x.com/en/rules-and-policies/authenticity) · [Automation rules](https://help.x.com/en/rules-and-policies/x-automation)
- **Telegram:** The Bot API upload route is technically implemented, but audience publishing stays opt-in. [Telegram Bot API](https://core.telegram.org/bots/api#sendvideo)
- **FiLo:** A browser-driven upload route is in the repository, but it still needs an authenticated form probe and a confirmed first upload. Direct creator earnings are treated as an experiment and must be measured from the account's actual analytics/RPM. No public upload API was confirmed during this review.
- **Aparat:** We have not yet verified a stable, authorized API route for this repository. Until that check is completed, retain a portable export instead of assuming automation exists.

These references are policy starting points, not permanent promises: review current wording and account-specific eligibility again before enabling a destination. None of the strategy is based on direct platform payouts.

## Three independent gates

1. **Rights/provenance:** The project needs permission or a license that permits the intended use of every reused asset, plus attribution if required. Discovery scores never clear rights.
2. **Destination policy:** Re-check current content, recommendation, spam, synthetic-media disclosure, safety, and formatting rules for that specific destination.
3. **Account route:** The destination needs a current, authorized publishing method. If an API is unavailable or unsuitable, keep the content package and use an allowed manual/native feature instead.

A successful check for one destination does not imply eligibility for another. Platform-native repost/remix tools are preferred over downloading and re-uploading when the desired use can be achieved with those features.

## Portable distribution package

The workflow `Original Short Export Bundle (No External Relay)` builds one original, rights-gated short and stores these files as a GitHub Actions artifact for seven days:

- The original generated MP4.
- A normalized 720 × 1280 H.264/AAC MP4 for downstream platform-specific encoding.
- `metadata.json`, `rights_manifest.json`, attribution information when required, and `distribution_manifest.json`.

The export workflow does not upload the video to YouTube or any other public destination. There is no relay-provider account, webhook secret, or 5 MB intermediary limit in this workflow.


## YouTube private-upload pilot

- The manual `Audience Content Pipeline` workflow now exposes an explicit `upload_to_youtube_private` checkbox. It defaults to off and can upload only as `private`; it never switches a video public or sets a public publishing time.
- To test it, add the three OAuth values as GitHub Actions secrets named `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`, and `YOUTUBE_REFRESH_TOKEN`, then dispatch the workflow with the checkbox enabled. The generated video must still pass editorial and rights gates.
- The YouTube upload ledger is stored in the workflow's cached `content_state` so duplicate content keys can be skipped between runs. A successful private upload is a technical smoke test, not proof that public scheduling through the API is authorized.

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

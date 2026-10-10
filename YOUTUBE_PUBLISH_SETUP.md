# YouTube private-upload setup (Make relay)

## Current status
- The YouTube upload intake scenario exists in Make as **YouTube Private Short Upload — Intake** and is inactive.
- It contains only a webhook trigger so far. It cannot publish anything until the incoming file structure is learned and the YouTube upload module is added.
- The intended flow is: GitHub builds one original Persian short → rights/editorial gates pass → the file is compressed to under 4.4 MB → GitHub sends it to Make → Make uploads it to the connected YouTube channel as **private**.
- The default GitHub workflow mode is `probe`. It sends a 1.5-second black test video whose title explicitly says it is a schema probe. The Make scenario will also filter out any request whose `mode` is not `upload`. Do not switch to `upload` until the scenario is finished and I confirm it is ready.
- Nothing in this path publishes a discovered third-party video. Only the curated, rights-gated original build is eligible.

## One-time steps

### 1. Authorize the correct YouTube channel in Make
Open the connection request supplied in the ChatGPT conversation, sign in, and choose the YouTube account/channel you want to grow. This is a Make-hosted authorization page; do not paste passwords or tokens into chat.

### 2. Save the Make webhook as a GitHub Actions secret
In GitHub, open this repository:
**Settings → Secrets and variables → Actions → New repository secret**

Name:
`MAKE_YOUTUBE_UPLOAD_WEBHOOK_URL`

Value: the webhook URL shown for the inactive Make scenario **YouTube Private Short Upload — Intake**. Keep the URL private. Do not commit it to a workflow file or source code.

### 3. Send a harmless schema probe
Open **Actions → YouTube Short -> Make Relay (Private) → Run workflow** and choose `probe`. This sends only the tiny schema-probe MP4; it is not intended for public posting. The resulting request is used to learn how Make represents the attached file.

## What happens next
After the YouTube connection is authorized and the probe structure is captured, configure the Make YouTube Upload module with:
- the authorized channel connection,
- the incoming video file and Persian metadata,
- `privacyStatus = private`,
- `selfDeclaredMadeForKids = false` unless the content is specifically made for children,
- synthetic/altered-media disclosure enabled for this AI-generated video.

The scenario stays inactive until its mapping and filter are verified. The final test upload remains private. Public auto-publishing is a separate step and is not enabled by this setup.

## Cost guard
Make documents a 5 MB per-file limit on its Free plan. The GitHub workflow enforces a 4.4 MB ceiling before it sends a file, leaving margin for multipart framing. If Make rejects a file, do not upgrade a plan automatically; inspect the run and the encoded file size first.

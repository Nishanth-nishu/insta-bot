# Publishing to Instagram

REELFORGE never posts on its own. Every reel ends as a package in `output/approved/<date>/<id>/` with the MP4, cover, caption, hashtags, sources and a checklist. From there you can upload manually, or let the official Instagram Graph API publisher post it after you approve.

## Option A: manual upload (works today)

1. Open the package folder, send the MP4 and cover to your phone (or use Instagram on the web).
2. Create a Reel, pick `cover.png` as the cover, paste `caption.txt`.
3. Record the post URL in `package.json` under `post_url`.

## Option B: Graph API publishing (one-time setup, free)

Two flavours exist; the publisher supports both via `IG_API_HOST` in `.env`.

**Unified / Instagram login (recommended, no Facebook Page needed)**

1. Instagram app: Settings → Account type and tools → switch to a **Professional** account (Creator or Business).
2. https://developers.facebook.com/apps/ → your app → Products → Instagram → **API setup with Unified login**.
3. Step 2 "Generate access tokens": **Add account**, log in with the Instagram account, then click **Generate token** next to it and approve. The number shown beside the username is the Instagram user id.
4. Put them in `.env` (git-ignored):
   ```
   IG_ACCESS_TOKEN=...
   IG_USER_ID=<number beside the username>
   IG_API_HOST=facebook
   ```
   Unified-login tokens are used against graph.facebook.com; set `instagram` only for a token from the older Instagram-login-only product.
5. Verify without posting: `.venv\Scripts\python -m services.publishing.instagram --check`

**Facebook login (needs an Instagram Professional account linked to a Facebook Page)**

1. Graph API Explorer (https://developers.facebook.com/tools/explorer/): pick the app, "Get User Access Token", add `instagram_basic`, `instagram_content_publish`, `pages_show_list`, `pages_read_engagement`, Generate Access Token and select the Page and Instagram account in the dialog.
2. Extend it at https://developers.facebook.com/tools/debug/accesstoken/ (about 60 days).
3. `GET me/accounts?fields=instagram_business_account` gives the Instagram user id. Set `IG_API_HOST=facebook`.

Instagram downloads the video from a public HTTPS URL. REELFORGE hosts the approved MP4 on the project's public GitHub repository on an orphan `media` branch and uses the `raw.githubusercontent.com` URL, which costs nothing. Set `PUBLIC_MEDIA_BASE_URL` in `.env` to use another host.

Publish flow (`services/publishing/instagram.py`):

```bash
# dry run: shows what would be hosted and posted, touches nothing
.venv\Scripts\python -m services.publishing.instagram --package output/approved/2026-11-08/gpt6-cyber
# live: hosts the file, creates a REELS container, waits for processing, publishes, records the permalink
.venv\Scripts\python -m services.publishing.instagram --package output/approved/2026-11-08/gpt6-cyber --confirm
```

Safeguards: the publisher refuses any package whose technical QA did not pass, defaults to a dry run, and requires `--confirm` plus credentials for a live post. Meta rate-limits content publishing to 100 posts per 24 hours per account, far above three reels a day.

## What is not supported

Unofficial Instagram automation (browser bots, private API clients) is excluded by design: it violates Instagram's terms and risks the account.

## Comment-to-DM link gate

Reels end with "comment LETS GO and I'll DM you the link". `services/publishing/dm_bot.py` reads the comments on every published reel (`output/approved/**/package.json` with `status: "published"`), finds the ones that say "let's go" in any spelling (`lets go`, `letsgo`, `LET'S GOOO 🔥`, with other words around it), sends that commenter a private reply containing the link, and can also post a public reply so others see the gate works. Official Graph API only: `GET /{media_id}/comments`, `POST /{ig_user_id}/messages` with `recipient.comment_id` (private reply), `POST /{comment_id}/replies` (public reply).

The link sent is `dm_link` in the package's `package.json` when present, otherwise the first line of `sources.txt` in the same folder. State is kept in `data/database/dm_bot.json`; a comment is never DMed twice and the account's own comments are skipped.

Delivery queue (since 2026-11-11): every matching comment is first put on a persistent queue in `data/database/dm_bot.json` (`queue`, keyed by comment id), then the bot delivers the queue oldest first. A send that fails for any reason (permission error, rate limit, network, Instagram outage) stays queued with `attempts`, `last_error` and `next_at`, and is retried on a later pass after 2, 5, 15, 30 and 60 minutes, then hourly, for up to seven days (the window in which Instagram still allows a private reply to a comment). Nothing is dropped silently: a delivered entry moves to `handled` with `ok: true` and its attempt count; an entry that outlives the window is moved to `handled` with `dropped: true` and the last error. Each pass reports `queued`, `sent`, `retried`, `pending`, `dropped` and `failed`; `python -m services.scheduler.daily status` prints the delivered count and whatever is still waiting. The scheduled `REELFORGE\dm-bot` task runs the pass every 5 minutes, so a LETS GO comment normally gets its DM within five minutes of being posted.

```bash
# dry run (default): lists the comments that would get a DM, posts nothing
.venv\Scripts\python -m services.publishing.dm_bot
# send the private replies; add --public-reply to also answer publicly
.venv\Scripts\python -m services.publishing.dm_bot --send
.venv\Scripts\python -m services.publishing.dm_bot --send --public-reply
# one reel only, custom texts, or poll forever (Ctrl+C to stop)
.venv\Scripts\python -m services.publishing.dm_bot --media 18449803003135288 --message "Here is the link: {link}" --public-text "Sent, check your DMs"
.venv\Scripts\python -m services.publishing.dm_bot --send --watch --interval 60
```

The run ends with a JSON summary (scanned, matched, sent, skipped_already, failed, per-failure errors). The token is never printed.

Development-mode caveat: while the Meta app is in Development mode, Instagram only lets it message users who hold a role on the app (admin, developer, tester). Reaching real followers needs App Review / Advanced Access for the messaging permission (`instagram_manage_messages`, or `instagram_business_manage_messages` for Instagram-login apps); until then the API answers with error code 10 / subcode 2534022 and the summary shows a `permission_hint`. The Instagram account must also allow message access for the app: Settings → Messages and story replies → Connected tools.

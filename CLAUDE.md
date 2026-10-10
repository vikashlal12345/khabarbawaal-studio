# KhabarBawaal Studio: guide for Claude

Instagram page **@khabarbawaal** (Facebook Page "KhabarBawaal"): Hinglish viral news, fun posts, market
updates for Indian 18-34s. Owner: Vikash (non-technical, iPhone 12, Claude **Pro** plan, no paid services).
A GitHub robot makes posts; the owner reviews them in a phone web app and posts to Instagram by hand.

## Working rules (owner's preferences)
- **Explain each change in plain words and wait for "yes" before doing it.** Read-only checks are fine.
- Keep everything **free** beyond the Claude membership (public repo, GitHub Actions/Pages, ntfy.sh).
- Upload with `python save.py "message"` (never plain `git push`, never force-push from local).
  `main` is **force-replaced every Sunday** (weekly-cleanup): if `git pull` fails with unrelated
  histories and there are no local changes, `git fetch origin && git reset --hard origin/main`.
- When redoing a published post by hand, **use new image file names** (the app caches images by path).
- **Don't change how 📤 Post shares files** without testing a version with the owner first.
  Verified (5 Oct 2026): Instagram orders shared photos by **file time stamp**, so `share()` keeps the
  original file names and sets `lastModified` in slide order. Renaming the files broke the order.
- Long chats are expensive (each message re-reads history): prefer fresh chats + this file.

## Content rules
- Hinglish in **English (Roman) letters only**, never Devanagari (fonts can't shape it).
- Politics: **pro right-wing (BJP/NDA), critical of left/opposition**; never pick stories that embarrass
  the BJP. Still: real facts, no invented quotes, no hate against communities, no calls to violence.
- Markets: data from NSE/CNBC only, **never buy/sell tips** (SEBI), "Not investment advice" on every post.
- Carousels max **10 images** (Instagram share-sheet limit): Top posts are **Top 5** (7 slides).
- Fun posts: single image; jokes on widely-known trends (Google Trends India, big stories, day/season) + relatable desi-life moments (first person, deadpan, e.g. @weebx_ style), short Gen Z meme formats; 3 options scored by a "young Indian" judge (understandable ≥7, funny ≥6) or the slot posts news. Joke bank retired.
- Captions: max **5 hashtags** (Instagram limit), high-reach + 1-2 specific, no #KhabarBawaal; `limit_hashtags()` enforces it.
- Every post is proofread by AI (spelling, names, facts vs article, layout); ✅/⚠️ shown in app.

## Schedule (IST) - `schedule.py`
**Fewer-posts plan (10 Oct 2026, ~9 a day = 5 Reels + 4 carousels).** Quiet 11 PM-5 AM (night jobs only).
6:00 🎬 🙏 devotional (festival from Google's India holidays calendar, Navratri goddess of the day anchored on
Saptami/Ashtami, else god of the weekday; devotional songs from JioSaavn search; Gita-Reel style) ·
7:00 🌅 Overnight Top 5 · 8:00 🎬 Tag that friend · 10:00 📰 news carousel · 13:00 🎬 Tag that friend ·
17:00 📰 news carousel · 18:00 🎬 political satire + REALITY CHECK (Congress/AAP/Left/Cockroach Janta Party) ·
19:00 🎬 meme · 20:00 🎬 news slideshow · 21:15 📰 Top 5 News. News carousels get a "KhabarBawaal ka take" slide
(originality). No market posts, Thought, Top 5 Viral or single-image fun posts (still available as `--kind`).
If a Reel can't be made, that slot posts a news carousel. Night: 1:00 three 📦 ready posts: one carousel in turn (Explainer → Quiz → Amazing Facts → Myth vs Fact,
from Sun 11 Oct 2026), 📅 On This Day, 🙏 Gita Reel (free Krishna-Arjuna paintings only, Wikimedia Commons; logo + "GITA GYAAN", Playfair/Cinzel
fonts, line-by-line fades with reading pauses; hook → "Shri Krishna ka sandesh" + verse ref (no Sanskrit) → Aaj ki seekh;
`reel.build_gita_reel`) ·
2:00 day planner (📅 previews, only at 10/17) · 4:00 learning.

## How it runs
- `.github/workflows/hourly.yml`: a self-restarting chain. `generate` job makes the post; `next` job
  waits for the next slot (`schedule.py next-wait`) while `inbox_watch.py` checks the inbox every minute,
  then dispatches the next run. Backup crons only act if the chain died. Guard: `--min-gap 20`.
- `create-post.yml`: ➕ Create (GitHub issue "Create post", or app inbox request with 2FA code).
- `insta-link.yml`: 🔗 Insta link via GitHub issue (backup; app normally uses the inbox).
- `deploy-app.yml`: publishes `docs/` when changed by hand. `weekly-fun-bank.yml`, `weekly-cleanup.yml`.
- All robot jobs share concurrency group `hourly-post` and check out `ref: main` (latest).

## Code map
- `generate.py`: news/fun/specials entry (`run()`), cards (`render_card`, banner = split poster), feed save.
- `carousel.py`: photo collection (article, X embeds via syndication API + Chrome screenshot, Bing News,
  Wikipedia), AI curation (order + story line per slide), slide renderers, `plan()`/`render()`.
- `proofread.py`: AI proofreader (`run(render, data, caption_of, article, drop=...)`), whole-word fixes.
- `specials.py` (Top 5s, roundup, thought) · `market.py` (NSE: `market-data-pre-open?key=NIFTY%2050`,
  allIndices, gainers/losers; CNBC quotes; BSE/Yahoo blocked) · `fun.py` (jokes + `claude_json` + usage log)
- 🎬 Your video/photos (in ➕ Create): the app grabs 6 frames into one picture, uploads it to the ntfy inbox as a file,
  `create_post.make_media()` writes on-video text, caption, pinned comment + cover options; the owner posts the video from the gallery.
- ➕ Create has **📰 Post / 🎬 Reel** (`format` in the inbox request → create-post.yml `FORMAT`). Reel + own video: the app
  also uploads the video (≤14.5 MB, else re-recorded at 1280 px without sound), `make_media_reel()` burns the AI hook onto it
  (`reel.own_reel`, own sound kept). Reel without video: `make_reel()` → `reel.topic_reel()` (news → photo slideshow,
  funny idea → Tag that friend/meme). Any failure falls back to the normal post. The owner only writes context.
- `reel.py`: 🎬 Reels (1080x1920 MP4, no music: owner adds a trending song). `make(kind)`: jokes = 3 options + judge
  (`fun.judge_best`), AI ranks Mixkit clips by title (no key); **only clips labelled "Free" licence** are used
  (Restricted = no business social media; checked one by one, Mixkit 429s bursts, licences remembered in state), proofread on still frames; news = AI picks a
  visual story from today's news posts, photos via `carousel.collect(max_words=14)`. Feed entry has `video`.
  Encoder: system ffmpeg, else `imageio-ffmpeg` (pip). Captions get `roman_only()` (no Devanagari).
  Reading time (owner, 11 Oct 2026): every text block stays `read_secs()` = 1.5 s + 0.25 s a word (news photos 3-7.5 s,
  Tag that friend 2.5 s + 0.25/word 6.5-10 s, meme build-up/punchline each ≥ old 4.4/3.6 s, satire hook 4.5-6 s);
  devotional/Gita keep their slower `read_time()`; own-video Reels = the video's length.
- `create_post.py` (`make(raw, kind)`; typed requests like "latest viral news" go through `resolve_prompt` = feeds + Bing + AI pick), `evergreen.py` (ready posts), `calendar_plan.py`, `insights.py`
  (learns only with 10+ linked posts and 30+ likes; ignores own pinned comment), `inbox.py`, `otp.py`.
- `status.py`: ⏳ Activity box in the app: Reel + ➕ Create steps/✅/❌ reasons go to ntfy topic `status_topic`
  (app polls every 10 s, keeps 24 h). `inbox_watch.py` skips requests already started; `create_done` in state stops repeats.
  ➕ Create allows duplicate posts (owner's choice); post ids include the time.
- `push.py`: 🔔 phone alerts from the Studio app itself (Web Push, delivered free by Apple). App 🔕/🔔 button (Home Screen app only)
  sends the phone's address to the inbox (`push_sub` → `state/push.json`, newest 3); workflows run `push.py --before` / `--after`
  (after deploy, so the post is live when tapped; tap opens that post). Robot posts 11 PM-5 AM don't buzz; ➕ Create ✅/❌ always.
- `inbox.py` also handles app 🗑️ delete and 🚀 boost messages. `save.py` merges feed/state clashes. `notify.py` turns alerts into GitHub issues (emails the owner).
- App: `docs/index.html` (feed, 📦 Ready tab, ➕ Create with PIN + authenticator code, 🔗 Insta link),
  `docs/stats.html` (report, Instagram, 🚀 boosts, plan usage, robot vs ad hoc), `docs/sw.js` (bump CACHE on change).
- Data: `docs/feed.json` (24 hours: `keep_days` 1, older posts + their files deleted; owner's choice to stop app crashes), `docs/ready.json` (also 24 hours), `state/history.json`, `docs/stats/*.json`.
- App memory (iPhone crashes): the app shows half-size previews `docs/posts/p/` (made/cleaned by `save.py`
  `make_previews()`, needs Pillow), loads carousel slides on swipe, plays only the on-screen Reel, keeps ~12 posts' share files.
- Config: `config.json` (feeds, colours, `inbox_topic` for ntfy.sh, counts).

## Secrets / Mac pieces
- GitHub secrets: `CLAUDE_CODE_OAUTH_TOKEN` (membership, created 2026-10-02, ~1 year; renew with
  `python3 renew_token.py` on the Mac), `CREATE_TOTP_SECRET` (authenticator for ➕ Create),
  `PUSH_VAPID_KEY` (🔔 alert sending key, made 2026-10-11; public half = `docs/app.json` `push_key`, change both together).
- Mac launchd job `com.khabarbawaal.stats` runs `mac_stats.py` hourly (only while awake): plan usage %
  (Mac keychain login), ad hoc Claude Code usage, Instagram followers + likes of linked posts.

## Testing locally
`export CLAUDE_BIN=~/.vscode/extensions/anthropic.claude-code-*/resources/native-binary/claude USE_CLAUDE_CLI=1`
then e.g. `.venv/bin/python generate.py --kind fun`; restore with
`git checkout docs/feed.json state/ && git clean -fq docs/posts` before uploading.

## Open ideas (not built)
Cloudflare email login for the app ·
Instagram Graph API (shares/saves, auto post list).

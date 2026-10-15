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
- Before replacing or deleting a Reel by hand, **check `docs/stats/ig_links.json`**: if the owner already posted it (linked), keep it and add the new version as a separate post (15 Oct 2026: a posted 6 PM was almost replaced).
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
- Joke Reels (8 AM, 1 PM, 7 PM; owner 12 Oct 2026): all 3 options on today's big talking points: `buzz_topics()` (owner
  14 Oct 2026: two 7 PM memes on matches nobody cared about) = 1 AI request reads Google + X trends + the feeds' headlines and
  rates what young Indians across India talk about (skips non-India/domestic matches, scorecards, share prices, region-only,
  deaths/crimes, hashtag campaigns, devotional wishes); only buzz 7+ go to the writer; relatable desi-life jokes only as a backup round when no trend joke passes the
  judge; `topic_articles()` gives the proofreader today's news about the joke's trend (facts like dates checked).
  📸 Context photos (owner 15 Oct 2026: a Ramayana trailer meme showed Thai dancers): the writer's `about` (film_show/person/team/party/brand/event_place/app + lookup; empty for festivals, seasons, everyday moments) → `context_photos()`: today's news photos of films/events/brands, kept only if the AI sees they show it (`look_at_photos`), then credited Wikipedia people + 'A VS B' card, App Store for apps; < 2 photos → stock clip (`pick_clip`, AI-looked); the photo slideshow lasts exactly the Reel's text time, every photo the same time, ≥ 2 s each (satire: the hook window). ➕ Create uses the same with `understand_topic`'s named things.
- Fun posts: single image; jokes on widely-known trends (Google Trends India, big stories, day/season) + relatable desi-life moments (first person, deadpan, e.g. @weebx_ style), short Gen Z meme formats; 3 options scored by a "young Indian" judge (understandable ≥7, funny ≥6) or the slot posts news. Joke bank retired.
- Captions: max **5 hashtags** (Instagram limit), high-reach + 1-2 specific, no #KhabarBawaal; `limit_hashtags()` enforces it.
- Every post is proofread by AI (spelling, names, facts vs article, layout); ✅/⚠️ shown in app.
  **No claim without a fact** (owner 12 Oct 2026): the proofreader must fix (reword/remove) any claim the article
  doesn't support instead of only flagging it; Reels use `proofread.run(strict=True)`: a claim it couldn't check
  left → `Unverified` → the Reel is made again (REEL_TRIES). Carousels still only show ⚠️.

📈 **What goes viral (owner 15 Oct 2026: a Salman Khan + Karisma reveal got 1.6k views + shares, most Reels ~200):** every picker/writer favours the BIGGEST names (A-list actors, top cricketers, PM) with a reveal / clash / behind-the-scenes / 90s nostalgia angle; hooks = star first then the surprise ('Salman ne Karisma ke saath shoot se mana kiya'); captions end with a side-taking question ('Salman sahi the? Haan ya Na?'); pinned comment = a share nudge ('Kisi Salman fan ko bhejo 👇'; devotional 'Apni maa ko bhejo'). `reel.viral_memory()` shows our own top 10 / weakest 5 Reels from `state/viral.json` to every picker and writer: `mac_stats.py` (Mac, every 3 h) reads 👁 views from each linked Reel's public embed page (`embed_views`: `video_view_count`, lower than the app's Views and later, so for RANKING; Reels linked in the last 3 days) into `instagram.json` and ranks the last 30 days (`viral_list`, posted 12 h+ ago); the owner's exact numbers from screen recordings go under 'recorded' (`python3 tools/views_from_recording.py <video> --save`, then save.py). 📊 report: 👁 on Today's plan chips and Best posts (sorted by views).

## Schedule (IST) - `schedule.py`
**12 Reels a day, 1.5 h apart (owner 15 Oct 2026: 11:30 AM + 3 PM news removed; speech at 9:30 PM)**: 6:00 🙏 devotional · 7:30 🔥 · 9:00 😂 Tag that friend · 10:30 🔥 · 12:00 😂 Tag that friend · 13:30 🔥 · 15:00 😂 meme · 16:30 🗳️ poll · 18:00 🎯 satire · 19:30 🔥 · 20:30 🧒 Bachpan · 21:30 🎥🔊 official speech (last three 1 h apart). `reel_news_noon/_aft` are `schedule.MANUAL`. App night tip: before 6 AM.
🎥🔊 = `make_official_reel` (owner 15 Oct 2026, 'every 10 PM post like' @geowiredaily's Modi Reel): official X accounts only (`OFFICIAL_X`: Modi, Shah, Rajnath, BJP, PMO, CMs, opposition leaders; never ANI/TV/films), read free from their public profile timelines (`official_videos`, last 20 h; X allows 30 reads / 15 min per address, stops politely), AI ranks speech videos (pro-Modi/BJP/NDA, skips greetings/devotional), `speech_start` = AI picks where he speaks (no title card / channel logo), `speech_reel` = ~30 s video on top + 2 red headline bars + slanted line with a question + CAPTION PADHO, `add_voice` = his voice inside the file (no song, posted as is), caption credits 'Video + voice: @handle on X'; no fitting video → `make_news_reel('reel_official')`.
🧒 = `make_bachpan_reel` (owner 15 Oct 2026, replaced the 9:15 PM 🔥, moved to 9 PM = 1 h before 10 PM; `reel_news_night` is now `schedule.MANUAL`); 2 styles on alternate days (`bachpan_style`, `BACHPAN_STYLES`; owner: 'both style, on alternate days'): odd date ordinal (16 Oct 2026) = TAG THAT FRIEND 'Wo dost jo bachpan mein…' (owner's favourite: the bijli 'Hooooo!' Reel) + 'TAG KARO USKO', even = the moment itself under a big red 'BACHPAN ME AISA KAUN KIYA?' banner (`clip_reel` shrinks long banners), ONE relatable 90s/2000s Indian childhood moment (myth / naughty trick / home-school moment, harmless, all-India), a stock clip that shows it (writer told to pick easy-to-film moments; Mac has only Mixkit, GitHub also Pixabay + AI pictures), footer 'COMMENT KARO: MAINE BHI', caption asks for comments; 3 options + judge, one rewrite; never repeats (`state.bachpan_recent`).
🗳️ = `make_poll_reel` (owner, after a creator's 'CHOOSE YOUR FUTURE' Reel got 16K likes / 8K comments): 'CHOOSE YOUR ___',
4 options tied to ❤️ like / 💬 comment / 🔁 repost / ✈️ share (button list in the caption), pictures via `poll_picture`
(Wikipedia `wiki_thumb` with its credit: photographer + licence in the caption, non-free skipped, GODL-India ok;
people/parties/teams never get a random news photo (once showed the wrong man as Kohli): name card instead; App Store
`app_icon`; things: Pixabay / news / AI / letter card), background = a bright free clip picked for the topic (owner chose
'test 3', poll schema `clip_search`/`clip_wanted`), else the sharp photo grid, topic from
`buzz_topics` or an evergreen desi debate, category varies (`state.poll_recent`); politics in the page's line (BJP = ✈️ share);
Instagram may show 'engagement bait' less, so once a day.
🔥 = ONE most viral/trending India story with its real photos (`NEWS_KINDS`: reel_news_early/_am/_noon/_aft/_pm/reel_news/
_night (by hand); owner: 'like the Bittu Tabahi one'; X + Google trends shown to the picker). The Top 5s became viral news; night
Reels (1 AM Ready, 3 AM God picture) and the 2 AM preview planner stopped (`PREVIEW_KINDS` = (); still runnable as
`--kind`, `schedule.MANUAL`); 📦 Ready tab removed from the app (owner 15 Oct 2026; `ready.json` is only written by night jobs run by hand, so the level-3 backup `evergreen.take_one` usually finds nothing); 4 AM learning stays. `docs/app.json` plan/reels match.
Older notes below (kept for the code they describe):
**All Reels (owner 13 Oct 2026: carousels weren't getting views; Reels reach non-followers): 9 Reels a day**, no regular
carousels (`REGULAR_HOURS` = []; news/Top 5/Thought/markets still work as `--kind`, `schedule.MANUAL`). Quiet 11 PM-5 AM.
6:00 🎬 🙏 devotional (festival from Google's India holidays calendar, Navratri goddess of the day anchored on
Saptami/Ashtami, else god of the weekday; devotional songs from JioSaavn search; Gita-Reel style) ·
7:00 🎬 overnight Top 5 Reel (`reel_top5_am`) · 8:00 🎬 Tag that friend · 10:00 🎬 news Reel (`reel_news_am`) ·
13:00 🎬 Tag that friend · 17:00 🎬 news Reel (`reel_news_pm`) · 18:00 🎬 political satire + REALITY CHECK (Congress/AAP/TMC/Left/Cockroach Janta Party;
owner 12 Oct 2026: a NEW joke daily, it kept repeating "X ka formula... EVM/vote chori": no ready-made example jokes,
rotating meme formats `SATIRE_FORMATS`, memory of the last 15 in `state.satire_recent` shown to picker/writer/judge
(judge needs fresh ≥ 6), party not in the last 2 preferred, facts = promise vs reality / U-turns / own record /
results; owner 14 Oct 2026: NO election results in REALITY CHECK (they came up daily; `ELECTION_RECORD` no longer
given to writer/proofreader; facts from the story's own articles; election results only for an election-result story),
memory now has `issue` + `facts` (picker: never the issue of the last 3 Reels; writer/judge: never recent facts), satire
judge bar clarity 6+ (`judge_best(min_clear=6)`, jokes 7+); 📸 the top window shows the REAL people the joke is about (owner 15 Oct 2026: Rahul Gandhi vs Bhagwant Mann story got a random stock woman): writer field `people` (1-3, Wikipedia titles) → `named_photos(fill_missing=True)` = 'RAHUL VS MANN' `names_card` + their photos (`photo_clip`, start 0), credited in the caption; no free photo → `name_card` (never a random news photo); nobody named → reaction clip as before. `wiki_thumb`: page image, else Wikidata P18; standard widths only (`WIKI_WIDTHS`, other sizes get 429) + `WIKI_UA` with contact + retries (`wiki_get`); KYA HUA? ≤ 28 words, else one AI rewrite `too_long()`; stories newest first (Bing `newest=True` + dates,
"N h ago"; today first, then backward day by day; **most viral first**: `fun.x_trends()` = X/Twitter India trends
from trends24.in (hours trended, free), the AI turns opposition-related trends into news searches (+1 request),
stories sorted by trend hours then age (owner: 12 Oct the most viral was CJP founder Abhijeet Dipke vs a Delhi
Police video, missed because searches were by party name); the trend word itself is searched too); the most viral story's jokes get
one rewrite with the judge's reasons (`NoGoodJoke.scores`) before the next story is tried; safety: skip stories
mainly about relatives or only allegations (FIR/ED/CBI, no verdict), REALITY CHECK = settled facts only, a death,
illness or grief is never the joke) ·
19:00 🎬 meme · 20:00 🎬 news Reel · 21:15 🎬 Top 5 News Reel (`reel_top5`). News Reels (`make_news_reel`): the most
viral + visual story of the feeds' last 8 h (+ X trends), not already used (`reel_news_used` links, `seen`); a 📅 planned
preview due at 10:00/17:00 (`schedule.PREVIEW_KINDS`) is that slot's story. Top 5 Reels (`make_top5_reel`): picked like
the old Top 5 carousels (`specials.gather/pick_top10`), one photo + "n. title" per story via `news_reel`, ~30 s.
**Never fail, best quality (owner 15 Oct 2026):** inside a joke Reel: clip = Pixabay videos → Mixkit
(Free licence; searches retried shorter, `shorter()`; backup searches) → if none fits 5/10 (`fallback_visual`, also
for the satire's reaction clip): free Pixabay photos (`stock_photos`) → news photos of the topic → 🎨 `ai_picture()` (owner
15 Oct 2026: 'people don't like the plain KhabarBawaal page': a comic-style AI picture of the joke's moment via the
god-picture demos, AI-checked for broken hands/legs/text, 2 tries, tall picture cropped for the meme window, proof note:
turn on Instagram's 'AI info') → `brand_background()` only as the very last option; jokes = trend round → one rewrite with the judge's
reasons → desi-life round. Slot: `generate.REEL_TRIES` = 3 tries → level 2 `reel.make_backup()` (a news Reel for a
joke/satire/devotional slot, a Tag that friend for a news/Top 5 slot, saved under the slot's id) → level 3
`evergreen.take_one(reels_only=True)` (a 📦 Ready Reel moved into the feed) → only then nothing. Free key: GitHub
secret `PIXABAY_API_KEY` (added 15 Oct 2026; Pexels removed: it stopped issuing API keys). Night: 1:00 two 📦 ready
REELS (14 Oct 2026: photo posts got 0-5 views): one carousel in turn (Explainer → Quiz → Amazing Facts → Myth vs Fact,
from Sun 11 Oct 2026) and 📅 On This Day, rendered as carousels then turned into Reels (`reel.slides_reel`, cover
without SWIPE as the hook, no closing card); the 🙏 Gita Reel is NOT made any more (14 Oct 2026, ~9 views avg; old notes:) "Gita Gyaan: roz ek shlok" (owner 11 Oct 2026: one shlok a day in order from 1.1,
`state.gita_next`; ~45 s; hook → KYA HO RAHA HAI → who says it + shlok, no Sanskrit → MATLAB → AAJ KI SEEKH →
AAJ KA SAWAAL; reference `assets/gita_ref.json` = owner's gita-shorts storyboards 1.1-2.30 (written by Claude,
owner-reviewed; 1.21-22 together), from 2.31 the free open library vedicscriptures.github.io (`gita_library()`:
Gita Press/Tejomayananda/Sivananda; same numbering for ch 2-18; down → AI knowledge + proofreader);
pictures: see 🎨 below; `--kind ready_gita` remakes only it; `reel.build_gita_reel`) ·
2:00 day planner (📅 previews, only at 10/17) · 3:00 🙏 God picture "Bhagwan ka saath" (📦 Ready, `bhakti.py`:
god beside a young Indian in a relatable moment, FLUX picture, Hinglish line in Kalam handwriting; own run = own
FLUX allowance; FLUX busy → no picture that night; since 14 Oct 2026 a 7 s Reel with that god's aartis/bhajans) · 4:00 learning.

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
  Context first (owner 15 Oct 2026: 'bachpan' matched a school app, its screenshots showed a child's name): `understand_topic()` (1 AI request) = what the owner means + the real things named (app/person/party/team/film_show/brand/event_place); ordinary words are never apps. Pictures by kind (`named_photos`): apps → App Store logos 'VS' card + screenshots; people/parties/teams/films → Wikipedia pictures (`names_card`, credited in the caption); brands/events → news photos; nothing named (an everyday moment) → a stock video clip. Proofreader `private` issue (a private person's details in a picture) → `proofread.Private` on Reels: the Create Reel switches to a video clip, robot Reels are made again.
- 🎨 God pictures (6 AM devotional + Gita Reels + 3 AM God picture, owner 11 Oct 2026): `flux_images()` paints 2 scenes
  (deity alone / with a young Indian today) with free public Hugging Face demos, no account or billing (owner said no
  billing; Gemini API free tier = 0 images): Z-Image-Turbo → FLUX.2-klein → FLUX.1-schnell (~4-5 pictures a day per IP,
  shared; 2 tries per scene). Gods shown with ONE visible hand (other arm hidden) or from behind: models add extra arms.
  Claude checks them (looks/respect/hands, no text); if none fits, the old free Wikimedia paintings (`pick_paintings`).
  Credit "AI illustration (<model>)".
- `reel.py`: 🎬 Reels (1080x1920 MP4, no music: owner adds a trending song). `make(kind)`: jokes = 3 options + judge
  (`fun.judge_best`), AI ranks Mixkit clips by title (no key); **only clips labelled "Free" licence** are used
  (Restricted = no business social media; checked one by one, Mixkit 429s bursts, licences remembered in state); 3D/CGI/cartoon clips skipped (`FAKE_CLIP`) and the AI LOOKS at 2 frames of the best 3 by title (`look_at_clips`: shows the moment 6+/10, real, bright, sharp; owner 15 Oct 2026 after a grey 3D 'bed' clip), none → `fallback_visual`; proofread on still frames; news = AI picks a
  visual story from today's news posts, photos via `carousel.collect(max_words=14)`. Feed entry has `video`.
  Encoder: system ffmpeg, else `imageio-ffmpeg` (pip). Captions get `roman_only()` (no Devanagari).
  🎵 Song ideas (owner 13 Oct 2026: "same song every Reel, not relatable"): `trending_songs(state)` merges free charts
  rank by rank: Shazam Top 200 India (page scrape, most looked-up), Spotify Top 50 India / Top Songs India / Top 50 Global
  (public embed pages), Apple Music most played IN/US (free RSS), JioSaavn "Insta Trending Viral Hits" (1223482895) +
  "Trending On Reels" (1269241159) playlists + trending; `SONG_WHERE` = where each charts. The AI picks songs whose
  hook/lyrics/vibe fit the moment, `mood` = why it fits; repeats allowed (owner) but at least 2 of 4 not in the last
  40 suggested (`state.songs_recent`, saved in `save_reel`). Top 5 Reels: top chart songs not suggested recently.
  🪝 Hook + follow (owner 14 Oct 2026, from his views screen recording: Reels avg 117 views vs photos 4; satire ~232,
  India news ~227, jokes ~150, devotional ~60, foreign/niche news ~15, Gita ~9; hits stall at 200-280 = Instagram's first
  test audience): the first frame always has the opening text; **no word-by-word / line-by-line text anywhere** (owner
  15 Oct 2026, 'like the Diwali safai Tag that friend Reel'): every screen shows its whole text at once (meme build-up
  and punchline, satire parts incl. both facts, news lines, end cards; devotional sections whole with a 0.3 s fade); news + Top 5 open on `hook_frame()`
  (HOOK_SECS 1.8: first photo, punch-in zoom, big Anton line from `hook` (AI, 4-8 words) / fixed Top 5 line, no logo
  or label; the line is max 500 px tall so a 4-line hook never covers @khabarbawaal, owner 15 Oct 2026); every Reel ends on `follow_frame()` (FOLLOW_SECS 1.5: 'Follow @khabarbawaal' + 'roz ki khabar + memes').
  Shorter: news 3 photos max (12-word lines); satire NOT shortened (tested 14 Oct 2026: 1 fact + ≤20/24-word KYA HUA?
  failed the judge on all 12 stories in 4 tries; kept hook ≤18 / KYA HUA? ≤28 / 2 facts ≤14 / punchline ≤14, ~35 s), Tag that friend ≤18 words, meme setup ≤12 + punch ≤10. News Reels: India-wide stories only. Clips: an
  'indian …' search first + Indian people preferred; no meat/alcohol/smoking clips for festival/religious jokes. First caption line = search keywords. Views can't be read
  automatically (public pages show likes only). Owner chose (14 Oct 2026) to send screen recordings of Instagram's
  Content list now and then: `python3 tools/views_from_recording.py <video>` (Mac: Apple Vision OCR, PDT → IST). Review ~21 Oct: replace the weakest slot with a 2nd satire Reel.
  Reading time (owner, 11 Oct 2026, `read_secs()` = 1.5 s + 0.25 s a word; 12 Oct 2026: "not able to read", then
  1.5x was "too much": **old time + a couple of seconds, not double**): every text screen +1 s = 2.5 s + 0.25/word
  (news photos 4-8.5 s, meme build-up/punchline ≥ 5.5/4.5 s ≤ 8.5 s, satire hook 5.5-7 s, satire KYA HUA?
  ~3.5 words/s + 1.9 s; satire set by the owner 15 Oct 2026 (~29 s): hook `satire_hook_secs` 4.5-6 s, KYA HUA? words/3.5 + 1.4 s, REALITY CHECK 9 s (`satire_part_secs`: each + up to 2 s, 1 s per 3 words above the OK'd Reel's 28/23 words; owner: 'only increase 1 or 2 seconds if content is more'), punchline + SEND THIS 5 s, follow 1 s = `SATIRE_FACTS_SECS/PUNCH/FOLLOW`; other Reels' follow stays 1.5 s), Tag that friend (one screen) +2 s = 4.5 s + 0.25/word, 8.5-12 s; a stock clip shorter than
  that freezes on its last frame (never cuts the text); devotional/Gita keep their slower `read_time()`;
  own-video Reels = the video's length.
- `create_post.py` (`make(raw, kind)`; typed requests like "latest viral news" go through `resolve_prompt` = feeds + Bing + AI pick), `evergreen.py` (ready posts), `calendar_plan.py`, `insights.py`
  (learns only with 10+ linked posts and 30+ likes; ignores own pinned comment), `inbox.py`, `otp.py`.
- `status.py`: ⏳ Activity box in the app: Reel + ➕ Create steps/✅/❌ reasons go to ntfy topic `status_topic`
  (app polls every 10 s, keeps 24 h). `inbox_watch.py` skips requests already started; `create_done` in state stops repeats.
  ➕ Create allows duplicate posts (owner's choice); post ids include the time.
- `push.py`: 🔔 phone alerts from the Studio app itself (Web Push, delivered free by Apple). App 🔕/🔔 button (Home Screen app only)
  sends the phone's address to the inbox (`push_sub` → `state/push.json`, newest 3); workflows run `push.py --before` / `--after`
  (after deploy, so the post is live when tapped; tap opens that post). Robot posts 11 PM-5 AM don't buzz; ➕ Create ✅/❌ always.
- `inbox.py` also handles app 🗑️ delete and 🚀 boost messages. `save.py` merges feed/state clashes. `notify.py` turns alerts into GitHub issues (emails the owner).
- App: `docs/index.html` (tabs **Posts** = not yet linked / **✅ Posted** = 🔗 Insta link pasted (`isLinked`: ig_links.json or this phone's `linked-` mark; owner 15 Oct 2026, replaced 📦 Ready) / ⏳ Activity; same 24 h clearing; ➕ Create with PIN + authenticator code, 🔗 Insta link; ⏱️ `postHint()`:
  time since the last 📤 Post on this phone (`lastShare`), wait an hour / night warning; Reel cover + song tips),
  `docs/stats.html` (report, Instagram, 🚀 boosts, plan usage, robot vs ad hoc; 🗓️ Today's plan reads ⏳ Activity live from ntfy: a slot past its time shows ⏳ in progress while the robot's last step is fresh 'run' (< 20 min) or in its first 30 min, only then ❌ not made; owner 15 Oct 2026), `docs/sw.js` (bump CACHE on change).
  `docs/app.json` `plan` (report's 🗓️ Today's plan) + `reels` (app's "Next Reel") are hand-kept: **change them with
  `schedule.SPECIALS`** (12 Oct 2026 they went stale after the all-Reels switch). Report's 🌙 Night posts = 📦 Ready posts
  made 0-6 AM IST today (`ready.json` ids `r<date>-<time>-<type>`: explainer/quiz/facts/myth_fact, on_this_day, gita, bhakti).
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

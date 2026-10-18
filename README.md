# KhabarBawaal Studio

Every hour a GitHub Action:
1. pulls fresh news from 15 Indian RSS feeds (viral, funny, politics, entertainment, cricket, tech),
2. asks Claude (on your membership) to pick the most viral story and write a headline, Hinglish caption and hashtags,
3. renders a 1080×1350 Instagram card (photo, headline, logo, handle, source),
4. publishes it to a small web app you install on your iPhone's home screen.

On the iPhone, tap **📤 Post**. The caption is copied, the share sheet opens, and you choose
Instagram → paste the caption → share.

## One-time setup

1. **Anthropic API key**: create one at https://console.anthropic.com → API Keys, and add some credit.
2. In the GitHub repo: **Settings → Secrets and variables → Actions → New repository secret**,
   name it `ANTHROPIC_API_KEY`, and paste the key.
3. **Settings → Pages → Source: GitHub Actions**.
4. **Actions → Hourly post → Run workflow** to make the first post right away.
5. On the iPhone, open `https://<github-username>.github.io/<repo-name>/` in **Safari** →
   Share button → **Add to Home Screen**.

## Customise

| What | Where |
|---|---|
| Page name, handle, colours, model, caption language | `config.json` (then run `python make_brand.py` for a new logo and icons) |
| Your own logo | Replace `assets/logo.png` (transparent PNG, wide) |
| News sources | `feeds` in `config.json` (any RSS feed) |
| Posting frequency | `cron` in `.github/workflows/hourly.yml` |
| Writing style | `SYSTEM_PROMPT` in `generate.py` |

## Run locally

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python generate.py --dry-run   # no AI: uses the newest story as-is
ANTHROPIC_API_KEY=sk-... .venv/bin/python generate.py
```

## Writing modes and cost

The post maker picks a mode on its own each hour:

| Mode | When | Extra cost |
|---|---|---|
| Membership | `CLAUDE_CODE_OAUTH_TOKEN` secret is set (from `claude setup-token`) | None. Uses your Claude plan's usage limits |
| Free | No token, or the AI call fails | None. Uses the news site's own headline and summary |
| API | `ANTHROPIC_API_KEY` secret is set | Pay-as-you-go API billing |

GitHub Actions and Pages are free for public repos.

## Fun posts

Every 3rd hourly post is an original funny post (tag-your-friend, desi relatable,
expectation vs reality, family group, office and student life) on a bright text card
made for forwarding. Claude writes it on your membership. If that's unavailable,
an unused post from `assets/fun_bank.json` is used. Add more to the bank with
`python fun.py --bank 50`. Make one now with `python generate.py --kind fun`.

## Email alerts

When something needs your attention, the robot opens a GitHub issue (label `alert`) that
@mentions you, and GitHub emails it. Alerts close themselves when the problem is gone.

| Alert | Meaning |
|---|---|
| `token` | Membership token expired or invalid. Posts continue in free mode. Renew with `python3 renew_token.py` |
| `token-expiry` | Token expires within 30 days |
| `limit` | Claude usage limit hit this hour. That post was made in free mode |
| `ai-error` | Other AI failure. That post was made in free mode |
| `feeds` | No news found for 3+ hours |

Test it: **Actions → Hourly post → Run workflow → tick "Also send a test alert email"**.
If no email arrives, check https://github.com/settings/notifications (Email must be on for "Participating").

## Before posting

Read the headline against the original story (**🔗 Story** button) before you post. Use news photos
with the source credit, which the card adds automatically.

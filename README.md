# KaleshAlert Studio

Every hour a GitHub Action:
1. pulls fresh news from 15 Indian RSS feeds (viral, funny, politics, entertainment, cricket, tech),
2. asks Claude to pick the most viral story and write a headline, Hinglish caption and hashtags,
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

## Cost

The default model (`claude-opus-5-5`, low effort) costs about $0.02–0.04 per post, so roughly
$15–30 a month at 24 posts a day. Setting `"model": "claude-haiku-4-5"` in `config.json`
cuts that to about a fifth, but headlines and story picks will be a bit weaker.
GitHub Actions and Pages are free for public repos.

## Before posting

Read the headline against the original story (**🔗 Story** button) before you post. Use news photos
with the source credit, which the card adds automatically.

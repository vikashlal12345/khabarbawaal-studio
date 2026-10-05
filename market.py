"""Market posts (trading days only, IST):
  8:15 AM  Market Morning Brief - global cues, GIFT Nifty, news that can move markets, stocks in focus
  3:45 PM  Closing Bell        - Nifty / Bank Nifty close, gainers & losers, sectors, why it moved

Numbers come from market data (NSE India, CNBC quotes) and are drawn exactly as received;
Claude only writes the explanations. Never "buy/sell" advice (SEBI: only registered advisers
may recommend stocks) - every post carries a "not investment advice" note.
"""
from __future__ import annotations

import os
from datetime import datetime

import requests
from PIL import Image, ImageDraw

import fun
import generate as g
import schedule

UA = g.UA
GREEN, RED, GREY = "#22c55e", "#ef4444", "#9a9a9a"
DISCLAIMER = "Sirf jaankari ke liye. Yeh investment advice nahi hai. Invest karne se pehle apni research karein."

NEWS_FEEDS = [
    ("Economic Times", "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"),
    ("Moneycontrol", "https://www.moneycontrol.com/rss/marketreports.xml"),
    ("Moneycontrol", "https://www.moneycontrol.com/rss/buzzingstocks.xml"),
    ("Mint", "https://www.livemint.com/rss/markets"),
    ("Business Standard", "https://www.business-standard.com/rss/markets-106.rss"),
]
SECTORS = ["NIFTY BANK", "NIFTY IT", "NIFTY AUTO", "NIFTY PHARMA", "NIFTY FMCG", "NIFTY METAL",
           "NIFTY REALTY", "NIFTY ENERGY", "NIFTY PSU BANK", "NIFTY MEDIA"]
GLOBAL = [(".SPX", "S&P 500"), (".IXIC", "Nasdaq"), (".DJI", "Dow Jones"), (".N225", "Nikkei"),
          (".HSI", "Hang Seng"), ("@LCO.1", "Brent Crude ($)"), ("@GC.1", "Gold ($)"), ("INR=", "USD / INR")]


# ---------------------------------------------------------------- data

def nse() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept": "application/json", "Referer": "https://www.nseindia.com/"})
    s.get("https://www.nseindia.com/", timeout=20)
    return s


def nse_json(s: requests.Session, path: str):
    return s.get("https://www.nseindia.com" + path, timeout=20).json()


def cnbc_quotes(symbols: list[str]) -> dict:
    r = requests.get("https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol",
                     params={"symbols": "|".join(symbols), "requestMethod": "itv", "noform": "1",
                             "partnerId": "2", "output": "json"},
                     headers={"User-Agent": UA}, timeout=20)
    out = {}
    for q in r.json()["FormattedQuoteResult"]["FormattedQuote"]:
        if q.get("last"):
            out[q["symbol"]] = {"last": q["last"], "pct": q.get("change_pct", "")}
    return out


def pct_value(p) -> float:
    try:
        return float(str(p).replace("%", "").replace("+", "").replace(",", ""))
    except ValueError:
        return 0.0


def today_nse_date() -> str:
    return schedule.ist_now().strftime("%d-%b-%Y")


def is_trading_day(s: requests.Session) -> bool:
    now = schedule.ist_now()
    if now.weekday() >= 5:
        return False
    try:
        holidays = {h["tradingDate"] for h in nse_json(s, "/api/holiday-master?type=trading")["CM"]}
    except Exception:
        holidays = set()
    return today_nse_date() not in holidays


def news_headlines(limit: int = 30) -> list[str]:
    import feedparser
    out = []
    for name, url in NEWS_FEEDS:
        try:
            feed = feedparser.parse(requests.get(url, headers={"User-Agent": UA}, timeout=20).content)
        except requests.RequestException:
            continue
        for e in feed.entries[:10]:
            out.append(f"({name}) {g.clean_text(e.get('title', ''))}: {g.clean_text(e.get('summary', ''))[:180]}")
    return out[:limit]


def market_photo() -> Image.Image | None:
    import create_post
    for title in ("Bombay Stock Exchange", "National Stock Exchange of India", "Dalal Street"):
        img = g.download_image(create_post.wikipedia_photo(title))
        if img is not None:
            return img
    return None


# ---------------------------------------------------------------- drawing helpers

def base(title: str, subtitle: str, photo: Image.Image | None = None, photo_h: int = 0) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    W, H = g.W, g.H
    img = Image.new("RGB", (W, H), "#0d0d0d")
    if photo is not None and photo_h:
        top = g.cover(photo, W, photo_h)
        img.paste(Image.blend(top, Image.new("RGB", top.size, "#000000"), 0.55), (0, 0))
    d = ImageDraw.Draw(img)
    if g.LOGO_FILE.exists():
        logo = Image.open(g.LOGO_FILE).convert("RGBA")
        logo.thumbnail((360, 82))
        img.paste(logo, (44, 44), logo)
    d.text((g.W - 50, 86), "MARKET", font=g.font("Poppins-Bold.ttf", 30), fill=g.CONFIG["accent_color"], anchor="rm")
    d.text((60, 210), title, font=g.font("Anton-Regular.ttf", 92), fill="white", anchor="lm")
    if subtitle:
        d.text((60, 290), subtitle, font=g.font("Poppins-SemiBold.ttf", 36), fill=g.CONFIG["accent_color"], anchor="lm")
    d.rectangle((0, H - 12, W, H), fill=g.CONFIG["accent_color"])
    return img, d


def foot(d: ImageDraw.ImageDraw, n: int, total: int, source: str) -> None:
    d.text((50, g.H - 58), f"{n}/{total}  ·  {g.CONFIG['handle']}", font=g.font("Poppins-SemiBold.ttf", 24),
           fill="white", anchor="lm")
    d.text((g.W - 50, g.H - 58), source, font=g.font("Poppins-SemiBold.ttf", 22), fill=(170, 170, 170), anchor="rm")


def arrow(p) -> tuple[bool | None, str]:
    """(up?, colour) for a change; up is None when unchanged."""
    v = pct_value(p)
    return (True, GREEN) if v > 0 else (False, RED) if v < 0 else (None, GREY)


def tri(d: ImageDraw.ImageDraw, x: int, y: int, size: int, up: bool | None, col: str) -> int:
    """Draw a ▲/▼ triangle with its left edge at x, centred on y; returns the x after it."""
    if up is None:
        return x
    h = size * 0.9
    pts = [(x, y + h / 2), (x + size, y + h / 2), (x + size / 2, y - h / 2)] if up else \
          [(x, y - h / 2), (x + size, y - h / 2), (x + size / 2, y + h / 2)]
    d.polygon(pts, fill=col)
    return x + size + int(size * 0.35)


def table_rows(d: ImageDraw.ImageDraw, rows: list[tuple[str, str, str]], y: int, row_h: int = 92) -> int:
    """(name, value, pct) rows with coloured change."""
    f_name, f_val = g.font("Poppins-SemiBold.ttf", 40), g.font("Poppins-Bold.ttf", 40)
    for i, (name, val, pct) in enumerate(rows):
        if i % 2 == 0:
            d.rectangle((40, y - 10, g.W - 40, y + row_h - 22), fill="#171717")
        _, col = arrow(pct)
        d.text((70, y + 26), g.printable(name), font=f_name, fill="white", anchor="lm")
        d.text((g.W - 330, y + 26), str(val), font=f_val, fill="white", anchor="rm")
        d.text((g.W - 70, y + 26), str(pct), font=f_val, fill=col, anchor="rm")
        y += row_h
    return y


def bullets(d: ImageDraw.ImageDraw, items: list[str], y: int, size: int = 40) -> int:
    for size_try in range(size, 26, -2):
        f = g.font("Poppins-Bold.ttf", size_try)
        wrapped = [g.wrap_words(g.printable(t).split(), f, g.W - 210, d) for t in items]
        row_h, gap = int(size_try * 1.35), int(size_try * 0.9)
        if y + sum(len(w) * row_h + gap for w in wrapped) <= g.H - 130:
            break
    for i, rows in enumerate(wrapped, 1):
        d.rounded_rectangle((60, y, 120, y + 60), radius=12, fill=g.CONFIG["tag_color"])
        d.text((90, y + 30), str(i), font=g.font("Anton-Regular.ttf", 40), fill="white", anchor="mm")
        for row in rows:
            d.text((145, y + 6), " ".join(row), font=f, fill="white")
            y += row_h
        y += gap
    return y


def disclaimer_slide(n: int, total: int) -> Image.Image:
    img, d = base("", "")
    d.text((g.W / 2, 470), "Aap kya", font=g.font("Anton-Regular.ttf", 140), fill="white", anchor="mm")
    d.text((g.W / 2, 630), "sochte ho?", font=g.font("Anton-Regular.ttf", 140), fill=g.CONFIG["accent_color"], anchor="mm")
    d.rounded_rectangle((g.W / 2 - 300, 750, g.W / 2 + 300, 840), radius=45, fill=g.CONFIG["tag_color"])
    d.text((g.W / 2, 795), "COMMENT KARO", font=g.font("Poppins-Bold.ttf", 40), fill="white", anchor="mm")
    f = g.font("Poppins-SemiBold.ttf", 30)
    y = 940
    for row in g.wrap_words(DISCLAIMER.split(), f, g.W - 160, d):
        d.text((g.W / 2, y), " ".join(row), font=f, fill=(190, 190, 190), anchor="mm")
        y += 44
    foot(d, n, total, "")
    return img


# ---------------------------------------------------------------- morning brief

MORNING_SCHEMA = {
    "type": "object",
    "properties": {
        "mood": {"type": "string", "description": "Max 10 words Hinglish: how the market may open today"},
        "big_news": {"type": "array", "items": {"type": "string"}, "description": "3 items, max 22 words each, Hinglish"},
        "in_focus": {"type": "array", "items": {"type": "object", "properties": {
            "company": {"type": "string"}, "why": {"type": "string", "description": "max 14 words, Hinglish"}},
            "required": ["company", "why"], "additionalProperties": False}, "description": "Up to 5 companies in the news"},
        "events": {"type": "array", "items": {"type": "string"}, "description": "Up to 4 things scheduled today, max 14 words"},
        "caption": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["mood", "big_news", "in_focus", "events", "caption", "hashtags"],
    "additionalProperties": False,
}

CLOSE_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "Max 12 words Hinglish one-liner about today's close"},
        "why": {"type": "array", "items": {"type": "string"}, "description": "3 reasons the market moved, max 22 words each, Hinglish"},
        "caption": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "why", "caption", "hashtags"],
    "additionalProperties": False,
}

RULES = ("Write in simple Hinglish for young Indians, in English (Roman) letters only, never Devanagari. Facts only from the data and headlines given. "
         "NEVER tell anyone to buy, sell or hold a stock, and never give targets or tips: SEBI rules allow that "
         "only for registered advisers. Say 'in focus' or 'news mein', never 'buy'. Use the numbers exactly as given. "
         "Caption: 3-4 short lines ending with a question, plus the line 'Not investment advice.' Hashtags: 8-10 "
         "including #KhabarBawaal #StockMarket #Nifty.")


def ai_write(system: str, user: str, schema: dict) -> dict | None:
    if not (os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or os.environ.get("USE_CLAUDE_CLI")):
        return None
    try:
        return fun.claude_json(system, user, schema, g.CONFIG.get("membership_model", "sonnet"))
    except Exception as e:
        print(f"  ! AI failed: {e}")
        return None


def morning_data(force: bool = False) -> dict | None:
    s = nse()
    if not force and not is_trading_day(s):
        print("Market holiday/weekend: no morning brief.")
        return None
    status = nse_json(s, "/api/marketStatus")
    gift = status.get("giftnifty") or {}
    quotes = cnbc_quotes([sym for sym, _ in GLOBAL])
    nifty = next((m for m in status["marketState"] if m["market"] == "Capital Market"), {})
    return {
        "gift": {"last": f"{float(gift.get('LASTPRICE', 0)):,.2f}", "pct": f"{float(gift.get('PERCHANGE', 0)):+.2f}%"} if gift else None,
        "nifty_prev": f"{float(nifty.get('last', 0)):,.2f}" if nifty.get("last") else "",
        "global": [(name, quotes[sym]["last"], quotes[sym]["pct"]) for sym, name in GLOBAL if sym in quotes],
    }


def make_morning(state: dict, force: bool = False) -> dict | None:
    import proofread

    market = morning_data(force)
    if market is None:
        return None
    news = news_headlines()
    facts = (f"GIFT Nifty: {market['gift']}\nNifty previous close: {market['nifty_prev']}\nGlobal: "
             + "; ".join(f"{n} {v} ({p})" for n, v, p in market["global"])
             + "\n(All numbers above are official market data and are correct.)")
    out = ai_write(f"You write the 8:15 AM 'Market Morning Brief' for {g.CONFIG['page_name']}. {RULES}",
                   f"{fun.today_context()}\n\nMarket data:\n{facts}\n\nHeadlines:\n" + "\n".join(news), MORNING_SCHEMA)
    if out is None:  # no AI: data slides only, headlines as news
        out = {"mood": "Aaj market ke liye global sanket", "big_news": [h.split(": ")[0][:110] for h in news[:3]],
               "in_focus": [], "events": [], "caption": "Aaj ka Market Morning Brief 📈\nNot investment advice.",
               "hashtags": ["#StockMarket", "#Nifty", "#KhabarBawaal"]}
        print("Mode: market_open (free)")
    else:
        print("Mode: market_open (membership)")
    photo = market_photo()
    data = {**out, "market": market}

    def render(dt: dict) -> list[Image.Image]:
        m = dt["market"]
        slides = []
        has_focus, has_events = bool(dt["in_focus"]), bool(dt["events"])
        total = 4 + has_focus + has_events
        # 1 cover
        img, d = base("MORNING BRIEF", schedule.ist_now().strftime("%A, %d %B %Y"), photo, 820)
        if m["gift"]:
            up, col = arrow(m["gift"]["pct"])
            d.text((60, 470), "GIFT NIFTY", font=g.font("Poppins-Bold.ttf", 44), fill="white", anchor="lm")
            d.text((60, 590), m["gift"]["last"], font=g.font("Anton-Regular.ttf", 150), fill="white", anchor="lm")
            x = tri(d, 60, 725, 60, up, col)
            d.text((x, 720), m["gift"]["pct"], font=g.font("Anton-Regular.ttf", 90), fill=col, anchor="lm")
        f = g.font("Poppins-Bold.ttf", 46)
        y = 900
        for row in g.wrap_words(g.printable(dt["mood"]).split(), f, g.W - 120, d)[:3]:
            d.text((60, y), " ".join(row), font=f, fill=g.CONFIG["accent_color"])
            y += 62
        d.rounded_rectangle((60, 1130, 360, 1190), radius=30, fill=g.CONFIG["accent_color"])
        d.text((210, 1160), "SWIPE  >>", font=g.font("Poppins-Bold.ttf", 30), fill="#0d0d0d", anchor="mm")
        foot(d, 1, total, "Data: NSE, CNBC")
        slides.append(img)
        # 2 global cues
        img, d = base("GLOBAL CUES", "Raat bhar duniya ke market")
        table_rows(d, m["global"], 380)
        foot(d, len(slides) + 1, total, "Data: CNBC")
        slides.append(img)
        # 3 big news
        img, d = base("AAJ KI BADI KHABAR", "Jo market hila sakti hai")
        bullets(d, dt["big_news"][:3], 380)
        foot(d, len(slides) + 1, total, "News: ET, Moneycontrol, Mint, BS")
        slides.append(img)
        # 4 stocks in focus (news, not advice)
        if has_focus:
            img, d = base("STOCKS IN FOCUS", "News mein rahenge (advice nahi)")
            bullets(d, [f"{x['company']}: {x['why']}" for x in dt["in_focus"][:5]], 380, size=38)
            foot(d, len(slides) + 1, total, "News based")
            slides.append(img)
        # 5 events
        if has_events:
            img, d = base("AAJ KE EVENTS", "In par nazar rakhein")
            bullets(d, dt["events"][:4], 380)
            foot(d, len(slides) + 1, total, "")
            slides.append(img)
        slides.append(disclaimer_slide(len(slides) + 1, total))
        return slides

    def caption(dt: dict) -> str:
        tags = " ".join(t if t.startswith("#") else f"#{t}" for t in dt["hashtags"])
        cap = dt["caption"].strip()
        if "investment advice" not in cap.lower():
            cap += "\nNot investment advice."
        return f"{cap}\n\nFollow {g.CONFIG['handle']} for daily market updates.\n\n{tags}"

    slides, data, proof = proofread.run(render, data, caption, facts + "\n\nHeadlines:\n" + "\n".join(news))
    return {"slides": slides, "caption": caption(data), "proof": proof, "tag": "📈 MARKET BRIEF",
            "headline": f"Market Morning Brief: {schedule.ist_now():%d %b}", "source": "NSE, CNBC, ET, Moneycontrol"}


# ---------------------------------------------------------------- closing bell

def close_data(force: bool = False) -> dict | None:
    s = nse()
    status = nse_json(s, "/api/marketStatus")
    cm = next((m for m in status["marketState"] if m["market"] == "Capital Market"), {})
    if not force and not cm.get("tradeDate", "").startswith(today_nse_date()):
        print(f"No trading today (last trade date {cm.get('tradeDate')}): no closing bell.")
        return None
    idx = {x["index"]: x for x in nse_json(s, "/api/allIndices")["data"]}
    def row(name, label=None):
        x = idx.get(name)
        return (label or name.title().replace("Nifty", "Nifty"), f"{x['last']:,.2f}", f"{x['percentChange']:+.2f}%") if x else None
    movers = lambda kind: nse_json(s, f"/api/live-analysis-variations?index={kind}").get("NIFTY", {}).get("data", [])
    gainers = [(x["symbol"], f"{x['ltp']:,.2f}", f"{x['perChange']:+.2f}%") for x in movers("gainers")[:5]]
    losers = [(x["symbol"], f"{x['ltp']:,.2f}", f"{x['perChange']:+.2f}%") for x in movers("loosers")[:5]]
    label = {"IT": "IT", "FMCG": "FMCG", "PSU BANK": "PSU Bank"}
    sectors = [r for r in (row(n, label.get(n[6:], n[6:].title())) for n in SECTORS) if r]
    sectors.sort(key=lambda r: pct_value(r[2]), reverse=True)
    quotes = cnbc_quotes(["INR=", "@GC.1", "@LCO.1"])
    nifty, bank, vix = idx.get("NIFTY 50", {}), idx.get("NIFTY BANK", {}), idx.get("INDIA VIX", {})
    trade_day = datetime.strptime(cm["tradeDate"][:11], "%d-%b-%Y").strftime("%A, %d %B %Y")
    return {
        "date": trade_day,
        "nifty": {"last": f"{nifty.get('last', 0):,.2f}", "change": f"{nifty.get('variation', 0):+,.2f}",
                  "pct": f"{nifty.get('percentChange', 0):+.2f}%",
                  "adv": nifty.get("advances", ""), "dec": nifty.get("declines", "")},
        "bank": {"last": f"{bank.get('last', 0):,.2f}", "pct": f"{bank.get('percentChange', 0):+.2f}%"},
        "vix": {"last": f"{vix.get('last', 0):,.2f}", "pct": f"{vix.get('percentChange', 0):+.2f}%"},
        "gainers": gainers, "losers": losers, "sectors": sectors,
        "others": [(name, quotes[sym]["last"], quotes[sym]["pct"]) for sym, name in
                   (("INR=", "USD / INR"), ("@GC.1", "Gold ($)"), ("@LCO.1", "Brent Crude ($)")) if sym in quotes],
    }


def make_close(state: dict, force: bool = False) -> dict | None:
    import proofread

    market = close_data(force)
    if market is None:
        return None
    news = news_headlines()
    n = market["nifty"]
    facts = (f"Nifty 50 closed {n['last']} ({n['change']}, {n['pct']}); advances {n['adv']}, declines {n['dec']}. "
             f"Bank Nifty {market['bank']['last']} ({market['bank']['pct']}). India VIX {market['vix']['last']}.\n"
             f"India VIX change {market['vix']['pct']}.\nTop gainers: {market['gainers']}\nTop losers: {market['losers']}\n"
             f"Sectors: {market['sectors']}\nOther markets: {market['others']}\n"
             f"(All numbers above are official market data and are correct.)")
    out = ai_write(f"You write the 3:45 PM 'Closing Bell' market wrap for {g.CONFIG['page_name']}. {RULES}",
                   f"{fun.today_context()}\n\nToday's market data:\n{facts}\n\nHeadlines:\n" + "\n".join(news), CLOSE_SCHEMA)
    if out is None:
        out = {"summary": "Aaj market aise band hua", "why": [h.split(": ")[0][:110] for h in news[:3]],
               "caption": "Aaj ka Closing Bell 🔔\nNot investment advice.", "hashtags": ["#StockMarket", "#Nifty", "#KhabarBawaal"]}
        print("Mode: market_close (free)")
    else:
        print("Mode: market_close (membership)")
    photo = market_photo()
    data = {**out, "market": market}

    def render(dt: dict) -> list[Image.Image]:
        m = dt["market"]
        total = 6
        slides = []
        # 1 cover: Nifty close
        img, d = base("CLOSING BELL", m["date"], photo, 820)
        up, col = arrow(m["nifty"]["pct"])
        d.text((60, 470), "NIFTY 50", font=g.font("Poppins-Bold.ttf", 44), fill="white", anchor="lm")
        d.text((60, 590), m["nifty"]["last"], font=g.font("Anton-Regular.ttf", 150), fill="white", anchor="lm")
        x = tri(d, 60, 725, 54, up, col)
        d.text((x, 720), f"{m['nifty']['change']} ({m['nifty']['pct']})", font=g.font("Anton-Regular.ttf", 80),
               fill=col, anchor="lm")
        bup, bc = arrow(m["bank"]["pct"])
        bf = g.font("Poppins-Bold.ttf", 44)
        d.text((60, 880), f"Bank Nifty  {m['bank']['last']}", font=bf, fill="white", anchor="lm")
        tw = d.textlength(m["bank"]["pct"], font=bf)
        d.text((g.W - 60, 880), m["bank"]["pct"], font=bf, fill=bc, anchor="rm")
        tri(d, int(g.W - 60 - tw - 46), 882, 32, bup, bc)
        f = g.font("Poppins-Bold.ttf", 44)
        y = 960
        for row in g.wrap_words(g.printable(dt["summary"]).split(), f, g.W - 120, d)[:2]:
            d.text((60, y), " ".join(row), font=f, fill=g.CONFIG["accent_color"])
            y += 60
        d.rounded_rectangle((60, 1130, 360, 1190), radius=30, fill=g.CONFIG["accent_color"])
        d.text((210, 1160), "SWIPE  >>", font=g.font("Poppins-Bold.ttf", 30), fill="#0d0d0d", anchor="mm")
        foot(d, 1, total, "Data: NSE")
        slides.append(img)
        # 2 gainers & losers
        img, d = base("TOP GAINERS", "Nifty 50")
        y = table_rows(d, m["gainers"], 360, row_h=80)
        d.text((60, y + 50), "TOP LOSERS", font=g.font("Anton-Regular.ttf", 80), fill="white", anchor="lm")
        table_rows(d, m["losers"], y + 120, row_h=80)
        foot(d, 2, total, "Data: NSE")
        slides.append(img)
        # 3 sectors as bars
        img, d = base("SECTORS", f"Advances {m['nifty']['adv']} · Declines {m['nifty']['dec']}")
        top = max([abs(pct_value(r[2])) for r in m["sectors"]] + [0.5])
        y = 380
        for name, _, pct in m["sectors"][:10]:
            v = pct_value(pct)
            mid, w = g.W // 2 + 60, int(abs(v) / top * 330)
            d.text((60, y + 28), g.printable(name), font=g.font("Poppins-SemiBold.ttf", 36), fill="white", anchor="lm")
            d.rectangle((mid, y + 8, mid + w, y + 48) if v >= 0 else (mid - w, y + 8, mid, y + 48),
                        fill=GREEN if v >= 0 else RED)
            d.text((g.W - 50, y + 28), pct, font=g.font("Poppins-Bold.ttf", 34), fill=GREEN if v >= 0 else RED, anchor="rm")
            y += 78
        foot(d, 3, total, "Data: NSE")
        slides.append(img)
        # 4 why it moved
        img, d = base("MARKET KYUN HILA?", "Aaj ke bade kaaran")
        bullets(d, dt["why"][:3], 380)
        foot(d, 4, total, "News: ET, Moneycontrol, Mint, BS")
        slides.append(img)
        # 5 rupee, gold, crude, VIX
        img, d = base("BAAKI MARKET", "Rupee, sona, crude, VIX")
        table_rows(d, m["others"] + [("India VIX", m["vix"]["last"], m["vix"]["pct"])], 380)
        foot(d, 5, total, "Data: CNBC, NSE")
        slides.append(img)
        slides.append(disclaimer_slide(6, total))
        return slides

    def caption(dt: dict) -> str:
        tags = " ".join(t if t.startswith("#") else f"#{t}" for t in dt["hashtags"])
        cap = dt["caption"].strip()
        if "investment advice" not in cap.lower():
            cap += "\nNot investment advice."
        return f"{cap}\n\nFollow {g.CONFIG['handle']} for daily market updates.\n\n{tags}"

    slides, data, proof = proofread.run(render, data, caption, facts + "\n\nHeadlines:\n" + "\n".join(news))
    return {"slides": slides, "caption": caption(data), "proof": proof, "tag": "🔔 CLOSING BELL",
            "headline": f"Closing Bell: Nifty {market['nifty']['last']} ({market['nifty']['pct']})", "source": "NSE, CNBC"}


# ---------------------------------------------------------------- 9:09 AM pre-open snapshot

def preopen_data(s: requests.Session | None = None, force: bool = False) -> dict | None:
    """NSE pre-open session (9:00-9:08): expected opening of Nifty 50 stocks and the index."""
    s = s or nse()
    if not force and not is_trading_day(s):
        print("Market holiday/weekend: no pre-open snapshot.")
        return None
    d = nse_json(s, "/api/market-data-pre-open?key=NIFTY%2050")   # NSE renamed the key from NIFTY (Oct 2026)
    rows = []
    for r in d.get("data", []):
        m = r.get("metadata", {})
        price = m.get("iep") or m.get("finalPrice") or m.get("lastPrice")
        if m.get("symbol") and price:
            rows.append({"symbol": m["symbol"], "price": float(price), "pct": float(m.get("pChange") or 0)})
    if len(rows) < 10:
        print("Pre-open data not available yet: skipping.")
        return None
    rows.sort(key=lambda r: r["pct"], reverse=True)
    fmt = lambda r: (r["symbol"], f"{r['price']:,.2f}", f"{r['pct']:+.2f}%")
    idx = nse_json(s, "/api/NextApi/apiClient?functionName=getIndexData&&index=NIFTY%2050")["data"][0]
    last, prev = float(idx.get("last") or 0), float(idx.get("previousClose") or 0)
    change = last - prev if last and prev else 0.0
    return {
        "nifty": {"last": f"{last:,.2f}", "change": f"{change:+,.2f}",
                  "pct": f"{(change / prev * 100) if prev else 0:+.2f}%", "prev": f"{prev:,.2f}"},
        "advances": d.get("advances", sum(r["pct"] > 0 for r in rows)),
        "declines": d.get("declines", sum(r["pct"] < 0 for r in rows)),
        "high": [fmt(r) for r in rows[:5]], "low": [fmt(r) for r in rows[::-1][:5]],
    }


def render_preopen(m: dict) -> list[Image.Image]:
    total = 3
    img, d = base("PRE-OPEN", schedule.ist_now().strftime("%A, %d %B %Y") + "  ·  9:08 AM", market_photo(), 820)
    up, col = arrow(m["nifty"]["pct"])
    d.text((60, 470), "NIFTY 50 EXPECTED OPEN", font=g.font("Poppins-Bold.ttf", 40), fill="white", anchor="lm")
    d.text((60, 590), m["nifty"]["last"], font=g.font("Anton-Regular.ttf", 150), fill="white", anchor="lm")
    x = tri(d, 60, 725, 54, up, col)
    d.text((x, 720), f"{m['nifty']['change']} ({m['nifty']['pct']})", font=g.font("Anton-Regular.ttf", 80),
           fill=col, anchor="lm")
    f = g.font("Poppins-Bold.ttf", 42)
    d.text((60, 890), f"Pichhla close: {m['nifty']['prev']}", font=f, fill=(210, 210, 210), anchor="lm")
    d.text((60, 960), f"Pre-open: {m['advances']} upar · {m['declines']} neeche", font=f,
           fill=g.CONFIG["accent_color"], anchor="lm")
    d.text((60, 1040), "Market 9:15 pe khulega", font=g.font("Anton-Regular.ttf", 64), fill="white", anchor="lm")
    foot(d, 1, total, "Data: NSE pre-open")
    slides = [img]
    img, d = base("OPENING HIGH", "Pre-open mein sabse upar")
    y = table_rows(d, m["high"], 360, row_h=80)
    d.text((60, y + 50), "OPENING LOW", font=g.font("Anton-Regular.ttf", 80), fill="white", anchor="lm")
    table_rows(d, m["low"], y + 120, row_h=80)
    foot(d, 2, total, "Data: NSE pre-open")
    slides.append(img)
    slides.append(disclaimer_slide(3, total))
    return slides


def make_preopen(state: dict, force: bool = False, data: dict | None = None) -> dict | None:
    """Data-only post (no AI) so it's ready within a minute of 9:09."""
    m = data or preopen_data(force=force)
    if m is None:
        return None
    print("Mode: market_preopen (data)")
    n = m["nifty"]
    trend = "positive" if pct_value(n["pct"]) > 0 else "negative" if pct_value(n["pct"]) < 0 else "flat"
    hi, lo = m["high"][0], m["low"][0]
    caption = (f"🔔 Pre-open update: Nifty {n['last']} ({n['pct']}) par khulne ke sanket, {trend} start.\n"
               f"Pre-open mein sabse upar {hi[0]} ({hi[2]}), sabse neeche {lo[0]} ({lo[2]}).\n"
               f"Market 9:15 pe khulega. Aaj aapko market kaisa lag raha hai? 👇\nNot investment advice.\n\n"
               f"Follow {g.CONFIG['handle']} for daily market updates.\n\n"
               f"#KhabarBawaal #StockMarket #Nifty #PreOpen #ShareMarketIndia #StockMarketIndia #DalalStreet")
    return {"slides": render_preopen(m), "caption": caption, "tag": "🔔 PRE-OPEN",
            "proof": {"state": "ok", "issues": [], "fixed": ["Data-only post: numbers straight from NSE"]},
            "headline": f"Pre-open: Nifty {n['last']} ({n['pct']})", "source": "NSE"}

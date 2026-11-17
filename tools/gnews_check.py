"""Google News public RSS from GitHub's computer: which sections answer, how many stories / sites. Read-only, posts nothing."""
import requests, feedparser
H = {"User-Agent": "khabarbawaal-studio news reader"}
B = "https://news.google.com/rss"
TESTS = {"Top stories (India)": f"{B}?hl=en-IN&gl=IN&ceid=IN:en",
         "India": f"{B}/topics/CAAqIQgKIhtDQkFTRGdvSUwyMHZNRE55YXpBU0FtVnVLQUFQAQ?hl=en-IN&gl=IN&ceid=IN:en",
         "World": f"{B}/headlines/section/topic/WORLD?hl=en-IN&gl=IN&ceid=IN:en",
         "Business": f"{B}/headlines/section/topic/BUSINESS?hl=en-IN&gl=IN&ceid=IN:en",
         "Entertainment": f"{B}/headlines/section/topic/ENTERTAINMENT?hl=en-IN&gl=IN&ceid=IN:en",
         "Sports": f"{B}/headlines/section/topic/SPORTS?hl=en-IN&gl=IN&ceid=IN:en",
         "Technology": f"{B}/headlines/section/topic/TECHNOLOGY?hl=en-IN&gl=IN&ceid=IN:en",
         "Search Bollywood 1d": f"{B}/search?q=Bollywood+when:1d&hl=en-IN&gl=IN&ceid=IN:en",
         "Search site:news18.com": f"{B}/search?q=site:news18.com+when:1d&hl=en-IN&gl=IN&ceid=IN:en",
         "Search site:indianexpress.com": f"{B}/search?q=site:indianexpress.com+when:1d&hl=en-IN&gl=IN&ceid=IN:en"}
for n, u in TESTS.items():
    try:
        r = requests.get(u, headers=H, timeout=20)
        e = feedparser.parse(r.content).entries
        print(f"GNEWS {n:30} http {r.status_code} items {len(e):3} sites {len({(x.get('source') or {}).get('title', '') for x in e}):3} | {e[0].title[:70] if e else ''}", flush=True)
    except Exception as x:
        print(f"GNEWS {n:30} ERR {str(x)[:70]}", flush=True)

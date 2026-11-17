"""Tests candidate news feeds from GitHub's computer (same reader name as the robot): http status, items, age of the newest item, picture found and downloadable. Read-only."""
import os, sys, time, calendar, requests, feedparser
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import generate as g
C = [("NDTV", "viral", "https://feeds.feedburner.com/ndtvnews-trending-news"), ("NDTV", "news", "https://feeds.feedburner.com/ndtvnews-latest"),
     ("NDTV", "entertainment", "https://feeds.feedburner.com/ndtvmovies-latest"), ("NDTV", "cricket", "https://feeds.feedburner.com/ndtvsports-cricket"),
     ("The Hindu", "news", "https://www.thehindu.com/news/national/feeder/default.rss"), ("The Hindu", "entertainment", "https://www.thehindu.com/entertainment/movies/feeder/default.rss"),
     ("Times of India", "viral", "https://timesofindia.indiatimes.com/rssfeedstopstories.cms"), ("Times of India", "news", "https://timesofindia.indiatimes.com/rssfeeds/-2128936835.cms"),
     ("Times of India", "cricket", "https://timesofindia.indiatimes.com/rssfeeds/4719161.cms"), ("Times of India", "tech", "https://timesofindia.indiatimes.com/rssfeeds/5880659.cms"),
     ("Mint", "news", "https://www.livemint.com/rss/news"), ("Mint", "tech", "https://www.livemint.com/rss/technology"),
     ("Business Standard", "news", "https://www.business-standard.com/rss/home_page_top_stories.rss"),
     ("Moneycontrol", "news", "https://www.moneycontrol.com/rss/latestnews.xml"), ("Moneycontrol", "business", "https://www.moneycontrol.com/rss/business.xml"),
     ("Firstpost", "news", "https://www.firstpost.com/commonfeeds/v1/mfp/rss/india.xml"), ("Firstpost", "entertainment", "https://www.firstpost.com/commonfeeds/v1/mfp/rss/entertainment.xml"),
     ("Firstpost", "viral", "https://www.firstpost.com/commonfeeds/v1/mfp/rss/trending.xml"),
     ("Outlook India", "news", "https://www.outlookindia.com/rss/main/magazine"), ("The Print", "politics", "https://theprint.in/feed/"),
     ("Scroll", "news", "https://feeds.feedburner.com/ScrollinArticles.rss"), ("Deccan Herald", "news", "https://www.deccanherald.com/feed"),
     ("Zee News", "news", "https://zeenews.india.com/rss/india-national-news.xml"), ("Zee News", "entertainment", "https://zeenews.india.com/rss/entertainment-news.xml"),
     ("DNA India", "news", "https://www.dnaindia.com/feeds/india.xml"), ("DNA India", "entertainment", "https://www.dnaindia.com/feeds/entertainment.xml"),
     ("Telegraph India", "news", "https://www.telegraphindia.com/rss/ts/india"), ("Tribune", "news", "https://www.tribuneindia.com/rss/feed?catId=1"),
     ("Filmibeat", "entertainment", "https://www.filmibeat.com/rss/filmibeat-fb.xml"), ("Bollywood Life", "entertainment", "https://www.bollywoodlife.com/feed/"),
     ("Bollywood Hungama", "viral", "https://www.bollywoodhungama.com/rss/features.xml"), ("India Today", "entertainment", "https://www.indiatoday.in/rss/1206550"),
     ("India Today", "cricket", "https://www.indiatoday.in/rss/1206522"), ("India Today", "tech", "https://www.indiatoday.in/rss/1206514"),
     ("Hindustan Times", "viral", "https://www.hindustantimes.com/feeds/rss/lifestyle/rssfeed.xml"), ("Hindustan Times", "cricket", "https://www.hindustantimes.com/feeds/rss/cricket/rssfeed.xml"),
     ("ESPNcricinfo", "cricket", "https://www.espncricinfo.com/rss/content/story/feeds/0.xml"), ("Cricbuzz", "cricket", "https://www.cricbuzz.com/rss-feed/cricket-news"),
     ("Sportstar", "cricket", "https://sportstar.thehindu.com/feeder/default.rss"), ("BBC", "world", "https://feeds.bbci.co.uk/news/world/rss.xml"),
     ("BBC", "news", "https://feeds.bbci.co.uk/news/world/asia/india/rss.xml"), ("The Guardian", "world", "https://www.theguardian.com/world/india/rss"),
     ("Al Jazeera", "world", "https://www.aljazeera.com/xml/rss/all.xml"), ("TechCrunch", "tech", "https://techcrunch.com/feed/"), ("The Verge", "tech", "https://www.theverge.com/rss/index.xml"),
     ("NDTV Gadgets", "tech", "https://feeds.feedburner.com/gadgets360-latest"), ("Inshorts", "viral", "https://inshorts.com/en/read"),
     ("Variety", "entertainment", "https://variety.com/feed/"), ("Hollywood Reporter", "entertainment", "https://www.hollywoodreporter.com/feed/"),
     ("ET Now", "business", "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"), ("ET Entertainment", "entertainment", "https://economictimes.indiatimes.com/industry/media/entertainment/rssfeeds/13357212.cms"),
     ("Mid-day", "entertainment", "https://www.mid-day.com/rss/entertainment-news"), ("Pinkvilla", "viral", "https://www.pinkvilla.com/feed"), ("Lokmat", "news", "https://www.lokmattimes.com/rss/")]
now = time.time()
for name, cat, url in C:
    try:
        r = requests.get(url, headers={"User-Agent": g.UA}, timeout=20)
        e = feedparser.parse(r.content).entries
        ages = []
        for x in e[:10]:
            p = x.get("published_parsed") or x.get("updated_parsed")
            if p:
                ages.append((now - calendar.timegm(p)) / 3600)
        img = g.rss_image(e[0]) if e else ""
        got = bool(img and g.download_image(img) is not None)
        print(f"CAND {'OK ' if r.status_code == 200 and e and ages and min(ages) < 12 and got else 'no '} {name:18} {cat:13} http {r.status_code} items {len(e):3} newest {min(ages) if ages else -1:5.1f}h pic {'yes' if got else 'no'} | {url}", flush=True)
    except Exception as ex:
        print(f"CAND no  {name:18} {cat:13} ERR {str(ex)[:50]} | {url}", flush=True)

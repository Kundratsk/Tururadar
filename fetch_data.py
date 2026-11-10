#!/usr/bin/env python3
"""Kogub uudised ja turuandmed ning salvestab need faili data.json (samasse kausta)."""
import html
import json
import os
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}

FEEDS = [
    ("CNBC", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    ("Yahoo Finance", "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%5EGSPC,%5EIXIC,%5EDJI,NVDA,AAPL,MSFT,AMZN,TSLA&region=US&lang=en-US"),
    ("BBC Business", "https://feeds.bbci.co.uk/news/business/rss.xml"),
]

# (nimi, Yahoo sümbol, kas indeks, komakohti)
SYMBOLS = [
    ("S&P 500", "^GSPC", True, 2), ("Nasdaq", "^IXIC", True, 2),
    ("Dow Jones", "^DJI", True, 2), ("Euro Stoxx 50", "^STOXX50E", True, 2),
    ("VIX (hirm)", "^VIX", False, 2), ("Nafta (WTI)", "CL=F", False, 2),
    ("Kuld", "GC=F", False, 2), ("10a USA intress", "^TNX", False, 2),
]


def get(url, timeout=15):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def clean(text):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text or ""))).strip()


def iso_date(s):
    try:
        return parsedate_to_datetime(s).astimezone(timezone.utc).isoformat()
    except Exception:
        return datetime.now(timezone.utc).isoformat()


MEDIA = "{http://search.yahoo.com/mrss/}"


def find_image(it):
    for tag in ("thumbnail", "content"):
        for el in it.iter(MEDIA + tag):
            url = el.get("url") or ""
            typ = el.get("type") or ""
            if url and (tag == "thumbnail" or el.get("medium") == "image" or typ.startswith("image")
                        or re.search(r"\.(jpe?g|png|webp)", url, re.I)):
                return big(url)
    enc = it.find("enclosure")
    if enc is not None and (enc.get("type") or "").startswith("image"):
        return big(enc.get("url") or "")
    m = re.search(r'<img[^>]+src="([^"]+)"', it.findtext("description") or "")
    return big(m.group(1)) if m else ""


def big(url):
    return url.replace("/240/", "/800/") if "ichef.bbci.co.uk" in url else url


def fetch_feed(feed):
    name, url = feed
    root = ET.fromstring(get(url))
    out = []
    for it in root.iter("item"):
        title = clean(it.findtext("title"))
        if not title:
            continue
        out.append({
            "title": title,
            "link": (it.findtext("link") or "#").strip(),
            "src": name,
            "date": iso_date(it.findtext("pubDate")),
            "desc": clean(it.findtext("description"))[:200],
            "img": find_image(it),
        })
    return out


def fetch_quote(s):
    name, sym, is_index, digits = s
    for host in ("query1", "query2"):
        try:
            url = f"https://{host}.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}?range=1d&interval=1d"
            meta = json.loads(get(url))["chart"]["result"][0]["meta"]
            prev = meta.get("chartPreviousClose") or meta.get("previousClose")
            price = meta["regularMarketPrice"]
            pct = (price - prev) / prev * 100 if prev else None
            return {"name": name, "price": price, "pct": pct, "index": is_index, "digits": digits}
        except Exception as e:
            print(f"  {name} ({host}): {e}")
    return {"name": name, "price": None}


def fetch_extra():
    out = []
    try:
        c = json.loads(get("https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum&vs_currencies=usd&include_24hr_change=true"))
        for key, nm in (("bitcoin", "Bitcoin"), ("ethereum", "Ethereum")):
            out.append({"name": nm, "price": c[key]["usd"], "pct": c[key]["usd_24h_change"], "prefix": "$", "digits": 0})
    except Exception as e:
        print("  CoinGecko:", e)
    try:
        f = json.loads(get("https://api.frankfurter.app/latest?from=EUR&to=USD"))
        out.append({"name": "EUR/USD", "price": f["rates"]["USD"], "pct": None, "digits": 4})
    except Exception as e:
        print("  Frankfurter:", e)
    return out


def main():
    failed, news = [], []
    with ThreadPoolExecutor(max_workers=8) as ex:
        feed_jobs = [(f[0], ex.submit(fetch_feed, f)) for f in FEEDS]
        quote_jobs = [ex.submit(fetch_quote, s) for s in SYMBOLS]
        extra_job = ex.submit(fetch_extra)
        for name, job in feed_jobs:
            try:
                news += job.result()
            except Exception as e:
                print(f"  {name}: {e}")
                failed.append(name)
        quotes = [j.result() for j in quote_jobs] + extra_job.result()

    seen, unique = set(), []
    for n in sorted(news, key=lambda x: x["date"], reverse=True):
        key = n["title"].lower()[:60]
        if key not in seen:
            seen.add(key)
            unique.append(n)

    data = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "quotes": quotes,
        "news": unique[:80],
        "failed": failed,
    }
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    ok = sum(1 for q in quotes if q.get("price") is not None)
    print(f"Valmis: {len(data['news'])} uudist, {ok}/{len(quotes)} hinda -> {path}")


if __name__ == "__main__":
    main()

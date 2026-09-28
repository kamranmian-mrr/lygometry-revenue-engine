import csv, urllib.request, xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "signals.csv"

FEEDS = {
    "Hacker News": "https://hnrss.org/newest",
    "Google News - AI": "https://news.google.com/rss/search?q=AI+problem+tool+workflow",
    "Google News - business": "https://news.google.com/rss/search?q=business+problem+software+workflow",
}

def parse_feed(name, url):
    req = urllib.request.Request(url, headers={"User-Agent":"LygometryRevenueEngine/0.1"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = r.read()
    root = ET.fromstring(data)
    rows=[]
    for item in root.findall(".//item")[:30]:
        title = item.findtext("title","").strip()
        link = item.findtext("link","").strip()
        desc = item.findtext("description","").strip()
        if title:
            rows.append({
                "source": name,
                "title": title,
                "link": link,
                "description": description if description else "",
                "captured_at": datetime.now(timezone.utc).isoformat()
            })
    return rows

def main():
    OUT.parent.mkdir(exist_ok=True)
    rows=[]
    for name,url in FEEDS.items():
        try:
            rows += parse_feed(name,url)
        except Exception as e:
            print("WARN", name, e)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w=csv.DictWriter(f, fieldnames=["source","title","link","description","captured_at"])
        w.writeheader(); w.writerows(rows)
    print(f"Captured {len(rows)} signals")

if __name__=="__main__":
    main()

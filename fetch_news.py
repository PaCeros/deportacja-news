#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
deportacja.online — robot aktualności

Pobiera wpisy z kanałów RSS i stron aktualności gov.pl, filtruje po słowach
kluczowych i zapisuje wynik do news.json.

Uruchamiany automatycznie przez GitHub Actions
(plik .github/workflows/aktualnosci.yml). Nie wymaga żadnych bibliotek —
wystarczy standardowy Python 3.
"""

import json
import re
import html
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

# ─── KONFIGURACJA (to jedyne miejsce, które edytujesz) ────────────────────

# Kanały RSS: (nazwa źródła, adres kanału).
# Pełną listę kanałów Straży Granicznej znajdziesz na:
#   https://www.strazgraniczna.pl/pl/rss
# Kliknij prawym przyciskiem na kanał "Aktualności" -> "Kopiuj adres linku"
# i wklej go poniżej w miejsce obecnego adresu, jeśli ten nie zadziała.
RSS_FEEDS = [
    ("Straż Graniczna", "https://www.strazgraniczna.pl/pl/rss/8,dok.xml"),
]

# Strony aktualności na gov.pl (nie mają RSS, więc czytamy listę wpisów):
# (nazwa źródła, adres strony z listą, prefiks linków artykułów)
GOVPL_PAGES = [
    ("MSWiA", "https://www.gov.pl/web/mswia/aktualnosci", "/web/mswia/"),
    ("UdSC", "https://www.gov.pl/web/udsc/aktualnosci", "/web/udsc/"),
]

# Wpis trafia do news.json tylko, gdy tytuł lub opis zawiera któreś słowo.
# Pusta lista [] = bierz wszystkie wpisy.
KEYWORDS = [
    "migra", "deporta", "cudzoziem", "powrot", "powrót", "granic",
    "azyl", "uchodź", "readmisj", "wydal", "legaliz", "wiz",
]

MAX_ITEMS = 12          # ile najnowszych wpisów zapisać
OUTPUT = "news.json"    # nazwa pliku wynikowego

# ──────────────────────────────────────────────────────────────────────────

UA = "Mozilla/5.0 (compatible; deportacja.online; +https://deportacja.online)"


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def clean(text):
    """Usuwa znaczniki HTML i nadmiarowe spacje."""
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def matches(item):
    if not KEYWORDS:
        return True
    hay = (item["title"] + " " + item["summary"]).lower()
    return any(k in hay for k in KEYWORDS)


def parse_rss(source, url):
    items = []
    root = ElementTree.fromstring(fetch(url))
    for it in root.iter("item"):
        title = clean(it.findtext("title"))
        link = (it.findtext("link") or "").strip()
        desc = clean(it.findtext("description"))[:220]
        date_pl, sort = "", ""
        raw = it.findtext("pubDate")
        if raw:
            try:
                d = parsedate_to_datetime(raw).astimezone(ZoneInfo("Europe/Warsaw"))
                sort = d.isoformat()
                date_pl = d.strftime("%d.%m.%Y · %H:%M")
            except Exception:
                pass
        if title and link:
            items.append({"title": title, "link": link, "date": date_pl,
                          "sort": sort, "source": source, "summary": desc})
    return items


def parse_govpl(source, url, prefix):
    """Prosty parser listy aktualności w serwisie gov.pl (brak RSS)."""
    page = fetch(url).decode("utf-8", "ignore")
    items, seen = [], set()
    pattern = r'<a[^>]+href="(' + re.escape(prefix) + r'[^"]+)"[^>]*>(.*?)</a>'
    for m in re.finditer(pattern, page, re.S):
        link = "https://www.gov.pl" + m.group(1)
        block = m.group(2)
        title_m = re.search(r'class="title"[^>]*>(.*?)<', block, re.S)
        title = clean(title_m.group(1) if title_m else block)
        date_m = re.search(r"(\d{2}\.\d{2}\.\d{4})", block)
        date_pl = date_m.group(1) if date_m else ""
        sort = ""
        if date_pl:
            try:
                sort = datetime.strptime(date_pl, "%d.%m.%Y") \
                    .replace(tzinfo=timezone.utc).isoformat()
            except ValueError:
                pass
        if len(title) > 25 and link not in seen and "/aktualnosci" not in link:
            seen.add(link)
            items.append({"title": title[:180], "link": link, "date": date_pl,
                          "sort": sort, "source": source, "summary": ""})
    return items


def main():
    collected = []
    for source, url in RSS_FEEDS:
        try:
            got = [i for i in parse_rss(source, url) if matches(i)]
            print(f"[OK]   RSS {source}: {len(got)} wpisów")
            collected += got
        except Exception as e:
            print(f"[BŁĄD] RSS {source}: {e}")
    for source, url, prefix in GOVPL_PAGES:
        try:
            got = [i for i in parse_govpl(source, url, prefix) if matches(i)]
            print(f"[OK]   gov.pl {source}: {len(got)} wpisów")
            collected += got
        except Exception as e:
            print(f"[BŁĄD] gov.pl {source}: {e}")

    collected.sort(key=lambda i: i["sort"], reverse=True)
    out = [{k: i[k] for k in ("title", "link", "date", "source", "summary")}
           for i in collected[:MAX_ITEMS]]

    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"Zapisano {len(out)} wpisów do {OUTPUT}")


if __name__ == "__main__":
    main()

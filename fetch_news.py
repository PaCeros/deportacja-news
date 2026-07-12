#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
deportacja.online — robot aktualności

Dla każdego urzędu pobiera najnowsze wpisy dwiema metodami:
 1) bezpośrednio z jego strony aktualności (scraping HTML),
 2) awaryjnie z Google News RSS (gdy metoda 1 nic nie zwróci).
Wynik zapisuje do news.json (po PER_SOURCE najnowszych wpisów z urzędu).

Uruchamiany automatycznie przez GitHub Actions
(plik .github/workflows/aktualnosci.yml). Nie wymaga żadnych bibliotek —
wystarczy standardowy Python 3.
"""

import json
import re
import html
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

# ─── KONFIGURACJA (to jedyne miejsce, które edytujesz) ────────────────────

# Źródła: (nazwa, typ, adres, prefiks linków artykułów)
# typ "intracom" = strony jak strazgraniczna.pl; typ "govpl" = serwis gov.pl
SOURCES = [
    ("Straż Graniczna", "intracom",
     "https://www.strazgraniczna.pl/pl/aktualnosci", "/pl/aktualnosci/"),
    ("MSWiA", "govpl",
     "https://www.gov.pl/web/mswia/aktualnosci", "/web/mswia/"),
    ("UdSC", "govpl",
     "https://www.gov.pl/web/udsc/aktualnosci", "/web/udsc/"),
]

# Awaryjne kanały Google News (użyte tylko, gdy scraping nic nie zwróci):
GNEWS_QUERY = {
    "Straż Graniczna": "site:strazgraniczna.pl",
    "MSWiA": "site:gov.pl/web/mswia",
    "UdSC": "site:gov.pl/web/udsc",
}

# Filtr słów kluczowych — dotyczy TYLKO urzędów z FILTERED_SOURCES
# (o mieszanej tematyce). SG i UdSC zajmują się migracją z definicji.
FILTERED_SOURCES = {"MSWiA"}
KEYWORDS = [
    "migra", "deporta", "cudzoziem", "powrot", "powrót", "granic",
    "azyl", "uchodź", "readmisj", "wydal", "legaliz", "wiz",
]

PER_SOURCE = 2          # ile najnowszych wpisów z każdego urzędu
MAX_ITEMS = 12          # łączny limit wpisów w news.json
OUTPUT = "news.json"    # nazwa pliku wynikowego

# ──────────────────────────────────────────────────────────────────────────

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
WARSAW = ZoneInfo("Europe/Warsaw")


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept-Language": "pl"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def clean(text):
    """Usuwa znaczniki HTML i nadmiarowe spacje."""
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def matches(item):
    if item["source"] not in FILTERED_SOURCES or not KEYWORDS:
        return True
    hay = (item["title"] + " " + item["summary"]).lower()
    return any(k in hay for k in KEYWORDS)


def make_item(source, title, link, date_pl="", sort="", summary=""):
    return {"title": title[:180], "link": link, "date": date_pl,
            "sort": sort, "source": source, "summary": summary[:220]}


def date_near(page, pos, radius=500):
    """Szuka daty dd.mm.rrrr w pobliżu pozycji w HTML."""
    window = page[max(0, pos - radius): pos + radius]
    m = re.search(r"(\d{2})\.(\d{2})\.(20\d{2})", window)
    return m.group(0) if m else ""


def sort_key_from(date_pl):
    for fmt in ("%d.%m.%Y · %H:%M", "%d.%m.%Y"):
        try:
            return datetime.strptime(date_pl, fmt) \
                .replace(tzinfo=WARSAW).isoformat()
        except ValueError:
            continue
    return ""


def parse_intracom(source, url, prefix):
    """Strony typu strazgraniczna.pl: linki /pl/aktualnosci/12345,Tytul.html"""
    page = fetch(url).decode("utf-8", "ignore")
    base = re.match(r"https?://[^/]+", url).group(0)
    items, seen = [], set()
    pattern = (r'<a[^>]+href="(' + re.escape(prefix) +
               r'\d+,[^"]+\.html)"[^>]*>(.*?)</a>')
    for m in re.finditer(pattern, page, re.S):
        link = base + m.group(1)
        if link in seen:
            continue
        title_attr = re.search(r'title="([^"]+)"', m.group(0))
        title = clean(title_attr.group(1) if title_attr else m.group(2))
        if len(title) < 20:
            continue
        seen.add(link)
        date_pl = date_near(page, m.start())
        items.append(make_item(source, title, link, date_pl,
                               sort_key_from(date_pl)))
    return items


def parse_govpl(source, url, prefix):
    """Listy aktualności w serwisie gov.pl."""
    page = fetch(url).decode("utf-8", "ignore")
    items, seen = [], set()
    # linki względne i bezwzględne
    pattern = (r'<a[^>]+href="(?:https?://www\.gov\.pl)?(' +
               re.escape(prefix) + r'[a-z0-9][^"?#]*)"[^>]*>(.*?)</a>')
    for m in re.finditer(pattern, page, re.S):
        link = "https://www.gov.pl" + m.group(1)
        if link in seen or "/aktualnosci" in m.group(1):
            continue
        block = m.group(2)
        title_m = re.search(r'class="title"[^>]*>(.*?)</', block, re.S)
        title = clean(title_m.group(1) if title_m else block)
        date_pl = date_near(page, m.start(), 800)
        # artykuł ma sensownie długi tytuł ORAZ datę w pobliżu
        # (odfiltrowuje linki nawigacyjne do podstron urzędu)
        if len(title) < 30 or not date_pl:
            continue
        seen.add(link)
        items.append(make_item(source, title, link, date_pl,
                               sort_key_from(date_pl)))
    return items


def parse_gnews(source):
    """Awaryjnie: Google News RSS (zawsze działa, linki przez przekierowanie)."""
    q = urllib.parse.quote(GNEWS_QUERY[source])
    url = (f"https://news.google.com/rss/search?q={q}"
           "&hl=pl&gl=PL&ceid=PL:pl")
    items = []
    root = ElementTree.fromstring(fetch(url))
    for it in root.iter("item"):
        title = clean(it.findtext("title"))
        title = re.sub(r"\s*-\s*[^-]+$", "", title)  # utnij " - nazwa serwisu"
        link = (it.findtext("link") or "").strip()
        date_pl, sort = "", ""
        raw = it.findtext("pubDate")
        if raw:
            try:
                d = parsedate_to_datetime(raw).astimezone(WARSAW)
                sort = d.isoformat()
                date_pl = d.strftime("%d.%m.%Y · %H:%M")
            except Exception:
                pass
        if title and link:
            items.append(make_item(source, title, link, date_pl, sort))
    return items


def load_previous():
    """Poprzedni news.json — pamięć robota: wcześniejsze wpisy i ich godziny."""
    try:
        with open(OUTPUT, encoding="utf-8") as f:
            prev = json.load(f)
        for i in prev:
            i.setdefault("summary", "")
            i["sort"] = sort_key_from(i.get("date", ""))
        return prev
    except Exception:
        return []


def main():
    collected = []
    for source, kind, url, prefix in SOURCES:
        got = []
        try:
            parser = parse_intracom if kind == "intracom" else parse_govpl
            got = [i for i in parser(source, url, prefix) if matches(i)]
            print(f"[OK]   {source} (strona): {len(got)} wpisów")
        except Exception as e:
            print(f"[BŁĄD] {source} (strona): {e}")
        if not got:
            try:
                got = [i for i in parse_gnews(source) if matches(i)]
                print(f"[OK]   {source} (Google News, awaryjnie): {len(got)} wpisów")
            except Exception as e:
                print(f"[BŁĄD] {source} (Google News): {e}")
        collected += got

    # Godziny: wpis znany z poprzedniego news.json zachowuje godzinę
    # pierwszego wykrycia; nowy wpis bez godziny dostaje bieżącą.
    previous = load_previous()
    prev_dates = {i["link"]: i.get("date", "") for i in previous}
    now_warsaw = datetime.now(WARSAW)
    now_pl = now_warsaw.strftime("%H:%M")
    for i in collected:
        if "·" in i["date"]:
            continue  # ma już godzinę publikacji
        prev = prev_dates.get(i["link"], "")
        if "·" in prev:
            i["date"] = prev
        elif i["date"]:
            i["date"] = i["date"] + " · " + now_pl
        else:
            i["date"] = now_warsaw.strftime("%d.%m.%Y") + " · " + now_pl
        i["sort"] = sort_key_from(i["date"]) or i["sort"]

    # Pamięć robota: dołącz wpisy z poprzedniego news.json, których teraz
    # nie pobrano (nowy news wypycha najstarszy, a znane wpisy zostają).
    fetched_links = {i["link"] for i in collected}
    collected += [i for i in previous if i["link"] not in fetched_links]

    collected.sort(key=lambda i: i["sort"], reverse=True)

    # maks. PER_SOURCE najnowszych wpisów z każdego urzędu
    final, per_source = [], {}
    for i in collected:
        taken = per_source.get(i["source"], 0)
        if taken < PER_SOURCE:
            per_source[i["source"]] = taken + 1
            final.append(i)

    out = [{k: i[k] for k in ("title", "link", "date", "source", "summary")}
           for i in final[:MAX_ITEMS]]

    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"Zapisano {len(out)} wpisów do {OUTPUT}")


if __name__ == "__main__":
    main()

"""Haber Takip toplayıcısı.

Kaynaklardan (RSS, Google News, Telegram) haberleri ve piyasa verilerini toplar,
haberleri sınıflar, aynı olayı anlatanları birleştirir ve uygulamanın okuyacağı
JSON dosyalarını yazar.

Kullanım:
    python collector/collect.py --out site/data --store .cache/store.json
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import email.utils
import hashlib
import html
import json
import re
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

import requests
from bs4 import BeautifulSoup

HERE = Path(__file__).resolve().parent
UTC = dt.timezone.utc
TRT = dt.timezone(dt.timedelta(hours=3))  # Türkiye saati
NOW = dt.datetime.now(UTC)
UA = {"User-Agent": "Mozilla/5.0 (HaberTakip; +https://github.com/mbedilsah-tech) Python-requests",
      "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8"}
STORE_DAYS = 15
FEED_HOURS = 36

CFG = json.loads((HERE / "sources.json").read_text(encoding="utf-8"))
META: dict = {"sources": {}, "errors": {}}

# ---------------------------------------------------------------- metin yardımcıları
STOP = set("""ve ile bir bu da de için olarak olan daha en çok ama gibi göre sonra önce kadar
ya veya ki mi mı mu mü ne şu o ise hem her yeni son dair üzerine karşı arasında the a an of to in on
for and with at by from as is are was were be has have after over says said new""".split())


def trlower(s: str) -> str:
    return s.replace("I", "ı").replace("İ", "i").lower()


def tokens(s: str) -> list[str]:
    return re.findall(r"[a-zçğıöşüâîû0-9]+", trlower(s))


def stems(s: str) -> set[str]:
    return {t[:6] for t in tokens(s) if len(t) >= 3 and t not in STOP}


def clean_html(s: str | None) -> str:
    if not s:
        return ""
    s = BeautifulSoup(s, "html.parser").get_text(" ")
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def compile_kws(kws: list[str]):
    out = []
    for k in kws:
        kl = trlower(k)
        if kl.startswith("="):
            out.append(("exact", kl[1:]))
            continue
        out.append(("phrase", kl) if " " in kl else ("exact" if len(kl) <= 3 else "prefix", kl))
    return out


def kw_hits(text_lower: str, toks: list[str], compiled) -> int:
    n = 0
    for kind, kw in compiled:
        if kind == "phrase":
            n += kw in text_lower
        elif kind == "exact":
            n += kw in toks
        else:
            n += any(t.startswith(kw) for t in toks)
    return n


CAT_KWS = {c: compile_kws(v) for c, v in CFG["categories"].items()}
FRONT_KWS = {f: compile_kws(v) for f, v in CFG["fronts"].items()}
RTE_KWS = {f: compile_kws(v) for f, v in CFG["rte_topics"].items()}
EXCLUDE = compile_kws(CFG["exclude"])
OFFICIAL = set(CFG["official_sources"])


def parse_date(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    s = s.strip()
    try:
        d = email.utils.parsedate_to_datetime(s)
    except (TypeError, ValueError):
        try:
            d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=UTC)
    return d.astimezone(UTC)


def iso(d: dt.datetime) -> str:
    return d.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def item_id(link: str, title: str) -> str:
    return hashlib.sha1((link or title).encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- kaynak okuyucular
def fetch(url: str, timeout: int = 25, tries: int = 3) -> bytes:
    for n in range(tries):
        try:
            r = requests.get(url, headers=UA, timeout=timeout)
            r.raise_for_status()
            return r.content
        except (requests.ConnectionError, requests.Timeout) as e:
            if n == tries - 1:
                raise
            time.sleep(3 * (n + 1))
    raise RuntimeError("unreachable")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_feed(raw: bytes, source: str, official: bool = False, tag: str | None = None) -> list[dict]:
    """RSS 2.0, RSS 1.0 (RDF) ve Atom'u standart kütüphaneyle okur."""
    root = ET.fromstring(raw)
    out = []
    for el in root.iter():
        if _local(el.tag) not in ("item", "entry"):
            continue
        f: dict = {}
        img = None
        src_name = None
        for ch in el:
            name = _local(ch.tag)
            if name == "title":
                f["title"] = (ch.text or "").strip()
            elif name == "link":
                f.setdefault("link", (ch.text or ch.get("href") or "").strip())
            elif name in ("description", "summary", "encoded", "content") and ch.text:
                if len(ch.text) > len(f.get("desc", "")):
                    f["desc"] = ch.text
            elif name in ("pubDate", "date", "updated", "published"):
                f.setdefault("date", ch.text)
            elif name in ("content", "thumbnail") and ch.get("url") and not img:
                if (ch.get("medium") in (None, "image")) and not (ch.get("type") or "").startswith("video"):
                    img = ch.get("url")
            elif name == "enclosure" and (ch.get("type") or "").startswith("image") and not img:
                img = ch.get("url")
            elif name == "source":
                src_name = (ch.text or "").strip()
        title = clean_html(f.get("title"))
        if not title:
            continue
        desc = clean_html(f.get("desc"))
        if not img and f.get("desc"):
            m = re.search(r'<img[^>]+src="([^"]+)"', f["desc"])
            img = m.group(1) if m else None
        name = source
        if src_name:  # Google News: "Başlık - Kaynak"
            name = src_name
            title = re.sub(r"\s+-\s+" + re.escape(src_name) + r"\s*$", "", title)
            if desc.startswith(title):
                desc = ""
        d = parse_date(f.get("date")) or NOW
        out.append({
            "id": item_id(f.get("link", ""), title), "title": title, "desc": desc[:500],
            "url": f.get("link", ""), "src": name, "via": source, "ts": iso(d), "img": img,
            "official": official or name in OFFICIAL, "kind": "rss", "tag": tag,
        })
    return out


def read_rss(s: dict) -> list[dict]:
    return parse_feed(fetch(s["url"]), s["name"], s.get("official", False))


def read_gnews(s: dict) -> list[dict]:
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": s["q"], "hl": "tr", "gl": "TR", "ceid": "TR:tr"})
    return parse_feed(fetch(url), "Google Haberler", tag=s.get("tag"))


def read_telegram(s: dict) -> list[dict]:
    soup = BeautifulSoup(fetch(f"https://t.me/s/{s['channel']}"), "html.parser")
    out = []
    for w in soup.select(".tgme_widget_message"):
        txt_el = w.select_one(".tgme_widget_message_text")
        a = w.select_one("a.tgme_widget_message_date")
        t = w.select_one("time[datetime]")
        if not (txt_el and a):
            continue
        text = re.sub(r"\s+", " ", txt_el.get_text(" ")).strip()
        if len(text) < 25:
            continue
        first = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0]
        title = first if len(first) <= 160 else first[:157].rsplit(" ", 1)[0] + "…"
        img, media = None, None
        for sel, kind in ((".tgme_widget_message_photo_wrap", "foto"), (".tgme_widget_message_video_thumb", "video")):
            el = w.select_one(sel)
            if el and "background-image" in (el.get("style") or ""):
                m = re.search(r"url\('?([^')]+)'?\)", el["style"])
                if m:
                    img, media = m.group(1), kind
                    break
        if not media and w.select_one(".tgme_widget_message_video_player"):
            media = "video"
        d = parse_date(t["datetime"]) if t else NOW
        out.append({
            "id": item_id(a["href"], title), "title": title, "desc": text[:500], "url": a["href"],
            "src": s["name"], "via": "Telegram", "ts": iso(d or NOW), "img": img, "media": media,
            "official": s["name"] in OFFICIAL, "kind": "telegram", "tag": None,
        })
    return out


def collect_items() -> list[dict]:
    jobs = [(f"RSS: {s['name']}", read_rss, s) for s in CFG["rss"]]
    jobs += [(f"Google: {s['q']}", read_gnews, s) for s in CFG["gnews"]]
    jobs += [(f"Telegram: {s['name']}", read_telegram, s) for s in CFG["telegram"]]
    items: list[dict] = []
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(fn, s): label for label, fn, s in jobs}
        for fut in cf.as_completed(futs):
            label = futs[fut]
            try:
                got = fut.result()
                META["sources"][label] = len(got)
                items += got
            except Exception as e:  # bir kaynağın bozulması diğerlerini durdurmasın
                META["sources"][label] = 0
                META["errors"][label] = f"{type(e).__name__}: {str(e)[:160]}"
    return items


# ---------------------------------------------------------------- depolama
def load_store(path: Path) -> dict[str, dict]:
    try:
        return {i["id"]: i for i in json.loads(path.read_text(encoding="utf-8"))}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def merge_store(store: dict[str, dict], new: list[dict]) -> list[dict]:
    cutoff = NOW - dt.timedelta(days=STORE_DAYS)
    for it in new:
        old = store.get(it["id"])
        if old:
            it["ts"] = old["ts"]  # ilk görülme zamanını koru
        store[it["id"]] = it
    return [i for i in store.values() if parse_date(i["ts"]) >= cutoff and parse_date(i["ts"]) <= NOW + dt.timedelta(hours=1)]


# ---------------------------------------------------------------- sınıflama ve birleştirme
def annotate(it: dict) -> dict:
    text = f"{it['title']} {it['desc'][:300]}"
    tl, tk = trlower(text), tokens(text)
    title_tk = tokens(it["title"])
    it["_excluded"] = kw_hits(trlower(it["title"]), title_tk, EXCLUDE) > 0
    ttl = trlower(it["title"])
    scores = {c: 2 * kw_hits(ttl, title_tk, kws) + kw_hits(tl, tk, kws) for c, kws in CAT_KWS.items()}
    it["_cats"] = scores
    it["_fronts"] = [f for f, kws in FRONT_KWS.items() if kw_hits(tl, tk, kws)]
    it["_sig"] = stems(it["title"])
    return it


def cluster(items: list[dict]) -> list[dict]:
    items = sorted(items, key=lambda i: i["ts"], reverse=True)
    clusters: list[dict] = []
    index: dict[str, set[int]] = defaultdict(set)
    window = dt.timedelta(hours=36)
    for it in items:
        if it["_excluded"] or len(it["_sig"]) < 3:
            continue
        t = parse_date(it["ts"])
        cand = Counter()
        for s in it["_sig"]:
            for ci in index[s]:
                cand[ci] += 1
        best, best_score = None, 0.0
        for ci, shared in cand.most_common(15):
            c = clusters[ci]
            if shared < 3 or abs(c["_t"] - t) > window:
                continue
            score = shared / min(len(c["_sig"]), len(it["_sig"]))
            if score >= 0.5 and score > best_score:
                best, best_score = ci, score
        if best is None:
            clusters.append({"items": [it], "_sig": set(it["_sig"]), "_t": t})
            ci = len(clusters) - 1
        else:
            clusters[best]["items"].append(it)
            clusters[best]["_sig"] |= it["_sig"]
            ci = best
        for s in it["_sig"]:
            index[s].add(ci)
    return [finish(c) for c in clusters]


CAT_ORDER = ["war", "def", "eco", "pol", "geo"]


def finish(c: dict) -> dict:
    its = c["items"]
    votes = Counter()
    for i in its:  # her haber en yüksek puanlı kategorisine bir oy verir
        sc = i["_cats"]
        if any(sc.values()):
            votes[max(CAT_ORDER, key=lambda k: (sc[k], -CAT_ORDER.index(k)))] += 1
    cat = max(CAT_ORDER, key=lambda k: (votes[k], -CAT_ORDER.index(k))) if votes else "gen"
    fronts = Counter(f for i in its for f in i["_fronts"])
    rep = max(its, key=lambda i: (i["official"], i["kind"] == "rss" and i["via"] != "Google Haberler", len(i["desc"])))
    srcs, seen = [], set()
    for i in sorted(its, key=lambda i: i["ts"]):
        if i["src"] in seen:
            continue
        seen.add(i["src"])
        srcs.append({"name": i["src"], "url": i["url"], "ts": i["ts"], "official": i["official"]})
    times = sorted(i["ts"] for i in its)
    media = next(((i.get("img"), i.get("media") or "foto", i["url"]) for i in its if i.get("img")), None)
    if not media:
        media = next(((None, i["media"], i["url"]) for i in its if i.get("media")), None)
    n = len(srcs)
    urls = [i["url"] for i in sorted(its, key=lambda i: (i["via"] == "Google Haberler", i["kind"] == "telegram")) if i["url"]]
    return {"_urls": urls[:6],
        "id": rep["id"], "title": rep["title"], "summary": rep["desc"][:420] if rep["desc"] != rep["title"] else "",
        "cat": cat, "fronts": [f for f, _ in fronts.most_common(2)], "n": n,
        "first": times[0], "last": times[-1], "sources": srcs[:10],
        "official": any(s["official"] for s in srcs),
        "unverified": n == 1 and all(i["kind"] == "telegram" for i in its) and not its[0]["official"],
        "img": media[0] if media else None, "media": media[1] if media else None, "media_url": media[2] if media else None,
        "rte": any(i.get("tag") == "rte" for i in its) or any("erdoğan" in trlower(i["title"]) for i in its),
    }


def hours_ago(ts: str) -> float:
    return (NOW - parse_date(ts)).total_seconds() / 3600


def rank(c: dict) -> float:
    return c["n"] * 3 + (2 if c["official"] else 0) - hours_ago(c["last"]) * 0.6


def public(c: dict) -> dict:
    return {k: v for k, v in c.items() if not k.startswith("_")}


# ---------------------------------------------------------------- özet ve görsel (haberin asıl sayfasından)
ENRICH_BUDGET = 220         # her çalıştırmada en fazla bu kadar yeni sayfa açılır
ENRICH: dict[str, dict] = {}
ENRICH_STATS = Counter()


def gn_decode(url: str) -> str | None:
    """Google Haberler yönlendirme linkini asıl haber adresine çevirir."""
    try:
        from googlenewsdecoder import gnewsdecoder  # varsa hazır kütüphane
        r = gnewsdecoder(url)
        if r.get("status") and r.get("decoded_url"):
            return r["decoded_url"]
    except Exception:
        pass
    try:  # yedek: aynı yöntemin elle uygulanışı
        aid = urllib.parse.urlparse(url).path.rstrip("/").split("/")[-1]
        page = BeautifulSoup(fetch(f"https://news.google.com/rss/articles/{aid}", timeout=15, tries=1), "html.parser")
        el = page.select_one("[data-n-a-sg][data-n-a-ts]")
        if not el:
            return None
        req = ["Fbv4je", '["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,null,null,null,0,1],'
               '"X","X",1,[1,1,1],1,1,null,0,0,null,0],"%s",%s,"%s"]' % (aid, el["data-n-a-ts"], el["data-n-a-sg"])]
        r = requests.post("https://news.google.com/_/DotsSplashUi/data/batchexecute",
                          headers={**UA, "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
                          data="f.req=" + urllib.parse.quote(json.dumps([[req]])), timeout=15)
        body = json.loads(r.text.split("\n\n")[1])[:-2]
        return json.loads(body[0][2])[1]
    except Exception:
        return None


def trim_summary(text: str, title: str, limit: int = 320) -> str:
    text = re.sub(r"\s+", " ", html.unescape(text or "")).strip()
    if trlower(text).startswith(trlower(title)[:40]):
        text = text[len(title):].lstrip(" .:-–—|")
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return cut[:end + 1] if end > 120 else cut.rsplit(" ", 1)[0] + "…"


def page_meta(url: str) -> dict:
    r = requests.get(url, headers={**UA, "User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/124 Mobile Safari/537.36"},
                     timeout=12, stream=True)
    r.raise_for_status()
    raw = r.raw.read(1_500_000, decode_content=True)
    soup = BeautifulSoup(raw, "html.parser")

    def meta(*names):
        for n in names:
            el = soup.find("meta", attrs={"property": n}) or soup.find("meta", attrs={"name": n})
            if el and el.get("content", "").strip():
                return el["content"].strip()
        return ""

    desc = meta("og:description", "twitter:description", "description")
    if len(desc) < 60:
        body = soup.find("article") or soup.find("main") or soup
        for p in body.find_all("p"):
            t = p.get_text(" ", strip=True)
            if len(t) > 80:
                desc = t
                break
    img = meta("og:image", "og:image:url", "twitter:image", "twitter:image:src")
    if img:
        img = urllib.parse.urljoin(r.url, img)
    return {"desc": desc, "img": img, "final": r.url}


def enrich_one(url: str) -> dict:
    real = gn_decode(url) if "news.google.com" in url else url
    if not real:
        return {"fail": "decode"}
    m = page_meta(real)
    return {"desc": m["desc"], "img": m["img"], "final": m["final"]}


def enrich(clusters: list[dict]) -> None:
    need = [c for c in clusters if len(c["summary"]) < 60 or not c["img"]]
    todo: list[str] = []
    for c in need:
        for u in c["_urls"][:2]:
            if u not in ENRICH and u not in todo and len(todo) < ENRICH_BUDGET:
                todo.append(u)
    def work(u):
        try:
            return u, enrich_one(u)
        except Exception as e:
            return u, {"fail": type(e).__name__}
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        for u, res in ex.map(work, todo):
            res["ts"] = iso(NOW)
            ENRICH[u] = res
            ENRICH_STATS["fail" if "fail" in res else "ok"] += 1
    for c in need:
        for u in c["_urls"][:2]:
            r = ENRICH.get(u)
            if not r or "fail" in r:
                continue
            if len(c["summary"]) < 60 and r.get("desc"):
                summ = trim_summary(r["desc"], c["title"])
                if len(summ) >= 40:
                    c["summary"] = summ
            if not c["img"] and r.get("img") and not r["img"].endswith((".svg", ".gif")):
                c["img"], c["media"], c["media_url"] = r["img"], c["media"] or "foto", r.get("final") or u
            if c["summary"] and c["img"]:
                break
    ENRICH_STATS["with_summary"] = sum(1 for c in clusters if len(c["summary"]) >= 40)
    ENRICH_STATS["with_img"] = sum(1 for c in clusters if c["img"])
    ENRICH_STATS["total"] = len(clusters)


def load_enrich(path: Path) -> None:
    try:
        cutoff = NOW - dt.timedelta(days=STORE_DAYS)
        ENRICH.update({k: v for k, v in json.loads(path.read_text(encoding="utf-8")).items()
                       if parse_date(v.get("ts")) and parse_date(v["ts"]) >= cutoff})
    except (FileNotFoundError, json.JSONDecodeError):
        pass


# ---------------------------------------------------------------- çıktılar
def build_news(clusters: list[dict]) -> dict:
    recent = [c for c in clusters if hours_ago(c["last"]) <= FEED_HOURS]
    recent.sort(key=rank, reverse=True)
    brief = ([c for c in recent if c["n"] >= 2] + [c for c in recent if c["n"] < 2])[:3]
    return {"updated": iso(NOW), "brief": [{"id": c["id"], "title": c["title"]} for c in brief],
            "stories": [public(c) for c in recent[:160]]}


def build_fronts(clusters: list[dict]) -> dict:
    today = NOW.astimezone(TRT).date()
    days = [today - dt.timedelta(days=i) for i in range(6, -1, -1)]
    out = []
    for f in CFG["fronts"]:
        cs = [c for c in clusters if f in c["fronts"] and c["cat"] in ("war", "geo", "def")]
        daily = Counter(parse_date(c["first"]).astimezone(TRT).date() for c in cs)
        cur = sum(daily[d] for d in days)
        prev = sum(daily[d - dt.timedelta(days=7)] for d in days)
        latest = sorted([c for c in cs if hours_ago(c["last"]) <= 7 * 24], key=lambda c: c["last"], reverse=True)[:15]
        out.append({"name": f, "count": cur, "prev": prev,
                    "daily": [[d.isoformat(), daily[d]] for d in days],
                    "events": [public(c) for c in latest]})
    out.sort(key=lambda x: x["count"], reverse=True)
    return {"updated": iso(NOW), "note": "Sayılar, bu cepheyle ilgili haberlerde geçen ayrı olay (haber kümesi) sayısıdır.", "fronts": out}


DID = re.compile(r"kabul ett|görüş(tü|me)|katıl|ziyaret|telefon|bir araya|ağırla|tören|zirve|açılış|imza|resmi karşılama|programı", re.I)
SAID = re.compile(r"(:|“|\"|dedi|açıkla|vurgula|konuş|mesaj|çağrı|uyar|tepki|sözler)", re.I)


def build_rte(clusters: list[dict]) -> dict:
    cs = [c for c in clusters if c["rte"] and hours_ago(c["last"]) <= 30]
    cs.sort(key=rank, reverse=True)
    did, said = [], defaultdict(list)
    for c in cs:
        tl, tk = trlower(c["title"] + " " + c["summary"]), tokens(c["title"] + " " + c["summary"])
        is_said = bool(SAID.search(c["title"]))
        if DID.search(c["title"]) and not (is_said and ":" in c["title"]):
            did.append(c)
        elif is_said:
            topic = max(RTE_KWS, key=lambda t: kw_hits(tl, tk, RTE_KWS[t]))
            if kw_hits(tl, tk, RTE_KWS[topic]) == 0:
                topic = "Diğer"
            said[topic].append(c)
    did.sort(key=lambda c: c["first"])
    return {"updated": iso(NOW),
            "brief": [c["title"] for c in cs[:3]],
            "did": [public(c) for c in did[:12]],
            "said": [{"topic": t, "items": [public(c) for c in v[:6]]} for t, v in sorted(said.items(), key=lambda kv: -len(kv[1]))]}


# ---------------------------------------------------------------- piyasa
YAHOO = {"usd": ("USD/TRY", "TRY=X", 2), "eur": ("EUR/TRY", "EURTRY=X", 2), "ons": ("ONS/USD", "GC=F", 0),
         "silver": ("Gümüş ONS/USD", "SI=F", 2), "bist": ("BIST 100", "XU100.IS", 0), "brent": ("Brent ($)", "BZ=F", 2)}


def yahoo(sym: str) -> list[list]:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}?range=1y&interval=1d"
    j = requests.get(url, headers=UA, timeout=25).json()["chart"]["result"][0]
    ts, closes = j["timestamp"], j["indicators"]["quote"][0]["close"]
    pts = {}
    for t, v in zip(ts, closes):
        if v is not None:
            pts[dt.datetime.fromtimestamp(t, TRT).date().isoformat()] = round(v, 4)
    live = j.get("meta", {}).get("regularMarketPrice")
    if live:
        pts[NOW.astimezone(TRT).date().isoformat()] = round(live, 4)
    return sorted(pts.items())


def build_markets() -> dict:
    series = {}
    for k, (label, sym, d) in YAHOO.items():
        try:
            series[k] = {"label": label, "decimals": d, "points": yahoo(sym)}
            META["sources"][f"Piyasa: {label}"] = len(series[k]["points"])
        except Exception as e:
            META["errors"][f"Piyasa: {label}"] = f"{type(e).__name__}: {str(e)[:160]}"
    if "ons" in series and "usd" in series:  # gram altın = ons × usd/try ÷ 31,1035
        usd = dict(series["usd"]["points"])
        pts = [[d, round(v * usd[d] / 31.1035, 2)] for d, v in series["ons"]["points"] if d in usd]
        series = {"usd": series["usd"], "eur": series.get("eur"), "gold": {"label": "Gram altın (₺)", "decimals": 0, "points": pts},
                  **{k: v for k, v in series.items() if k not in ("usd", "eur")}}
    if "usd" not in series:  # yedek: TCMB günlük kur
        try:
            root = ET.fromstring(fetch("https://www.tcmb.gov.tr/kurlar/today.xml"))
            for code, key, label in (("USD", "usd", "USD/TRY"), ("EUR", "eur", "EUR/TRY")):
                el = root.find(f".//Currency[@CurrencyCode='{code}']/ForexSelling")
                series[key] = {"label": label, "decimals": 2, "points": [[NOW.astimezone(TRT).date().isoformat(), float(el.text)]]}
        except Exception as e:
            META["errors"]["Piyasa: TCMB"] = f"{type(e).__name__}: {str(e)[:160]}"
    return {"updated": iso(NOW), "series": {k: v for k, v in series.items() if v}}


# ---------------------------------------------------------------- ana akış
def write(out: Path, name: str, obj) -> None:
    (out / name).write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="site/data")
    ap.add_argument("--store", default=".cache/store.json")
    ap.add_argument("--no-markets", action="store_true")
    a = ap.parse_args()
    out, store_path = Path(a.out), Path(a.store)
    out.mkdir(parents=True, exist_ok=True)
    store_path.parent.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    new = collect_items()
    items = merge_store(load_store(store_path), new)
    store_path.write_text(json.dumps([{k: v for k, v in i.items() if not k.startswith("_")} for i in items],
                                     ensure_ascii=False), encoding="utf-8")
    clusters = cluster([annotate(dict(i)) for i in items])

    # Uygulamada görünecek kümelere özet ve görsel ekle (önce en önemliler)
    enrich_path = store_path.with_name("enrich.json")
    load_enrich(enrich_path)
    shown = sorted([c for c in clusters if hours_ago(c["last"]) <= FEED_HOURS], key=rank, reverse=True)[:160]
    shown_ids = {id(c) for c in shown}
    extra = [c for c in clusters if id(c) not in shown_ids and hours_ago(c["last"]) <= 7 * 24
             and (c["fronts"] or c["rte"])]
    extra.sort(key=lambda c: c["last"], reverse=True)
    enrich(shown + extra[:120])
    enrich_path.write_text(json.dumps(ENRICH, ensure_ascii=False), encoding="utf-8")
    META["enrich"] = dict(ENRICH_STATS)

    news = build_news(clusters)
    write(out, "news.json", news)
    write(out, "fronts.json", build_fronts(clusters))
    write(out, "rte.json", build_rte(clusters))
    if not a.no_markets:
        write(out, "markets.json", build_markets())
    manual = HERE.parent / "data" / "manual.json"
    if manual.exists():
        (out / "manual.json").write_text(manual.read_text(encoding="utf-8"), encoding="utf-8")
    META.update({"updated": iso(NOW), "new_items": len(new), "stored_items": len(items),
                 "clusters": len(clusters), "seconds": round(time.time() - t0, 1)})
    write(out, "meta.json", META)
    print(json.dumps(META, ensure_ascii=False, indent=1))
    # Kalite özeti: iş akışı kaydında sınıflamayı kontrol etmek için
    print("KATEGORİLER", dict(Counter(s["cat"] for s in news["stories"])))
    for s in news["stories"][:60]:
        print(f'  [{s["cat"]}] n={s["n"]} {",".join(s["fronts"][:1]):<14} {s["title"][:100]}')
    return 0


if __name__ == "__main__":
    sys.exit(main())

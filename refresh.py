"""Download the SimplifyJobs internship list and rebuild the page.

    python refresh.py            download fresh data, write index.html + widget.json + listings.csv
    python refresh.py --offline  reuse the last downloaded listings.json

Settings come from config.json (copied from config.example.json on first run).
Graduation-year tags come from gradyear_cache.json, which scrape_gradyear.py
builds by reading each posting's own page; run it after this script, then run
this script again with --offline to merge the results.
"""
import collections
import csv
import datetime
import json
import os
import re
import shutil
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEFAULTS = {
    "season": "Summer 2027",
    "source": "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/.github/scripts/listings.json",
    "source_repo": "SimplifyJobs/Summer2027-Internships",
    "categories": ["Software", "AI/ML/Data", "Quant"],
    "degrees": ["Bachelor's"],
    "class_years": [2029, 2030],
    "needs_sponsorship": True,
    "expose": "tailscale",
    "port": 8765,
    "site_url": "",
    "you": "",
}


def load_config():
    path = os.path.join(HERE, "config.json")
    example = os.path.join(HERE, "config.example.json")
    if not os.path.exists(path) and os.path.exists(example):
        shutil.copy(example, path)
        print("Created config.json from config.example.json - edit it to match you.")
    cfg = dict(DEFAULTS)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            user = json.load(f)
        cfg.update({k: v for k, v in user.items() if not k.startswith("_")})
    cfg["class_years"] = [int(y) for y in cfg["class_years"]]
    return cfg


CONFIG = load_config()

# Simplify uses two spellings for some categories; both map to the short name.
CATEGORY_ALIASES = {
    "Software": "Software", "Software Engineering": "Software",
    "AI/ML/Data": "AI/ML/Data", "Data Science, AI & Machine Learning": "AI/ML/Data",
    "Quant": "Quant", "Quantitative Finance": "Quant",
    "Hardware": "Hardware", "Hardware Engineering": "Hardware",
    "Product": "Product", "Product Management": "Product",
}
WANTED = set(CONFIG["categories"])
DEGREES = set(CONFIG["degrees"])

# ---------------------------------------------------------------------------
# Location: is this posting in the US?
# ---------------------------------------------------------------------------

NONUS = re.compile(r"\b(canada|uk|india|china|japan|germany|australia|singapore|ireland|netherlands|france|israel|mexico|brazil|switzerland|spain|italy|poland|sweden|denmark|hong kong|taiwan|korea|vietnam|philippines|malaysia|uae|dubai|belgium|austria|portugal|czech|hungary|romania|norway|finland|new zealand|argentina|colombia|chile|south africa|egypt|turkey|england|scotland|wales)\b")
US_STATES = set("AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC".split())
US_WORDS = {"nyc", "sf", "la", "south sf", "remote", "united states", "remote in usa", "remote in us", "usa", "us",
            "minnesota", "california", "texas", "new york", "washington", "virginia", "new jersey", "massachusetts", "illinois"}

with open(os.path.join(HERE, "defense.txt"), encoding="utf-8") as f:
    DEFENSE = re.compile(r"\b(" + "|".join(re.escape(w.strip()) for w in f if w.strip() and not w.startswith("#")) + r")\b")


def is_us(loc):
    ll = loc.lower().strip()
    if ll in US_WORDS or "remote in usa" in ll or "remote in us" in ll:
        return True
    if NONUS.search(ll):
        return False
    m = re.search(r", ([A-Z]{2})$", loc.strip())
    return bool(m and m.group(1) in US_STATES)


# ---------------------------------------------------------------------------
# Load + filter
# ---------------------------------------------------------------------------

def load(offline):
    path = os.path.join(HERE, "listings.json")
    if not offline:
        print("Downloading", CONFIG["source"])
        req = urllib.request.Request(CONFIG["source"], headers={"User-Agent": "intern-radar"})
        with urllib.request.urlopen(req, timeout=60) as r, open(path, "wb") as out:
            out.write(r.read())
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def build(raw):
    now = datetime.datetime.now()
    season = CONFIG["season"]
    rows = []
    for x in raw:
        cat = CATEGORY_ALIASES.get(x.get("category"))
        if not (x.get("active") and x.get("is_visible") and season in x.get("terms", []) and cat in WANTED):
            continue
        degrees = x.get("degrees", [])
        if degrees and not DEGREES & set(degrees):
            continue
        title = x["title"].replace("–", "-").replace("�C", "-")
        tl, cl = title.lower(), x["company_name"].lower()
        flags = []
        if x.get("sponsorship") == "U.S. Citizenship is Required" or re.search(r"citizen|clearance|top secret|ts/sci", tl):
            flags.append("citizenship-required")
        elif CONFIG["needs_sponsorship"] and DEFENSE.search(cl):
            flags.append("likely-us-person")
        if x.get("sponsorship") == "Does Not Offer Sponsorship" and CONFIG["needs_sponsorship"]:
            flags.append("no-sponsorship")
        if re.search(r"co-?op", tl):
            flags.append("co-op")
        if "undergrad" in tl:
            flags.append("undergrad-specific")
        if re.search(r"early internship|\bearly\b", tl):
            flags.append("early-program")
        if tl.split() and tl.split()[0] == "senior":
            flags.append("senior-title")
        locs = x.get("locations", [])
        region = "US" if any(is_us(l) for l in locs) else ("Unknown" if not locs else "Non-US")
        posted = datetime.datetime.fromtimestamp(x["date_posted"])
        rows.append(dict(id=x["id"], company=x["company_name"], title=title, category=cat,
                         locations=list(locs), region=region, url=x["url"], company_url=x.get("company_url", ""),
                         posted=posted.strftime("%Y-%m-%d"), age_days=(now - posted).days, flags=flags))
    # Merge duplicate company + title rows (same job posted per city).
    merged = {}
    for o in rows:
        k = (o["company"].lower(), o["title"].lower())
        if k in merged:
            m = merged[k]
            for l in o["locations"]:
                if l not in m["locations"]:
                    m["locations"].append(l)
            if o["url"] != m["url"]:
                m.setdefault("alt_urls", []).append(o["url"])
            if o["region"] == "US":
                m["region"] = "US"
            if o["posted"] > m["posted"]:
                m["posted"], m["age_days"] = o["posted"], o["age_days"]
        else:
            merged[k] = o
    res = list(merged.values())
    res.sort(key=lambda o: (o["region"] != "US", o["category"], o["company"].lower(), o["title"]))
    return res


def attach_gradyear(res):
    """Merge gradyear_cache.json (from scrape_gradyear.py) into the rows."""
    path = os.path.join(HERE, "gradyear_cache.json")
    cache = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            cache = json.load(f)
    for o in res:
        c = cache.get(o["id"])
        if not c:
            o["grad"] = "unscraped"; o["grad_min"] = o["grad_max"] = None; o["grad_note"] = ""
        elif not c.get("fetched"):
            o["grad"] = "unfetched"; o["grad_min"] = o["grad_max"] = None; o["grad_note"] = c.get("source", "")
        elif c.get("years"):
            o["grad"] = "stated"; o["grad_min"] = c["min"]; o["grad_max"] = c["max"]; o["grad_note"] = c.get("note", "")
        else:
            o["grad"] = "unspecified"; o["grad_min"] = o["grad_max"] = None; o["grad_note"] = ""
    return res


# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------

BLOCKING = {"likely-us-person", "citizenship-required", "no-sponsorship"}
WIDGET_NEWEST = 20


def open_to_you(res):
    return [o for o in res if o["region"] == "US" and not set(o["flags"]) & BLOCKING]


def grad_ok(o, year):
    if year == "any" or o["grad"] != "stated":
        return True
    return o["grad_min"] <= year <= o["grad_max"]


def grad_label(o):
    if o["grad"] != "stated":
        return ""
    if o["grad_max"] >= 2032:
        return f"{o['grad_min']}+"
    return str(o["grad_min"]) if o["grad_min"] == o["grad_max"] else f"{o['grad_min']}-{o['grad_max']}"


def site_url():
    if CONFIG["site_url"]:
        return CONFIG["site_url"].rstrip("/")
    if CONFIG["expose"] == "tailscale":
        try:
            import serve
            ip = serve.tailscale_ip()
            if ip:
                return f"http://{ip}:{CONFIG['port']}"
        except Exception:
            pass
    return f"http://localhost:{CONFIG['port']}"


def write_widget(res):
    """One small payload the phone widget reads: counts per class-year view plus the newest postings."""
    us = open_to_you(res)
    cats = CONFIG["categories"]
    views = {}
    for year in list(CONFIG["class_years"]) + ["any"]:
        rows = [o for o in us if grad_ok(o, year)]
        views[str(year)] = {
            "open": len(rows),
            "new7": sum(1 for o in rows if o["age_days"] <= 7),
            "new14": sum(1 for o in rows if o["age_days"] <= 14),
            "stated_ok": sum(1 for o in rows if o["grad"] == "stated"),
            "excluded": len(us) - len(rows),
            "by_category": {
                c: {"open": sum(1 for o in rows if o["category"] == c),
                    "new7": sum(1 for o in rows if o["category"] == c and o["age_days"] <= 7)}
                for c in cats
            },
        }
    by_age = sorted(us, key=lambda o: (o["age_days"], o["company"].lower()))
    picked, seen = [], set()
    for o in by_age[:WIDGET_NEWEST] + [o for c in cats for o in [x for x in by_age if x["category"] == c][:8]]:
        if o["id"] not in seen:
            seen.add(o["id"]); picked.append(o)
    newest = sorted(picked, key=lambda o: (o["age_days"], o["company"].lower()))
    payload = {
        "generated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "snapshot": datetime.date.today().isoformat(),
        "season": CONFIG["season"],
        "site_url": site_url(),
        "class_years": [str(y) for y in CONFIG["class_years"]],
        "default_view": str(CONFIG["class_years"][0]) if CONFIG["class_years"] else "any",
        "categories": cats,
        "total_rows": len(res),
        "us_open": len(us),
        "views": views,
        "newest": [
            {
                "company": o["company"],
                "title": o["title"],
                "category": o["category"],
                "posted": o["posted"],
                "location": o["locations"][0] if o["locations"] else "",
                "url": o["url"],
                "grad": grad_label(o),
                "ok": {str(y): grad_ok(o, y) for y in CONFIG["class_years"]},
            }
            for o in newest
        ],
    }
    with open(os.path.join(HERE, "widget.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    return payload


def write(res):
    with open(os.path.join(HERE, "current_rows.json"), "w", encoding="utf-8") as f:
        json.dump([{"id": r["id"], "url": r["url"], "company": r["company"], "title": r["title"]} for r in res], f)
    attach_gradyear(res)

    page_cfg = {
        "season": CONFIG["season"],
        "source_repo": CONFIG["source_repo"],
        "categories": CONFIG["categories"],
        "degrees": CONFIG["degrees"],
        "class_years": [str(y) for y in CONFIG["class_years"]],
        "needs_sponsorship": CONFIG["needs_sponsorship"],
        "you": CONFIG["you"],
        "snapshot": datetime.date.today().isoformat(),
    }
    safe = lambda s: s.replace("</", "<\\/")
    with open(os.path.join(HERE, "template.html"), encoding="utf-8") as f:
        html = f.read()
    html = (html.replace("__TITLE__", f"{CONFIG['season']} Intern Radar")
                .replace("__CONFIG__", safe(json.dumps(page_cfg, ensure_ascii=False)))
                .replace("__DATA__", safe(json.dumps(res, ensure_ascii=False, separators=(",", ":")))))
    with open(os.path.join(HERE, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)

    write_widget(res)

    with open(os.path.join(HERE, "listings.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["Company", "Title", "Category", "Region", "Locations", "Posted", "Flags", "Grad years stated", "Grad note", "Apply URL"])
        for x in res:
            w.writerow([x["company"], x["title"], x["category"], x["region"], "; ".join(x["locations"]),
                        x["posted"], "; ".join(x["flags"]), grad_label(x), x["grad_note"], x["url"]])


if __name__ == "__main__":
    res = build(load("--offline" in sys.argv))
    write(res)
    us = open_to_you(res)
    print(f"Wrote index.html, widget.json and listings.csv: {len(res)} rows, {len(us)} US roles open to you.")
    print("Newest:", collections.Counter(o["posted"] for o in us).most_common(3))
    unscraped = sum(1 for o in res if o["grad"] == "unscraped")
    if unscraped:
        print(f"{unscraped} rows have no class-year scan yet. Run:  python scrape_gradyear.py   then  python refresh.py --offline")
    print(f"Serve it with:  python serve.py   (http://localhost:{CONFIG['port']})")

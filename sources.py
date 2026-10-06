"""Where the postings come from.

Every source returns the same shape, so refresh.py never cares which list or
job board a row came from:

    {
      "id":           stable string (Simplify's id, or a hash of the URL),
      "source":       "simplify" | "vanshb03/..." | "greenhouse:figma" | ...,
      "company":      "Figma",
      "title":        "Data Engineer Intern (2027)",
      "url":          apply link,
      "company_url":  "" or a link,
      "locations":    ["San Francisco, CA", "New York, NY"],
      "posted":       unix seconds,
      "terms":        ["Summer 2027"]  (what the posting says it is for),
      "season_assumed": True when the posting names no season and we assume
                        the configured one (employer boards mostly do this),
      "category":     "Software" | "AI/ML/Data" | "Quant" | "Hardware" | "Product" | None,
      "degrees":      ["Bachelor's", ...] or [] when the source does not say,
      "sponsorship":  Simplify's vocabulary or "Other",
      "active":       bool,
    }

Sources:
  simplify   SimplifyJobs' listings.json - the richest (category, degrees, sponsorship).
  listing    Any other repo that publishes the same listings.json (vanshb03 does, with
             a slightly older schema).
  readme     Any PittCSC-style README table (speedyapply, jobright, ...). Parsed from
             markdown, so company / role / location / link / age only.
  greenhouse / lever / ashby
             Employers' own job boards, via each ATS's public JSON API. Postings land
             here hours to days before any list picks them up.

Nothing here needs credentials, and every request is a plain GET of data the
employer or list maintainer publishes for exactly this purpose.
"""
import datetime
import hashlib
import html
import json
import re
import urllib.request

UA = {"User-Agent": "intern-radar (https://github.com/zxpeng2007/intern-radar)"}
INTERN = re.compile(r"\bintern(ship)?s?\b|\bco-?op\b|\bsummer analyst\b", re.I)
NOT_INTERN = re.compile(r"\b(new grad|graduate program|full[- ]time|senior|staff|principal|director|manager|lead\b|head of|vp\b|apprentice)", re.I)
SEASON = re.compile(r"\b(summer|fall|autumn|winter|spring)\b[\s,'/-]*((?:20)?\d\d)?", re.I)
YEAR = re.compile(r"\b(20[2-3]\d)\b")

CATEGORY_RULES = [
    ("Quant", re.compile(r"\bquant|\btrad(er|ing)\b|\bquantitative strateg|\bstrats?\b|\bportfolio (manag|research|implement)|\b(market|quantitative|model) risk\b|\balgorithm(ic)? (trad|develop)|\bsummer analyst\b.*\b(markets|trading|securities)", re.I)),
    ("Hardware", re.compile(r"\bhardware|\bfpga|\bembedded|\belectrical|\basic\b|\bfirmware|\bsilicon|\bchip\b|\bcircuit|\bverification\b|\brf\b|\bpcb\b|\bmechanical\b|\bmechatronic|\brobotics hardware", re.I)),
    ("Product", re.compile(r"\bproduct manag|\bproduct intern|\bapm\b|\bprogram manag|\btechnical program|\bproduct design", re.I)),
    ("AI/ML/Data", re.compile(r"\bdata\b|\bmachine learning|\bml\b|\bai\b|\bartificial intelligence|\banalytics?\b|\bscientist|\bresearch\b|\bdeep learning|\bcomputer vision|\bnlp\b|\bperception|\bautonomy|\bapplied scien|\bbusiness intelligence|\bstatistic", re.I)),
    ("Software", re.compile(r"\bsoftware|\bengineer|\bdeveloper|\bswe\b|\bsde\b|\bbackend|\bfront[- ]?end|\bfull[- ]?stack|\bplatform\b|\binfrastructure|\bdevops|\bsre\b|\bsecurity|\bmobile\b|\bios\b|\bandroid|\bweb\b|\bcloud\b|\bsystems?\b|\bprogrammer|\bcomputer scien|\bcomputing\b|\bsolutions? engineer|\btechnolog(y|ist) intern", re.I)),
]
# Not what we are looking for even when the words above match.
NOT_TECH = re.compile(r"\b(it support|help ?desk|accounting|audit|payroll|recruit|people (ops|analytics|team)|hr\b|legal|marketing|sales|communications|facilities|workplace|learning & development|controller|operations intern|inventory|compliance|user research|ux research|design research|customer (success|support)|manufacturing|industrial engineer|supply chain|quality engineer|process engineer|civil engineer|chemical engineer|materials engineer|cnc\b|machinist)", re.I)
# At these firms, research / analyst / strategy roles are quant roles.
QUANT_FIRMS = re.compile(r"\b(jump|imc|optiver|dv trading|virtu|akuna|point72|schonfeld|pdt|old mission|transmarket|flow traders|aqr|citadel|jane street|hudson river|hrt|two sigma|d\.? ?e\.? ?shaw|susquehanna|sig\b|drw|five rings|tower research|belvedere|chicago trading|ctc|wolverine|headlands|radix|xtx|squarepoint|qube|g-research|millennium|balyasny|voleon|arrowstreet|walleye|voloridge|quantbot|xantium|aquatic|maven|trillium|geneva trading|valkyrie|tradeweb|cubist|worldquant|engineers gate|marshall wace|man group|ahl|bridgewater|renaissance|verition|exoduspoint|anthelion|axq)\b", re.I)

SIMPLIFY_CATEGORY = {
    "Software": "Software", "Software Engineering": "Software",
    "AI/ML/Data": "AI/ML/Data", "Data Science, AI & Machine Learning": "AI/ML/Data",
    "Quant": "Quant", "Quantitative Finance": "Quant",
    "Hardware": "Hardware", "Hardware Engineering": "Hardware",
    "Product": "Product", "Product Management": "Product",
}


def get(url, timeout=60):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def get_json(url, timeout=60):
    return json.loads(get(url, timeout))


def infer_category(title, company=""):
    if NOT_TECH.search(title) and not re.search(r"\bsoftware|\bengineer|\bdata (scien|engineer)|\bmachine learning", title, re.I):
        return None
    cat = None
    for name, rx in CATEGORY_RULES:
        if rx.search(title):
            cat = name
            break
    if company and QUANT_FIRMS.search(company) and cat in (None, "AI/ML/Data") and re.search(r"\bresearch|\banalyst|\bstrateg|\btrad", title, re.I):
        cat = "Quant"
    return cat


def norm_title(t):
    return re.sub(r"\s+", " ", str(t or "").replace("–", "-").replace("�C", "-")).strip()


def stable_id(url, fallback=""):
    return hashlib.sha1((url or fallback).encode("utf-8")).hexdigest()[:32]


def terms_from_title(title, season_cfg):
    """['Summer 2027'] if the title names a season; [] if it does not."""
    cfg_year = (YEAR.search(season_cfg) or [None, None])[1]
    found = []
    for m in SEASON.finditer(title):
        word = m.group(1).capitalize().replace("Autumn", "Fall")
        year = m.group(2)
        if year and len(year) == 2:
            year = "20" + year
        if not year:
            years = YEAR.findall(title)
            year = years[0] if years else cfg_year
        if year:
            found.append(f"{word} {year}")
    if not found:
        years = YEAR.findall(title)
        if years and cfg_year and years[0] != cfg_year and "Summer" in season_cfg:
            # "2026 Internship Program" with no season word: treat the year as decisive.
            found.append(f"Summer {years[0]}")
    return list(dict.fromkeys(found))


# ---------------------------------------------------------------------------
# Simplify-style listings.json (SimplifyJobs, vanshb03, cvrve, ...)
# ---------------------------------------------------------------------------

def listing_json(url, name, season_cfg):
    raw = get_json(url)
    out = []
    for x in raw:
        if not x.get("is_visible", True):
            continue
        title = norm_title(x.get("title"))
        terms = x.get("terms")
        if not terms:
            # vanshb03 schema: "season": "Summer" with no year
            s = str(x.get("season") or "").strip()
            terms = terms_from_title(title, season_cfg)
            if not terms and s and s.lower() != "null":
                yr = (YEAR.search(season_cfg) or [None, None])[1]
                terms = [f"{part.strip().capitalize()} {yr}" for part in s.split("/") if part.strip()]
        cat = SIMPLIFY_CATEGORY.get(x.get("category")) or infer_category(title, x.get("company_name", ""))
        out.append(dict(
            id=x.get("id") or stable_id(x.get("url", ""), title),
            source=name,
            company=x.get("company_name", "").strip(),
            title=title,
            url=x.get("url", ""),
            company_url=x.get("company_url", "") or "",
            locations=[l for l in x.get("locations", []) if l],
            posted=int(x.get("date_posted") or x.get("date_updated") or 0),
            terms=list(terms),
            season_assumed=False,
            category=cat,
            degrees=list(x.get("degrees", []) or []),
            sponsorship=x.get("sponsorship", "Other") or "Other",
            active=bool(x.get("active", True)),
        ))
    return out


# ---------------------------------------------------------------------------
# README tables (speedyapply, jobright, any PittCSC-style list)
# ---------------------------------------------------------------------------

AGE = re.compile(r"^\s*(\d+)\s*(h|d|w|mo|m|y)\s*$", re.I)
HREF = re.compile(r'href="([^"]+)"', re.I)
TAG = re.compile(r"<[^>]+>")


def _cell_text(c):
    return html.unescape(TAG.sub("", c)).replace(" ", " ").strip()


def _age_to_epoch(s, now):
    m = AGE.match(s or "")
    if not m:
        return int(now.timestamp())
    n, unit = int(m.group(1)), m.group(2).lower()
    days = {"h": n / 24, "d": n, "w": 7 * n, "mo": 30 * n, "m": 30 * n, "y": 365 * n}[unit]
    return int((now - datetime.timedelta(days=days)).timestamp())


def readme_table(url, name, season_cfg, assume_season=True):
    """Parse every markdown table whose header has company / role-ish / link-ish columns."""
    text = get(url)
    now = datetime.datetime.now()
    out = []
    section = ""
    header = None
    last_company = ""
    for line in text.splitlines():
        if line.startswith("#"):
            section = line.strip("# ").lower()
            header = None
            continue
        if not line.startswith("|"):
            header = None
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if header is None:
            low = [c.lower() for c in cells]
            if any("company" in c for c in low) and any(("position" in c or "role" in c or "title" in c) for c in low):
                header = low
            continue
        if set("".join(cells)) <= set("-: "):
            continue  # the |---|---| separator line
        if len(cells) < 3:
            continue
        col = lambda *names: next((i for i, h in enumerate(header) if any(n in h for n in names)), None)
        ci, ri, li, ai = col("company"), col("position", "role", "title"), col("location"), col("age", "date", "posted")
        link_i = col("posting", "application", "apply", "link")
        if ci is None or ri is None:
            continue
        raw_company = cells[ci] if ci < len(cells) else ""
        company = _cell_text(raw_company)
        if company in ("↳", "") or raw_company.strip() in ("↳", ""):
            company = last_company
        else:
            last_company = company
        role_raw = cells[ri] if ri < len(cells) else ""
        title = norm_title(_cell_text(role_raw))
        if not title or not company:
            continue
        closed = "🔒" in line or "closed" in role_raw.lower()
        link_cell = cells[link_i] if link_i is not None and link_i < len(cells) else role_raw
        m = HREF.search(link_cell) or HREF.search(role_raw) or re.search(r"\((https?://[^)\s]+)\)", link_cell)
        link = m.group(1) if m else ""
        if not link or "simplify.jobs" in link and "/p/" not in link:
            pass
        loc_text = _cell_text(cells[li]) if li is not None and li < len(cells) else ""
        locations = [l.strip() for l in re.split(r"<br\s*/?>|\n|;|\|| • |, (?=[A-Z][a-z]+, [A-Z]{2}\b)", cells[li] if li is not None and li < len(cells) else "") if _cell_text(l).strip()]
        locations = [_cell_text(l) for l in locations] or ([loc_text] if loc_text else [])
        age = _cell_text(cells[ai]) if ai is not None and ai < len(cells) else ""
        terms = terms_from_title(title, season_cfg)
        assumed = False
        if not terms and assume_season:
            terms, assumed = [season_cfg], True
        cat = infer_category(title, company)
        if "quant" in section and cat in (None, "Software", "AI/ML/Data") and re.search(r"trad|quant|research", title, re.I):
            cat = "Quant"
        out.append(dict(
            id=stable_id(link, company + title),
            source=name,
            company=company,
            title=title,
            url=link,
            company_url=(HREF.search(raw_company).group(1) if HREF.search(raw_company) else ""),
            locations=locations,
            posted=_age_to_epoch(age, now),
            terms=terms,
            season_assumed=assumed,
            category=cat,
            degrees=[],
            sponsorship="Other",
            active=not closed and bool(link),
        ))
    return out


# ---------------------------------------------------------------------------
# Employer job boards
# ---------------------------------------------------------------------------

def _is_intern(title):
    return bool(INTERN.search(title)) and not NOT_INTERN.search(title)


def _board_terms(title, season_cfg):
    terms = terms_from_title(title, season_cfg)
    if terms:
        return terms, False
    return [season_cfg], True


def _iso_epoch(s):
    try:
        return int(datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp())
    except Exception:
        return int(datetime.datetime.now().timestamp())


def greenhouse(board, season_cfg, company=None):
    j = get_json(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs")
    out = []
    for x in j.get("jobs", []):
        title = norm_title(x.get("title"))
        if not _is_intern(title):
            continue
        terms, assumed = _board_terms(title, season_cfg)
        loc = (x.get("location") or {}).get("name", "") or ""
        locations = [l.strip() for l in re.split(r"\s*[•;|]\s*|\s+or\s+", loc) if l.strip()]
        out.append(dict(
            id=stable_id(x.get("absolute_url", ""), f"gh{x.get('id')}"),
            source=f"greenhouse:{board}",
            company=company or x.get("company_name") or board.title(),
            title=title, url=x.get("absolute_url", ""), company_url="",
            locations=locations, posted=_iso_epoch(x.get("first_published") or x.get("updated_at")),
            terms=terms, season_assumed=assumed, category=infer_category(title, company or board),
            degrees=[], sponsorship="Other", active=True,
        ))
    return out


def lever(slug, season_cfg, company=None):
    j = get_json(f"https://api.lever.co/v0/postings/{slug}?mode=json")
    out = []
    for x in j if isinstance(j, list) else []:
        title = norm_title(x.get("text"))
        cats = x.get("categories") or {}
        commitment = str(cats.get("commitment") or "")
        if not (_is_intern(title) or ("intern" in commitment.lower() and not NOT_INTERN.search(title))):
            continue
        terms, assumed = _board_terms(title, season_cfg)
        locations = list(cats.get("allLocations") or ([cats["location"]] if cats.get("location") else []))
        out.append(dict(
            id=stable_id(x.get("hostedUrl", ""), f"lv{x.get('id')}"),
            source=f"lever:{slug}",
            company=company or slug.title(),
            title=title, url=x.get("hostedUrl", ""), company_url="",
            locations=locations, posted=int((x.get("createdAt") or 0) / 1000) or int(datetime.datetime.now().timestamp()),
            terms=terms, season_assumed=assumed, category=infer_category(title, company or slug),
            degrees=[], sponsorship="Other", active=True,
        ))
    return out


def ashby(org, season_cfg, company=None):
    j = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{org}")
    out = []
    for x in j.get("jobs", []):
        if x.get("isListed") is False:
            continue
        title = norm_title(x.get("title"))
        emp = str(x.get("employmentType") or "")
        if not (_is_intern(title) or (emp.lower() == "intern" and not NOT_INTERN.search(title))):
            continue
        terms, assumed = _board_terms(title, season_cfg)
        locations = [x.get("location")] if x.get("location") else []
        for s in x.get("secondaryLocations") or []:
            if s.get("location"):
                locations.append(s["location"])
        addr = ((x.get("address") or {}).get("postalAddress") or {})
        if addr.get("addressCountry") == "United States" and locations and "United States" not in locations[0]:
            locations[0] = locations[0] + ", United States"
        if x.get("isRemote") and not any("remote" in l.lower() for l in locations):
            locations.append("Remote")
        out.append(dict(
            id=stable_id(x.get("jobUrl", ""), f"ab{x.get('id')}"),
            source=f"ashby:{org}",
            company=company or org.title(),
            title=title, url=x.get("jobUrl", ""), company_url="",
            locations=locations, posted=_iso_epoch(x.get("publishedAt")),
            terms=terms, season_assumed=assumed, category=infer_category(title, company or org),
            degrees=[], sponsorship="Other", active=True,
        ))
    return out


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def fetch_all(cfg, log=print):
    """Fetch every configured source. A source that fails is logged and skipped,
    never fatal. Returns (records, report) where report maps source -> count or error."""
    season = cfg["season"]
    src = cfg.get("sources") or {}
    jobs = []
    if src.get("simplify", True):
        jobs.append(("simplify", lambda: listing_json(cfg["source"], "simplify", season)))
    for repo in src.get("listing_json", []) or []:
        repo = repo.strip()
        url = repo if repo.startswith("http") else f"https://raw.githubusercontent.com/{repo}/dev/.github/scripts/listings.json"
        jobs.append((repo, lambda url=url, repo=repo: listing_json(url, repo, season)))
    for repo in src.get("readme", []) or []:
        repo = repo.strip()
        if repo.startswith("http"):
            url = repo
        else:
            branch = "main"
            url = f"https://raw.githubusercontent.com/{repo}/{branch}/README.md"
        jobs.append((repo, lambda url=url, repo=repo: readme_table(url, repo, season)))
    boards = src.get("boards") or {}
    for slug in boards.get("greenhouse", []) or []:
        slug, _, label = slug.partition("=")
        jobs.append((f"greenhouse:{slug}", lambda s=slug, l=label: greenhouse(s, season, l or None)))
    for slug in boards.get("lever", []) or []:
        slug, _, label = slug.partition("=")
        jobs.append((f"lever:{slug}", lambda s=slug, l=label: lever(s, season, l or None)))
    for slug in boards.get("ashby", []) or []:
        slug, _, label = slug.partition("=")
        jobs.append((f"ashby:{slug}", lambda s=slug, l=label: ashby(s, season, l or None)))

    records, report = [], {}
    from concurrent.futures import ThreadPoolExecutor

    def run(item):
        name, fn = item
        try:
            r = fn()
            return name, r, None
        except Exception as e:
            return name, [], f"{type(e).__name__}: {str(e)[:80]}"

    with ThreadPoolExecutor(max_workers=12) as ex:
        for name, r, err in ex.map(run, jobs):
            report[name] = err or len(r)
            if err:
                log(f"  source {name}: FAILED ({err})")
            records.extend(r)
    return records, report


def merge(records):
    """Collapse the same posting seen in several sources.

    Match on apply URL first (ignoring query strings), then on company + title.
    Simplify rows win on metadata (category, degrees, sponsorship) because they
    are hand-curated; the earliest posting date wins; every source is recorded.
    """
    def url_key(u):
        u = (u or "").split("?", 1)[0].split("#", 1)[0].rstrip("/").lower()
        return re.sub(r"^https?://(www\.)?", "", u)

    def ct_key(r):
        return (r["company"].lower().strip(), re.sub(r"[^a-z0-9]+", " ", r["title"].lower()).strip())

    by_url, by_ct, order = {}, {}, []
    for r in records:
        r = dict(r)
        r["sources"] = [r["source"]]
        k1 = url_key(r["url"]) if r["url"] else None
        k2 = ct_key(r)
        tgt = (k1 and by_url.get(k1)) or by_ct.get(k2)
        if tgt is None:
            if k1:
                by_url[k1] = r
            by_ct.setdefault(k2, r)
            order.append(r)
            continue
        # merge r into tgt
        if r["source"] not in tgt["sources"]:
            tgt["sources"].append(r["source"])
        prefer_new = r["source"] == "simplify" and tgt["source"] != "simplify"
        if prefer_new:
            for k in ("category", "degrees", "sponsorship", "company_url", "id", "source"):
                if r.get(k):
                    tgt[k] = r[k]
        else:
            if not tgt.get("category") and r.get("category"):
                tgt["category"] = r["category"]
            if not tgt.get("degrees") and r.get("degrees"):
                tgt["degrees"] = r["degrees"]
            if not tgt.get("company_url") and r.get("company_url"):
                tgt["company_url"] = r["company_url"]
        for l in r["locations"]:
            if l not in tgt["locations"]:
                tgt["locations"].append(l)
        if r["url"] and r["url"] != tgt["url"] and r["url"] not in tgt.setdefault("alt_urls", []):
            tgt["alt_urls"].append(r["url"])
        if r["posted"] and (not tgt["posted"] or r["posted"] < tgt["posted"]):
            tgt["posted"] = r["posted"]
        if r["terms"] and not r["season_assumed"]:
            if tgt["season_assumed"]:
                tgt["terms"], tgt["season_assumed"] = list(r["terms"]), False
            else:
                for t in r["terms"]:
                    if t not in tgt["terms"]:
                        tgt["terms"].append(t)
        tgt["active"] = tgt["active"] or r["active"]
        if k1 and k1 not in by_url:
            by_url[k1] = tgt
    return order

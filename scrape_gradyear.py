"""Fetch each posting page and extract graduation-year / school-year requirements.

Writes gradyear_cache.json  {listing_id: {fetched, source, years, min, max, words, note}}
refresh.py reads that cache and adds grad-year fields to index.html.

Usage: python scrape_gradyear.py            (only fetches ids not already in the cache)
       python scrape_gradyear.py --force    (refetch everything)
"""
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "gradyear_cache.json")
TEXTS = os.path.join(HERE, "gradyear_texts.json")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
SUMMER_YEAR = 2027  # internship summer; "rising junior" etc. are interpreted relative to this


def get(url, accept="text/html,application/json", timeout=25, data=None, headers=None):
    h = {"User-Agent": UA, "Accept": accept, "Accept-Language": "en-US,en;q=0.9"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def strip_html(s):
    s = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


# ---------- per-ATS fetchers: return plain text of the job description ----------
_ashby_cache = {}


def fetch_greenhouse(u):
    m = re.search(r"greenhouse\.io/([^/]+)/jobs/(\d+)", u)
    if not m:
        return None
    board, jid = m.groups()
    try:
        j = json.loads(get(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{jid}", "application/json"))
        return strip_html(html.unescape(j.get("content", ""))) + " " + j.get("title", "")
    except Exception:
        return strip_html(get(u))


def fetch_lever(u):
    m = re.search(r"jobs\.lever\.co/([^/]+)/([0-9a-f-]{36})", u)
    if not m:
        return None
    co, jid = m.groups()
    try:
        j = json.loads(get(f"https://api.lever.co/v0/postings/{co}/{jid}", "application/json"))
        parts = [j.get("descriptionPlain", ""), j.get("additionalPlain", "")]
        for l in j.get("lists", []):
            parts.append(l.get("text", "") + " " + strip_html(l.get("content", "")))
        return " ".join(parts)
    except Exception:
        return strip_html(get(u))


def fetch_ashby(u):
    m = re.search(r"jobs\.ashbyhq\.com/([^/?]+)/([0-9a-f-]{36})", u)
    if not m:
        return None
    org, jid = m.groups()
    if org not in _ashby_cache:
        try:
            j = json.loads(get(f"https://api.ashbyhq.com/posting-api/job-board/{org}?includeCompensation=false", "application/json"))
            _ashby_cache[org] = {x["id"]: x for x in j.get("jobs", [])}
        except Exception:
            _ashby_cache[org] = {}
    x = _ashby_cache[org].get(jid)
    if x:
        return strip_html(x.get("descriptionHtml") or x.get("descriptionPlain") or "")
    # fallback: GraphQL single posting
    body = json.dumps({"operationName": "ApiJobPosting", "variables": {"organizationHostedJobsPageName": org, "jobPostingId": jid},
                       "query": "query ApiJobPosting($organizationHostedJobsPageName: String!, $jobPostingId: String!) { jobPosting(organizationHostedJobsPageName: $organizationHostedJobsPageName, jobPostingId: $jobPostingId) { title descriptionHtml } }"}).encode()
    j = json.loads(get("https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiJobPosting", "application/json", data=body,
                       headers={"Content-Type": "application/json"}))
    jp = (j.get("data") or {}).get("jobPosting") or {}
    return strip_html(jp.get("descriptionHtml", "")) + " " + jp.get("title", "")


def fetch_workday(u):
    p = urllib.parse.urlparse(u)
    host = p.netloc
    tenant = host.split(".")[0]
    segs = [s for s in p.path.split("/") if s]
    if segs and re.fullmatch(r"[a-z]{2}-[A-Z]{2}", segs[0]):
        segs = segs[1:]
    if len(segs) < 3 or segs[1] != "job":
        return strip_html(get(u))
    site = segs[0]
    api = f"https://{host}/wday/cxs/{tenant}/{site}/" + "/".join(segs[1:])
    j = json.loads(get(api, "application/json"))
    info = j.get("jobPostingInfo", {})
    return strip_html(info.get("jobDescription", "")) + " " + info.get("title", "")


def fetch_smartrecruiters(u):
    m = re.search(r"jobs\.smartrecruiters\.com/([^/]+)/(\d+)", u)
    if not m:
        return None
    co, jid = m.groups()
    try:
        j = json.loads(get(f"https://api.smartrecruiters.com/v1/companies/{co}/postings/{jid}", "application/json"))
        secs = j.get("jobAd", {}).get("sections", {})
        return " ".join(strip_html(v.get("text", "")) for v in secs.values() if isinstance(v, dict))
    except Exception:
        return strip_html(get(u))


def fetch_oracle(u):
    m = re.search(r"/sites/([^/]+)/job/(\d+)", u)
    if not m:
        return strip_html(get(u))
    site, jid = m.groups()
    p = urllib.parse.urlparse(u)
    api = (f"https://{p.netloc}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails"
           f"?expand=all&onlyData=true&finder=ByRequisitionId;requisitionId={jid},siteNumber={site}")
    try:
        j = json.loads(get(api, "application/json"))
    except Exception:
        j = json.loads(get(api, "application/vnd.oracle.adf.resourceitem+json",
                           headers={"Ora-Irc-Language": "en", "REST-Framework-Version": "4"}))
    items = j.get("items", [])
    if not items:
        return ""
    it = items[0]
    return strip_html(" ".join(str(it.get(k, "")) for k in ("ExternalDescriptionStr", "ExternalQualificationsStr", "ExternalResponsibilitiesStr", "Title")))


def fetch_simplify(u):
    # simplify.jobs/p/<uuid> pages are JS-rendered; use their public API
    m = re.search(r"simplify\.jobs/p/([0-9a-f-]{36})", u)
    if not m:
        return None
    j = json.loads(get(f"https://api.simplify.jobs/v2/job-posting/{m.group(1)}/company", "application/json"))
    return strip_html(j.get("description", "")) + " " + json.dumps(j.get("requirements", ""))


def fetch_workable(u):
    m = re.search(r"apply\.workable\.com/([^/]+)/j/([A-Za-z0-9]+)", u)
    if not m:
        return None
    j = json.loads(get(f"https://apply.workable.com/api/v2/accounts/{m.group(1)}/jobs/{m.group(2)}", "application/json"))
    return strip_html(" ".join(str(j.get(k, "")) for k in ("title", "description", "requirements", "benefits")))


def fetch_generic(u):
    try:
        txt = strip_html(get(u))
    except Exception as e:  # IncompleteRead still carries partial data
        partial = getattr(e, "partial", None)
        if not partial:
            raise
        txt = strip_html(partial.decode("utf-8", "replace"))
    return txt


# Hosts that render the posting with JavaScript or block plain fetches; no point requesting them.
JS_ONLY = ("lifeattiktok.com", "jobs.bytedance.com", "icims.com", "careers.amd.com", "higher.gs.com", "taleo.net",
           "phenompeople.com", "eightfold.ai", "avature.net", "successfactors.com", "brassring.com", "careers.aqr.com")


def fetch(u):
    host = urllib.parse.urlparse(u).netloc.lower()
    if any(h in host for h in JS_ONLY):
        return "", "js-page"
    if "greenhouse.io" in host:
        t = fetch_greenhouse(u); src = "greenhouse"
    elif "lever.co" in host:
        t = fetch_lever(u); src = "lever"
    elif "ashbyhq.com" in host:
        t = fetch_ashby(u); src = "ashby"
    elif "myworkdayjobs.com" in host:
        t = fetch_workday(u); src = "workday"
    elif "smartrecruiters.com" in host:
        t = fetch_smartrecruiters(u); src = "smartrecruiters"
    elif "oraclecloud.com" in host:
        t = fetch_oracle(u); src = "oracle"
    elif "simplify.jobs" in host:
        t = fetch_simplify(u); src = "simplify"
    elif "workable.com" in host:
        t = fetch_workable(u); src = "workable"
    else:
        t = fetch_generic(u); src = "html"
    if t is None:
        t = fetch_generic(u); src = "html"
    return t, src


# ---------- extraction ----------
# A year counts only when a graduation phrase sits within ~90 characters of it.
GRAD_CTX = re.compile(r"((?<!under)(?<!post)(?<!post-)graduat\w*|class of|grad(?:uation)? (?:date|year)|degree (?:by|in|completion|conferr\w*|expected|anticipated)|"
                      r"expected (?:to )?(?:graduate|complete|completion)|anticipated (?:graduation|completion)|completion date|"
                      r"complet\w* (?:your|their|a|an|the) (?:degree|bachelor|studies)|conferral|obtain\w* (?:your|their|a) degree)", re.I)
NOISE = re.compile(r"(?i)(start date|internship dates?|program dates?|summer of 20\d\d|summer 20\d\d|20\d\d summer|©|copyright|posted|deadline|apply by|applications? (?:close|due)|"
                   r"begin\w* in|starting in|start in|onboard|hours from|from (?:late|early|mid)|to (?:late|early|mid)|through (?:late|early|mid|august|september)|"
                   r"(?:january|february|march|april|may|june|july|august|september|october|november|december) \d{1,2},? 20\d\d)")
# school-year words for the academic year in which applications happen (2026-27) -> expected class year
YEAR_WORDS = [
    (r"rising sophomores?", 2030), (r"rising juniors?", 2029), (r"rising seniors?", 2028),
    (r"\bfreshm[ae]n\b", 2030), (r"\bfirst[- ]years?\b(?= (?:student|undergrad))", 2030),
    (r"\bsophomores?\b", 2029), (r"\bsecond[- ]year\b(?= (?:student|undergrad))", 2029),
    (r"\b(?:current(?:ly)?(?: a)?|college|university|undergraduate|incoming|academic) juniors?\b", 2028),
    (r"\bjuniors?\b(?= (?:standing|status|year|students?|undergrad\w*|or|and|,|/))", 2028),
    (r"\bthird[- ]year\b(?= (?:student|undergrad))", 2028), (r"\bpenultimate\b", 2028),
    (r"\b(?:current(?:ly)?(?: a)?|college|university|undergraduate|non-graduating|graduating) seniors?\b", 2027),
    (r"\bseniors?\b(?= (?:standing|status|year|students?|undergrad\w*))", 2027),
    (r"\bfinal[- ]year\b(?= (?:student|undergrad|of))", 2027),
]


OPEN_LATER = re.compile(r"(?i)^\s*(?:,|or)?\s*(?:or )?(?:later|after|beyond|onwards?)")          # "December 2027 or later"
OPEN_EARLIER = re.compile(r"(?i)(?:completed|at least|minimum(?: of)?|or higher|or above|and above|or beyond|\+)")  # "Junior or higher", "Completed sophomore year"
OPEN_MAX = 2032   # stands for "any later year"


def analyze(text):
    t = " " + re.sub(r"\s+", " ", text) + " "
    years = set()
    notes = []
    open_later = False
    for m in re.finditer(r"\b(20(2[6-9]|3[0-2]))\b", t):
        y = int(m.group(1))
        lo, hi = max(0, m.start() - 90), min(len(t), m.end() + 90)
        window = t[lo:hi]
        near = t[max(0, m.start() - 40):m.end() + 40]
        if GRAD_CTX.search(window) and not NOISE.search(near):
            years.add(y)
            notes.append(window.strip())
            if OPEN_LATER.match(t[m.end():m.end() + 12]):
                open_later = True
    if years:
        years = set(range(min(years), (OPEN_MAX if open_later else max(years)) + 1))
    words = {}
    word_years = set()
    for pat, y in YEAR_WORDS:
        m = re.search(pat, t, re.I)
        if m:
            words[m.group(0).lower()] = y
            word_years.add(y)
            before, after = t[max(0, m.start() - 30):m.start()], t[m.end():m.end() + 16]
            # "junior or higher" / "completed sophomore year" mean that class year or any earlier-graduating one
            if OPEN_EARLIER.search(before) or OPEN_EARLIER.search(after):
                word_years.update(range(2027, y + 1))
            notes.append(t[max(0, m.start() - 100):m.end() + 100].strip())
    yrs = set(years) | word_years
    note = " … ".join(dict.fromkeys(notes))[:700]
    return dict(years=sorted(yrs), min=min(yrs) if yrs else None, max=max(yrs) if yrs else None, words=sorted(words), note=note)


def main():
    force = "--force" in sys.argv
    with open(os.path.join(HERE, "current_rows.json"), encoding="utf-8") as f:
        rows = json.load(f)
    cache = {}
    if os.path.exists(CACHE) and not force:
        with open(CACHE, encoding="utf-8") as f:
            cache = json.load(f)
    texts = {}
    if os.path.exists(TEXTS) and not force:
        with open(TEXTS, encoding="utf-8") as f:
            texts = json.load(f)
    if "--reanalyze" in sys.argv:  # re-run analyze() on stored text without refetching
        for jid, text in texts.items():
            if jid in cache and cache[jid].get("fetched"):
                cache[jid].update(analyze(text))
        todo = []
    else:
        todo = [r for r in rows if r["id"] not in cache]
    print(f"{len(rows)} rows, {len(todo)} to fetch")

    def work(r):
        try:
            text, src = fetch(r["url"])
            ok = bool(text) and len(text) > 200
            a = analyze(text) if ok else dict(years=[], min=None, max=None, words=[], note="")
            return r["id"], dict(fetched=ok, source=src, chars=len(text or ""), **a), (text if ok else "")
        except Exception as e:
            return r["id"], dict(fetched=False, source="error", error=str(e)[:120], years=[], min=None, max=None, words=[], note=""), ""

    def save():
        with open(CACHE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=0)
        with open(TEXTS, "w", encoding="utf-8") as f:
            json.dump(texts, f, ensure_ascii=False)

    t0 = time.time()
    done = 0
    with ThreadPoolExecutor(max_workers=12) as ex:
        for jid, res, text in ex.map(work, todo):
            cache[jid] = res
            if text:
                texts[jid] = text[:20000]
            done += 1
            if done % 50 == 0:
                print(f"  {done}/{len(todo)} ({time.time()-t0:.0f}s)", flush=True)
                save()
    save()
    fetched = sum(1 for v in cache.values() if v.get("fetched"))
    stated = sum(1 for v in cache.values() if v.get("years"))
    print(f"done in {time.time()-t0:.0f}s: fetched {fetched}/{len(cache)}, {stated} state a grad year or school year")


if __name__ == "__main__":
    main()

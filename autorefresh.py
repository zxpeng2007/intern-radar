"""Unattended refresh: download the list, scan new postings for class-year
wording, rebuild index.html / widget.json / listings.csv.

This is what the scheduled task (Windows) or cron job (Mac/Linux) runs.
Safe to run by hand too:  python autorefresh.py

Everything is logged to refresh.log next to this file. If GitHub cannot be
reached, the last downloaded listings are reused so the page never goes
missing; the log says so.
"""
import datetime
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "refresh.log")
os.chdir(HERE)
sys.path.insert(0, HERE)
HAS_CONSOLE = sys.stdout is not None and sys.stderr is not None


def log(msg):
    line = f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    if HAS_CONSOLE:
        try:
            print(line, flush=True)
        except OSError:
            pass


def main():
    if not HAS_CONSOLE:  # windowless: send stray prints from the imported modules to the log
        sys.stdout = sys.stderr = open(LOG, "a", encoding="utf-8")
    log("refresh start")
    import refresh
    import scrape_gradyear

    try:
        raw = refresh.load(offline=False)
        log("downloaded listings")
    except Exception as e:
        log(f"download failed ({e!r}); reusing the last listings.json")
        raw = refresh.load(offline=True)

    res = refresh.build(raw)
    refresh.write(res)  # writes current_rows.json, which the scraper reads
    log(f"built {len(res)} rows")

    try:
        sys.argv = ["scrape_gradyear.py"]
        scrape_gradyear.main()
        log("class-year scan done")
    except Exception as e:
        log(f"class-year scan failed: {e!r}")

    res = refresh.build(raw)
    refresh.write(res)
    us = refresh.open_to_you(res)
    stated = sum(1 for o in res if o["grad"] == "stated")
    log(f"refresh done: {len(res)} rows, {len(us)} US open, {stated} state a class year")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log("refresh crashed:\n" + traceback.format_exc())
        raise

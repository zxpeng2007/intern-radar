# Intern Radar

A personal internship tracker that runs on your own laptop. It takes the
[SimplifyJobs internship list](https://github.com/SimplifyJobs/Summer2027-Internships)
(the big GitHub README everyone refreshes), keeps only the postings **you** can
actually apply to, and gives you a filterable page, a CSV, and an iPhone
home-screen widget that updates itself.

What it filters that the README can't:

- **Your class year.** Postings rarely say "class of 2029" in the title, but
  many say it on the apply page ("graduating between December 2027 and June
  2028", "rising juniors"). Intern Radar reads every posting's page and tags
  each row with the graduation window it accepts, so a sophomore stops wasting
  time on rising-senior-only roles.
- **Citizenship and sponsorship.** If you're an international student, it
  flags defense contractors, national labs and federal bodies, where
  "US citizenship required" is usually in the fine print, and hides them by
  default.
- **Noise.** Closed roles, other seasons, wrong categories, grad-only roles,
  and duplicate rows for the same job in five cities are all collapsed away.

No accounts, no cloud, no dependencies beyond Python. Everything runs on your
machine.

## Quick start

You need Python 3.9 or newer ([python.org](https://www.python.org/downloads/);
on Windows tick *Add to PATH*).

```bash
git clone https://github.com/zxpeng2007/intern-radar.git
cd intern-radar
cp config.example.json config.json      # then edit it, see below
python autorefresh.py                   # download + scan postings, about a minute
python serve.py
```

Open http://localhost:8765. On Windows you can double-click `start.bat`
instead of the last three commands.

## Tell it who you are

Edit `config.json`:

| Key | What to put |
|---|---|
| `season` | The term you want, exactly as the list spells it: `"Summer 2027"`, `"Fall 2026"`, ... |
| `source` | The listings file of the repo for that season. The default is Summer 2027; for another season, use that repo's `.github/scripts/listings.json` URL on its `dev` branch. |
| `categories` | Any of `Software`, `AI/ML/Data`, `Quant`, `Hardware`, `Product`. |
| `degrees` | Keep postings open to any of these: `Bachelor's`, `Master's`, `PhD`, `MBA`. |
| `class_years` | Graduation years you can honestly present as, e.g. `[2029, 2030]`. The first is the default filter. |
| `needs_sponsorship` | `true` if you're on F-1/J-1 or will need a visa. Turns on the citizenship flags. |
| `expose` | `local` (this computer only), `tailscale` (this computer + your own phone), or `lan`. See below. |
| `port` | Default `8765`. |

Run `python refresh.py --offline` after changing filters to rebuild without
re-downloading.

## What you get

- **`index.html`** — the page. Chips for category, region and class year;
  toggles for co-ops and citizenship-likely roles; search; sortable columns;
  a checkbox per row to mark it applied (remembered in your browser). Hover a
  class-year tag to see the exact sentence it came from.
- **`listings.csv`** — the same rows for a spreadsheet.
- **`widget.json`** — the small payload the phone widget reads.

## Keep it fresh automatically

The list changes daily. Set a refresh every 3 hours:

**Windows** (one command, no admin needed):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File setup\windows_tasks.ps1
```

This registers two scheduled tasks: *Intern Radar Refresh* every 3 hours and
*Intern Radar Server* at logon, running in the background with no window.
`-Remove` deletes them, `-Hours 6` changes the interval.

**Mac / Linux**: add to `crontab -e`:

```
0 */3 * * * cd /path/to/intern-radar && python3 autorefresh.py
```

and keep `python3 serve.py` running (a `screen`/`tmux` session, a launchd
agent, or a systemd user service). `setup/start.sh` does a refresh and then
serves.

## iPhone widget

A home-screen widget showing how many roles are open to you, how many are new
this week, and the newest postings. Tap a posting to open its apply page.

It runs in [Scriptable](https://apps.apple.com/app/scriptable/id1405459188)
(free) because a native widget would need Xcode and a Mac. It reaches your
computer over [Tailscale](https://tailscale.com) (free for personal use), a
private network between your own devices, so the page is never on the public
internet.

1. Install Tailscale on the computer and on the phone, sign in to the same
   account on both.
2. Set `"expose": "tailscale"` in `config.json` (the default) and restart
   `serve.py`. It prints the Tailscale address it bound, something like
   `http://100.101.102.103:8765`. Open that in Safari on the phone to confirm.
3. In Scriptable, tap **+**, paste the whole of `widget/InternRadar.js`,
   name it `InternRadar`, and set `BASE_URL` at the top to that address.
4. Run it once in Scriptable to preview, then add a Scriptable widget to the
   home screen and pick the `InternRadar` script.

Long-press the widget → *Edit Widget* → *Parameter* to change the view:
`2030`, `any`, `2029,quant`, `2030,swe`, and so on.

**Windows firewall:** if the phone can't connect, Windows is blocking Python
on the Tailscale adapter. The setup script prints the one admin command that
allows port 8765 from Tailscale addresses only.

## How the class-year scan works

`scrape_gradyear.py` fetches each posting through the job board's API where
one exists (Greenhouse, Lever, Ashby, Workday, SmartRecruiters, Workable) and
plain HTML otherwise, then looks for graduation phrases near a year
("expected graduation", "class of", "graduating between") and school-year words
("rising junior", "sophomore standing", "penultimate"). Phrases like "or
later" open the window upward; "junior or higher" opens it toward earlier
years. The sentence it matched is kept and shown on hover, because it is a
regex, not a recruiter: trust the posting over the tag.

It caches everything, so after the first run only new postings are fetched.
Pages rendered with JavaScript (TikTok, ByteDance, iCIMS, Oracle, Taleo and a
few others) cannot be read and show `?`.

## Files

| File | Purpose |
|---|---|
| `refresh.py` | download, filter, merge, write the outputs |
| `scrape_gradyear.py` | read posting pages for class-year wording (`--reanalyze` reruns the regex on cached text, `--force` refetches) |
| `autorefresh.py` | the two above in one unattended run, logged to `refresh.log` |
| `serve.py` | tiny server; binds loopback and, if configured, your Tailscale address |
| `template.html` | the page |
| `defense.txt` | companies flagged as citizenship-likely, one per line, edit freely |
| `widget/InternRadar.js` | the Scriptable widget |
| `setup/windows_tasks.ps1`, `setup/start.sh` | automation helpers |

## Honest limitations

- US-centric: "region" is US vs non-US, because the source list is.
- The class-year tag is a text match. It's right most of the time and shows
  its evidence; it is not a substitute for reading the posting.
- It does not apply for you, and it never will. Job boards ban bots, and
  recruiters see through them. Use it to pick targets, then apply yourself.

## Credits

Data from [SimplifyJobs/Summer2027-Internships](https://github.com/SimplifyJobs/Summer2027-Internships).
MIT license.

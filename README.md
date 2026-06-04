# 🕷️ Greatest Web Scraper

A full-featured, async Python web scraper with a beautiful terminal UI — built to scrape **anything**: static HTML, JS-rendered SPAs, JSON APIs, and bot-protected sites. Define what you want in a YAML job file (or a reusable output schema) and it returns clean, structured data. **No LLM in the extraction path** — everything is rule-based (CSS / XPath / JSONPath / regex).

---

## ✨ Features

- **Any site type** — static pages, JavaScript SPAs (Playwright), JSON APIs, auth-required pages
- **Anti-detection** — TLS fingerprint impersonation (curl_cffi), stealth browser patches, browser-cookie import, proxy rotation
- **Rule-based extraction** — CSS selectors, XPath, JMESPath, regex, transforms, auto JSON-LD parsing — no AI required
- **Reusable output schemas** — declare your desired output shape once, reuse across jobs
- **Auto-pagination** — follows "Next" links and infinite scroll with zero config
- **Rate limiting & retries** — token-bucket + adaptive limiting (speeds up on success, backs off on 429)
- **Beautiful TUI** — live Rich dashboard with stats, progress, and a result preview
- **Flexible export** — JSON / JSONL / CSV / SQLite + real-time webhook push
- **Scheduling** — cron-style recurring jobs via APScheduler

---

## 🚀 Quick start

```bash
pip install -r requirements.txt
playwright install chromium        # only needed for browser/stealth mode
```

```bash
# Instantly scrape one URL — no config needed
python scraper.py quick https://quotes.toscrape.com --select "span.text"

# Run a full job from a YAML file
python scraper.py run example_jobs/aggressive.yaml

# Validate a job before running
python scraper.py validate example_jobs/aggressive.yaml

# See every feature + command
python scraper.py info
```

Output is written to `output/<job_name>/` as `.jsonl` and `.csv`, with a live dashboard in the terminal.

---

## 📋 Job files

A job is a YAML file describing what to scrape and how. Minimal example:

```yaml
name: quotes
urls:
  - https://quotes.toscrape.com
mode: http            # http | browser | auto
rules:
  - name: quotes
    selector: span.text
    multiple: true
  - name: authors
    selector: small.author
    multiple: true
export:
  formats: [json, csv, terminal]
  output_dir: output/quotes
```

See [`example_jobs/`](example_jobs/) for ready-to-run jobs demonstrating TLS bypass, stealth browsing, auto-pagination, and more. Reusable output shapes live in [`schemas/`](schemas/).

### Anti-detection options

```yaml
mode: auto
stealth: true                 # patch Playwright automation signals
tls_impersonate: chrome124    # chrome124 | chrome110 | safari17_0 | firefox117
concurrency: 10
adaptive_rate: true
cookies_from: chrome          # import a logged-in session (chrome/firefox/edge/brave)
pagination:
  auto: true
  max_pages: 20
```

---

## 🏛️ Case study: Constituent Assembly Debates

A dedicated pipeline scrapes the [Constituent Assembly of India debates](https://www.constitutionofindia.net) into structured, queryable records.

### Extract

```bash
python cad_extract.py --volumes 1        # one volume
python cad_extract.py --volumes 1 7 9    # several
python cad_extract.py --all              # all 12 volumes (~38k speeches)
python cad_extract.py --url <debate-url> # a single day
```

Each record is one **speaker turn**:

```json
{
  "volume": 7,
  "date": "6th December 1948",
  "speaker": "B. R. Ambedkar",
  "text": "Sir, I beg to move: “That in clause (2) of article 19...”",
  "articles_referenced": ["19"]
}
```

It handles the hard parts automatically: speaker-turn grouping (forward-filling continuation paragraphs), volume detection from the page's id badges, date reformatting, and article-reference extraction via regex.

### Search

```bash
python cad_query.py "secular state"                       # full-text search
python cad_query.py "minority rights" --speaker Ambedkar  # stack filters
python cad_query.py --article 25                           # speeches citing Article 25
python cad_query.py --speaker "H. V. Kamath" --volume 7
python cad_query.py "fundamental rights" --full --limit 20
```

Builds a cached SQLite **FTS5** index over all speeches — search by full text, speaker, volume, or referenced article, ranked by relevance.

---

## 🗂️ Project structure

```
scraper.py            Typer CLI entry point (run / quick / schedule / validate / info)
cad_extract.py        Constituent Assembly Debates -> structured speech records
cad_query.py          Full-text search over scraped CAD speeches (SQLite FTS5)
core/
  models.py           Pydantic job-config models
  http_client.py      Async HTTP/2 client (httpx) with rate limiting
  tls_client.py       curl_cffi TLS-impersonation client
  browser.py          Stealthed Playwright engine
  stealth.py          Browser anti-detection patches
  pagination.py       Auto next-page / infinite-scroll detection
  cookie_import.py    Import cookies from a local browser
  extractor.py        Rule-based data extraction (CSS/XPath/JMESPath/regex)
  ai_extractor.py     Output-schema loader (rule-based, despite the name)
  engine.py           Orchestrates clients, concurrency, links, pagination
pipeline/exporters.py JSONL / CSV / SQLite / webhook export
ui/dashboard.py       Live Rich TUI dashboard
scheduler/runner.py   APScheduler cron runner
example_jobs/         Ready-to-run job files
schemas/              Reusable rule-based output schemas
```

---

## 🛠️ Tech stack

Python 3.12 · asyncio · httpx · curl_cffi · Playwright · BeautifulSoup + lxml · jmespath · Pydantic v2 · Rich · Typer · APScheduler · aiosqlite

---

## ⚖️ Use responsibly

Scrape sites you're authorized to access, respect their terms of service and `robots.txt`, and use rate limiting to avoid placing undue load on servers.

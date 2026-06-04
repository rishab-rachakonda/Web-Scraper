"""
Greatest Web Scraper — async, multi-engine, beautiful.

Usage:
    python scraper.py run example_jobs/quotes.yaml
    python scraper.py quick https://example.com --select "h1"
    python scraper.py schedule example_jobs/quotes.yaml
    python scraper.py validate example_jobs/hackernews.yaml
    python scraper.py info
"""
from __future__ import annotations

import sys

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

import asyncio
from pathlib import Path
from typing import Optional

import typer
import yaml
from rich import box
from rich.align import Align
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

app = typer.Typer(
    name="scraper",
    help="[bold bright_cyan]The Greatest Web Scraper on the Planet[/]",
    add_completion=False,
    rich_markup_mode="rich",
    no_args_is_help=True,
)
console = Console(legacy_windows=False)

_JOB_FILE_HELP = "Path to job YAML file"

# (format-key, label, description) for the interactive download menu.
_FORMAT_MENU = [
    ("csv",         "CSV",      "spreadsheet — Excel / Google Sheets"),
    ("xlsx",        "Excel",    ".xlsx workbook (styled, frozen header)"),
    ("json_pretty", "JSON",     "structured, pretty-printed"),
    ("jsonl",       "JSONL",    "one JSON object per line (streaming)"),
    ("pdf",         "PDF",      "printable report"),
    ("markdown",    "Markdown", ".md table"),
    ("html",        "HTML",     "web-page table you can open in a browser"),
]
_FORMAT_ALIASES = {"json": "json_pretty", "excel": "xlsx", "md": "markdown"}


def _parse_format_choice(raw: str) -> list[str]:
    """Turn '1,3' / 'csv,pdf' / 'all' into a list of format keys."""
    raw = raw.strip().lower()
    if raw in ("all", "*"):
        return [key for key, _, _ in _FORMAT_MENU]
    keys: list[str] = []
    valid = {key for key, _, _ in _FORMAT_MENU}
    for token in raw.replace(" ", ",").split(","):
        if not token:
            continue
        if token.isdigit() and 1 <= int(token) <= len(_FORMAT_MENU):
            key = _FORMAT_MENU[int(token) - 1][0]
        else:
            key = _FORMAT_ALIASES.get(token, token)
        if key in valid and key not in keys:
            keys.append(key)
    return keys


def _choose_formats() -> list[str]:
    """Show the download-format menu and return the chosen format keys.
    Falls back to CSV+JSON when input isn't interactive."""
    if not sys.stdin.isatty():
        return ["csv", "json_pretty"]
    from rich.prompt import Prompt

    table = Table(title="📦  How do you want your results?", box=box.ROUNDED,
                  title_style="bold bright_magenta", header_style="bold cyan")
    table.add_column("#", justify="right", style="bright_yellow")
    table.add_column("Format")
    table.add_column("Description", style="dim")
    for i, (_, label, desc) in enumerate(_FORMAT_MENU, 1):
        table.add_row(str(i), label, desc)
    table.add_row("*", "All of the above", "every format")
    console.print()
    console.print(table)

    while True:
        raw = Prompt.ask(
            "[bright_cyan]Choose format(s)[/] — numbers or names, comma-separated, or 'all'",
            default="1")
        chosen = _parse_format_choice(raw)
        if chosen:
            return chosen
        console.print("[yellow]Didn't recognise that — try e.g. 1,3 or csv,pdf or all.[/]")


def _load_job(path: Path):
    from core.models import JobConfig
    with open(path, encoding="utf-8") as f:
        return JobConfig(**yaml.safe_load(f))


def _collect_preview_keys(items) -> list[str]:
    seen: set[str] = set()
    keys: list[str] = []
    for item in items:
        for k in item.data:
            if not k.startswith("_") and k not in seen:
                seen.add(k)
                keys.append(k)
    return keys[:6]


def _build_preview_row(item, keys: list[str]) -> list[str]:
    url = item.url[:40] + ("..." if len(item.url) > 40 else "")
    cells = [url]
    for k in keys:
        v = item.data.get(k)
        cells.append(str(v)[:55] if v is not None else "")
    return cells


def _print_preview(items):
    if not items:
        return
    keys = _collect_preview_keys(items)
    if not keys:
        return

    t = Table(
        title="[bold bright_cyan] EXTRACTED DATA (preview - first 10 rows) [/]",
        box=box.ROUNDED,
        border_style="cyan",
        header_style="bold bright_cyan",
        show_lines=True,
        expand=True,
    )
    t.add_column("URL", style="dim cyan", max_width=40, no_wrap=True)
    for k in keys:
        t.add_column(k.replace("_", " ").title(), style="bright_white", max_width=55)

    for item in items:
        if not item.error:
            t.add_row(*_build_preview_row(item, keys))

    console.print()
    console.print(t)


async def _run_job(job, verbose: bool = False, ask_format: bool = False):
    from core.engine import ScraperEngine
    from core.models import ScraperStats, ScrapedItem
    from pipeline.exporters import Exporter
    from ui.dashboard import ScraperDashboard

    dash = ScraperDashboard(job.name)
    items: list[ScrapedItem] = []

    dash.start(total=len(job.urls))

    def on_item(item: ScrapedItem):
        items.append(item)
        if verbose and item.error:
            console.log(f"[red]ERR[/] {item.url}: {item.error}")

    def on_stats(stats: ScraperStats):
        if items:
            dash.update(items[-1], stats)

    engine = ScraperEngine(job, on_item=on_item, on_stats=on_stats)

    try:
        all_items = await engine.run()
        dash.stop()
        if engine.smart_notes:
            from rich.panel import Panel
            console.print(Panel(
                "\n".join(f"• {n}" for n in engine.smart_notes),
                title="🧠 decisions & notices", border_style="bright_magenta", expand=False))
        if ask_format and all_items:
            chosen = _choose_formats()
            # keep the live terminal preview, swap in the chosen file formats
            job.export.formats = chosen + (["terminal"] if "terminal" in job.export.formats else [])
        paths = await Exporter(job.export, job.name).export_all(all_items)
        dash.print_summary(paths)
        if "terminal" in job.export.formats:
            _print_preview(all_items[:10])
    except KeyboardInterrupt:
        dash.stop()
        console.print("\n[yellow]Interrupted.[/]")
        raise typer.Exit(0)
    except Exception as exc:
        dash.stop()
        console.print(f"\n[red bold]Error:[/] {exc}")
        if verbose:
            console.print_exception()
        raise typer.Exit(1)


@app.command()
def run(
    job_file: Path = typer.Argument(..., help=_JOB_FILE_HELP, exists=True),
    output_dir: Optional[str] = typer.Option(None, "--output", "-o", help="Override output directory"),
    fmt: Optional[str] = typer.Option(None, "--format", "-f", help="Override output format(s), e.g. csv,pdf,all"),
    ask: bool = typer.Option(False, "--ask", help="Choose output format(s) interactively after scraping"),
    polite: bool = typer.Option(False, "--polite", help="Respect robots.txt (allow/disallow + crawl-delay)"),
    no_browser: bool = typer.Option(False, "--no-browser", help="Force HTTP mode (skip Playwright)"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed error logs"),
):
    """[bold bright_cyan]Run a scraping job defined in a YAML file.[/]"""
    job = _load_job(job_file)
    if output_dir:
        job.export.output_dir = output_dir
    if no_browser:
        job.mode = "http"
    if polite:
        job.respect_robots = True
    if fmt:
        chosen = _parse_format_choice(fmt)
        if not chosen:
            console.print(f"[red]Unknown format(s):[/] {fmt}")
            raise typer.Exit(1)
        job.export.formats = chosen + ["terminal"]
    asyncio.run(_run_job(job, verbose, ask_format=ask))


@app.command()
def quick(
    url: str = typer.Argument(..., help="URL to scrape"),
    selector: Optional[str] = typer.Option(None, "--select", "-s", help="CSS selector to extract"),
    attribute: Optional[str] = typer.Option(None, "--attr", "-a", help="HTML attribute to pull (e.g. href)"),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Output file (.json/.csv)"),
    follow: bool = typer.Option(False, "--follow", "-f", help="Follow same-domain links"),
    depth: int = typer.Option(1, "--depth", "-d", help="Max crawl depth when following links"),
    use_browser: bool = typer.Option(False, "--browser", "-b", help="Use Playwright browser engine"),
    fmt: Optional[str] = typer.Option(None, "--format", help="Output format(s), e.g. csv,pdf,all"),
    ask: bool = typer.Option(False, "--ask", help="Choose output format(s) interactively after scraping"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """[bold bright_cyan]Instantly scrape a URL — no config file needed.[/]"""
    from core.models import ExportConfig, ExtractorRule, JobConfig

    rules = []
    if selector:
        rules.append(ExtractorRule(
            name="data",
            selector=selector,
            attribute=attribute or None,
            multiple=True,
        ))

    formats = ["terminal"]
    out_dir = "output"
    stem = None
    if output:
        p = Path(output)
        stem = p.stem
        out_dir = str(p.parent)
        ext = p.suffix.lower().lstrip(".")
        formats = ["json", "terminal"] if ext in ("json", "jsonl") else ["csv", "terminal"]
    if fmt:
        chosen = _parse_format_choice(fmt)
        if not chosen:
            console.print(f"[red]Unknown format(s):[/] {fmt}")
            raise typer.Exit(1)
        formats = chosen + ["terminal"]

    job = JobConfig(
        name=stem or "quick",
        urls=[url],
        mode="browser" if use_browser else "auto",
        rules=rules,
        follow_links=follow,
        max_depth=depth,
        export=ExportConfig(formats=formats, output_dir=out_dir, filename=stem),
    )
    asyncio.run(_run_job(job, verbose, ask_format=ask))


@app.command()
def smart(
    url: str = typer.Argument(..., help="URL to scrape"),
    item: Optional[str] = typer.Option(None, "--item", help="Override the auto-detected item_selector"),
    output_dir: Optional[str] = typer.Option(None, "--output", "-o", help="Output directory"),
    fmt: Optional[str] = typer.Option(None, "--format", "-f",
        help="Output format(s), comma-separated (csv,xlsx,json,jsonl,pdf,markdown,html,all). "
             "Omit to be asked interactively."),
    pages: int = typer.Option(1, "--pages", "-p", help="Max pages to auto-paginate"),
    polite: bool = typer.Option(False, "--polite", help="Respect robots.txt"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """[bold bright_magenta]Let the scraper decide everything[/] — transport, browser-vs-HTTP,
    and the extraction schema. Give it just a URL; pick your download format when prompted."""
    from core.models import ExportConfig, JobConfig, PaginationConfig

    ask_format = fmt is None
    if ask_format:
        formats = ["terminal"]              # placeholder; chosen after scraping
    else:
        chosen = _parse_format_choice(fmt)
        if not chosen:
            console.print(f"[red]Unknown format(s):[/] {fmt}")
            raise typer.Exit(1)
        formats = chosen + ["terminal"]

    job = JobConfig(
        name="smart",
        urls=[url],
        mode="smart",                       # the project decides the rest
        item_selector=item,                 # None unless you override
        respect_robots=polite,
        pagination=PaginationConfig(auto=True, max_pages=pages) if pages > 1 else None,
        export=ExportConfig(formats=formats, output_dir=output_dir or "output/smart"),
    )
    asyncio.run(_run_job(job, verbose, ask_format=ask_format))


def _slug_from_url(url: str) -> str:
    from urllib.parse import urlparse
    host = urlparse(url).netloc.replace("www.", "").split(".")[0]
    return "".join(ch if ch.isalnum() else "_" for ch in host).strip("_") or "scrape"


@app.command()
def detect(
    url: str = typer.Argument(..., help="URL to analyse"),
    save: Optional[Path] = typer.Option(None, "--save", "-s", help="Write the generated job YAML to this file"),
):
    """[bold bright_cyan]Preview the auto-detected schema[/] for a URL and print a ready-to-run
    job YAML — without scraping. Edit it, then `python scraper.py run` it."""
    import httpx
    from core.autoschema import detect_schema

    console.print(f"[cyan]Probing[/] {url} ...")
    try:
        ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
        html = httpx.get(url, headers={"User-Agent": ua}, timeout=30, follow_redirects=True).text
    except Exception as exc:
        console.print(f"[red]Fetch failed:[/] {exc}")
        raise typer.Exit(1)

    selector, rules = detect_schema(html)
    if not selector:
        console.print("[yellow]No repeating structure detected.[/] "
                      "The page may be JS-rendered (try `smart`) or need manual selectors.")
        raise typer.Exit(0)

    table = Table(title=f"🔎  Detected schema  ·  item_selector = [bold]{selector}[/]",
                  box=box.ROUNDED, header_style="bold cyan")
    table.add_column("Field"); table.add_column("Selector", style="dim"); table.add_column("Extracts")
    for r in rules:
        kind = f"attribute: {r.attribute}" if r.attribute else (r.transform or "text")
        table.add_row(r.name, r.selector, kind)
    console.print(table)

    name = _slug_from_url(url)
    job_dict = {
        "name": name,
        "urls": [url],
        "mode": "http",
        "item_selector": selector,
        "rules": [
            {"name": r.name, "selector": r.selector,
             **({"attribute": r.attribute} if r.attribute else {}),
             **({"transform": r.transform} if r.transform else {})}
            for r in rules
        ],
        "export": {"formats": ["csv", "json", "terminal"], "output_dir": f"output/{name}"},
    }
    yaml_str = yaml.safe_dump(job_dict, sort_keys=False, allow_unicode=True)
    console.print(Panel(yaml_str.rstrip(), title="📝 generated job YAML",
                        border_style="green", expand=False))

    if save:
        save.write_text(yaml_str, encoding="utf-8")
        console.print(f"[green]Saved[/] → {save}    run it with: python scraper.py run {save}")
    else:
        console.print("[dim]Tip: re-run with --save example_jobs/%s.yaml to keep it.[/]" % name)


@app.command(name="schedule")
def schedule_cmd(
    job_file: Path = typer.Argument(..., help=_JOB_FILE_HELP, exists=True),
):
    """[bold bright_cyan]Run a job repeatedly on its cron schedule.[/]"""
    job = _load_job(job_file)

    if not job.schedule:
        console.print("[red]Error:[/] No 'schedule' cron expression in this job file.")
        raise typer.Exit(1)

    from scheduler.runner import JobScheduler

    sched = JobScheduler()

    async def _run():
        await _run_job(job)

    sched.add(job, _run)
    sched.start()

    console.print(Panel(
        f"[bright_cyan]Job:[/]      {job.name}\n"
        f"[bright_cyan]Schedule:[/] {job.schedule}\n\n"
        "[dim]Press Ctrl+C to stop[/]",
        title="[bold bright_cyan] SCHEDULER RUNNING [/]",
        border_style="cyan",
        padding=(1, 2),
    ))

    try:
        asyncio.get_event_loop().run_forever()
    except KeyboardInterrupt:
        sched.stop()
        console.print("\n[yellow]Scheduler stopped.[/]")


@app.command()
def validate(
    job_file: Path = typer.Argument(..., help=_JOB_FILE_HELP),
):
    """[bold bright_cyan]Validate a job configuration file.[/]"""
    try:
        job = _load_job(job_file)
        extraction = (
            f"[magenta]schema[/] -> {job.output_schema}"
            if job.output_schema
            else f"inline rules ({len(job.rules)})"
        )
        console.print(Panel(
            f"[green]✓  Valid configuration[/]\n\n"
            f"[bright_cyan]Name        [/] {job.name}\n"
            f"[bright_cyan]Description [/] {job.description or '-'}\n"
            f"[bright_cyan]URLs        [/] {len(job.urls)}\n"
            f"[bright_cyan]Mode        [/] {job.mode}\n"
            f"[bright_cyan]Extraction  [/] {extraction}\n"
            f"[bright_cyan]Export      [/] {', '.join(job.export.formats)}\n"
            f"[bright_cyan]Follow links[/] {job.follow_links} (depth={job.max_depth})\n"
            f"[bright_cyan]Schedule    [/] {job.schedule or 'none'}\n"
            f"[bright_cyan]Proxy       [/] {len(job.proxy.urls) if job.proxy else 0} proxies",
            title="[bold bright_green] JOB VALID [/]",
            border_style="green",
            padding=(1, 2),
        ))
    except Exception as exc:
        console.print(Panel(
            f"[red]✗  {exc}[/]",
            title="[bold red] VALIDATION FAILED [/]",
            border_style="red",
            padding=(1, 2),
        ))
        raise typer.Exit(1)


@app.command()
def info():
    """[bold bright_cyan]Show version and feature overview.[/]"""
    t = Table(box=box.ROUNDED, border_style="bright_cyan", show_header=False)
    t.add_column(style="bright_cyan bold", width=22)
    t.add_column(style="white")

    rows = [
        ("Engines",       "HTTP/2 (httpx)  +  Browser (Playwright Chromium)"),
        ("Auto-detect",   "Detects JS-heavy SPAs and switches engine automatically"),
        ("Auth",          "Basic  |  Bearer token  |  Cookies  |  Form login"),
        ("Proxy",         "Proxy rotation with per-domain rate limiting"),
        ("Rate limiting", "Token-bucket algorithm, configurable burst"),
        ("Retry",         "Exponential backoff, configurable status codes"),
        ("Extraction",    "CSS selectors  |  XPath  |  JMESPath  |  Regex"),
        ("Output schema", "Reusable YAML schema files define your output shape"),
        ("Export",        "JSON Lines  |  CSV  |  SQLite  |  Terminal table"),
        ("Scheduling",    "APScheduler cron integration"),
        ("Link following","Same-domain crawler with configurable depth"),
        ("Dashboard",     "Live Rich TUI with real-time stats and results"),
    ]
    for k, v in rows:
        t.add_row(k, v)

    console.print()
    console.print(Panel(
        Align.center(t),
        title="[bold bright_cyan] GREATEST WEB SCRAPER - Feature Overview [/]",
        border_style="bright_cyan",
        padding=(1, 2),
    ))
    console.print()
    console.print("  [dim]python scraper.py --help[/]  for commands")
    console.print("  [dim]python scraper.py run example_jobs/quotes.yaml[/]  to start scraping")
    console.print()


if __name__ == "__main__":
    app()

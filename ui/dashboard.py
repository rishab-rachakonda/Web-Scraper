from __future__ import annotations

from typing import TYPE_CHECKING

from rich import box
from rich.align import Align
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table
from rich.text import Text

if TYPE_CHECKING:
    from core.models import ScrapedItem, ScraperStats

_BANNER = """\
 ██████╗ ██████╗ ███████╗ █████╗ ████████╗███████╗███████╗████████╗
██╔════╝ ██╔══██╗██╔════╝██╔══██╗╚══██╔══╝██╔════╝██╔════╝╚══██╔══╝
██║  ███╗██████╔╝█████╗  ███████║   ██║   █████╗  ███████╗   ██║
██║   ██║██╔══██╗██╔══╝  ██╔══██║   ██║   ██╔══╝  ╚════██║   ██║
╚██████╔╝██║  ██║███████╗██║  ██║   ██║   ███████╗███████║   ██║
 ╚═════╝ ╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝   ╚═╝   ╚══════╝╚══════╝   ╚═╝
     ██╗    ██╗███████╗██████╗     ███████╗ ██████╗██████╗  █████╗ ██████╗ ███████╗██████╗
     ██║    ██║██╔════╝██╔══██╗    ██╔════╝██╔════╝██╔══██╗██╔══██╗██╔══██╗██╔════╝██╔══██╗
     ██║ █╗ ██║█████╗  ██████╔╝    ███████╗██║     ██████╔╝███████║██████╔╝█████╗  ██████╔╝
     ██║███╗██║██╔══╝  ██╔══██╗    ╚════██║██║     ██╔══██╗██╔══██║██╔═══╝ ██╔══╝  ██╔══██╗
     ╚███╔███╔╝███████╗██████╔╝    ███████║╚██████╗██║  ██║██║  ██║██║     ███████╗██║  ██║
      ╚══╝╚══╝ ╚══════╝╚═════╝     ╚══════╝ ╚═════╝╚═╝  ╚═╝╚═╝  ╚═╝╚═╝     ╚══════╝╚═╝  ╚═╝"""

_GRADIENT = [
    "bright_cyan", "cyan", "bright_blue", "blue",
    "bright_magenta", "magenta", "bright_cyan",
]


def _header_panel() -> Panel:
    text = Text()
    for i, line in enumerate(_BANNER.split("\n")):
        text.append(line + "\n", style=f"bold {_GRADIENT[i % len(_GRADIENT)]}")
    return Panel(
        Align.center(text),
        border_style="bright_cyan",
        padding=(0, 1),
    )


def _stats_panel(stats: "ScraperStats") -> Panel:
    t = Table(box=None, show_header=False, padding=(0, 2))
    t.add_column(style="bright_cyan bold")
    t.add_column(style="bright_white")

    elapsed = stats.elapsed_seconds
    clock = f"{int(elapsed // 60):02d}:{int(elapsed % 60):02d}"
    ok = stats.completed - stats.failed
    if stats.success_rate >= 80:
        rate_color = "green"
    elif stats.success_rate >= 50:
        rate_color = "yellow"
    else:
        rate_color = "red"
    bw = stats.bytes_downloaded
    bw_str = f"{bw / 1_048_576:.2f} MB" if bw >= 1_048_576 else f"{bw / 1024:.1f} KB"

    t.add_row("Scraped", f"[green]{ok}[/] / [white]{stats.total_urls}[/]")
    t.add_row("Failed", f"[red]{stats.failed}[/]")
    t.add_row("Speed", f"[yellow]{stats.urls_per_second:.1f}[/] req/s")
    t.add_row("Success", f"[{rate_color}]{stats.success_rate:.1f}%[/]")
    t.add_row("Items", f"[magenta]{stats.items_extracted}[/]")
    t.add_row("Bandwidth", f"[cyan]{bw_str}[/]")
    t.add_row("Elapsed", f"[white]{clock}[/]")

    return Panel(t, title="[bold bright_cyan] LIVE STATS [/]", border_style="cyan", padding=(1, 1))


def _format_result_row(item: "ScrapedItem") -> tuple[str, "Text"]:
    if item.error:
        return "[red]X ERR[/]", Text(item.error[:90], style="red dim")
    code = item.status_code or 0
    st = f"[green]OK {code}[/]" if code < 400 else f"[yellow]! {code}[/]"
    parts = [
        f"[dim]{k}:[/][white]{str(v)[:45]}[/]"
        for k, v in list(item.data.items())[:3]
        if not k.startswith("_")
    ]
    return st, Text.from_markup("  ".join(parts) or "[dim italic]no fields[/]")


def _results_panel(items: "list[ScrapedItem]") -> Panel:
    t = Table(
        box=box.MINIMAL_DOUBLE_HEAD,
        header_style="bold bright_magenta",
        border_style="magenta",
        row_styles=["", "dim"],
        expand=True,
        show_lines=False,
    )
    t.add_column("ST", width=7, justify="center")
    t.add_column("URL", ratio=2, no_wrap=True)
    t.add_column("Data Preview", ratio=3)
    t.add_column("At", width=10)

    for item in items[-18:]:
        st, preview = _format_result_row(item)
        url_short = item.url[:55] + ("..." if len(item.url) > 55 else "")
        t.add_row(st, Text(url_short, style="cyan"), preview, item.scraped_at.strftime("%H:%M:%S"))

    return Panel(t, title="[bold bright_magenta] RESULTS [/]", border_style="magenta", padding=(0, 0))


class ScraperDashboard:
    def __init__(self, job_name: str):
        self._job_name = job_name
        self._items: list["ScrapedItem"] = []
        self._stats: "ScraperStats | None" = None
        self._console = Console(legacy_windows=False)
        self._progress = Progress(
            SpinnerColumn(style="bright_cyan"),
            TextColumn("[bold bright_cyan]{task.description}"),
            BarColumn(bar_width=None, style="cyan", complete_style="bright_cyan"),
            TaskProgressColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            console=self._console,
        )
        self._task_id = None
        self._live: Live | None = None

    def _layout(self) -> Layout:
        from core.models import ScraperStats
        stats = self._stats or ScraperStats()

        root = Layout()
        root.split_column(
            Layout(name="header", size=11),
            Layout(name="body"),
            Layout(name="progress", size=5),
        )
        root["body"].split_row(
            Layout(name="stats", minimum_size=36, ratio=1),
            Layout(name="results", ratio=2),
        )
        root["header"].update(_header_panel())
        root["stats"].update(_stats_panel(stats))
        root["results"].update(_results_panel(self._items))
        root["progress"].update(
            Panel(
                self._progress,
                border_style="bright_cyan",
                title=f"[bold bright_cyan] JOB · {self._job_name.upper()} [/]",
            )
        )
        return root

    def start(self, total: int):
        from core.models import ScraperStats
        self._stats = ScraperStats(total_urls=total)
        self._task_id = self._progress.add_task(
            f"Scraping {self._job_name}...", total=total
        )
        self._live = Live(
            self._layout(),
            refresh_per_second=6,
            console=self._console,
            screen=False,
        )
        self._live.__enter__()

    def update(self, item: "ScrapedItem", stats: "ScraperStats"):
        self._items.append(item)
        self._stats = stats
        if self._task_id is not None:
            self._progress.update(
                self._task_id,
                completed=stats.completed,
                total=stats.total_urls,
            )
        if self._live:
            self._live.update(self._layout())

    def stop(self):
        if self._live:
            self._live.__exit__(None, None, None)
            self._live = None

    def print_summary(self, export_paths: list[str]):
        from core.models import ScraperStats
        stats = self._stats or ScraperStats()
        elapsed = stats.elapsed_seconds

        t = Table(
            title="[bold bright_green] SCRAPE COMPLETE [/]",
            box=box.DOUBLE_EDGE,
            border_style="bright_green",
            header_style="bold bright_green",
        )
        t.add_column("Metric", style="bright_cyan bold")
        t.add_column("Value", style="bright_white")
        t.add_row("Total URLs", str(stats.total_urls))
        t.add_row("Successful", f"[green]{stats.items_extracted}[/]")
        t.add_row("Failed", f"[red]{stats.failed}[/]")
        t.add_row("Success Rate", f"{stats.success_rate:.1f}%")
        t.add_row("Total Time", f"{elapsed:.1f}s")
        t.add_row("Avg Speed", f"{stats.urls_per_second:.2f} req/s")
        bw = stats.bytes_downloaded
        t.add_row("Bandwidth", f"{bw / 1024:.1f} KB")

        self._console.print()
        self._console.print(Align.center(t))

        if export_paths:
            self._console.print()
            self._console.print(Panel(
                "\n".join(f"[bright_cyan]→[/] [white]{p}[/]" for p in export_paths),
                title="[bold bright_green] OUTPUT FILES [/]",
                border_style="green",
                padding=(0, 2),
            ))

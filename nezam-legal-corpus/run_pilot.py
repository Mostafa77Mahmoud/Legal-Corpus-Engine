#!/usr/bin/env python3
"""
Pilot runner — Stage 1 only.

Usage:
    python run_pilot.py [LAW_ID]

LAW_ID defaults to EG_PDPL (Personal Data Protection Law).

Place the PDF at:  data/raw_pdfs/<pdf_filename>
The filename for each law is listed in config/law_registry.py.

Outputs:
    data/extracted_raw/<LAW_ID>.txt           — raw extracted text
    data/extracted_raw/<LAW_ID>_meta.json     — extraction metadata
    data/extracted_raw/<LAW_ID>_confidence.json — confidence report
"""

import json
import logging
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from config.law_registry import get_law
from config.settings import RAW_PDFS_DIR, RAW_TXTS_DIR
from pipeline import stage_1_extract, stage_1_5_val_extract
from utils.cost_tracker import CostTracker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)
console = Console()


def main() -> None:
    law_id = sys.argv[1] if len(sys.argv) > 1 else "EG_PDPL"

    try:
        law_entry = get_law(law_id)
    except KeyError as exc:
        console.print(f"[bold red]Error:[/] {exc}")
        sys.exit(1)

    # TXT source takes priority over PDF when txt_filename is registered
    if law_entry.txt_filename:
        source_path = RAW_TXTS_DIR / law_entry.txt_filename
        source_label = "TXT"
    else:
        source_path = RAW_PDFS_DIR / law_entry.pdf_filename
        source_label = "PDF"

    pdf_path = RAW_PDFS_DIR / law_entry.pdf_filename  # still passed to stage_1.run()

    console.print(Panel.fit(
        f"[bold]Nezam Legal Corpus — Stage 1 Pilot[/]\n"
        f"Law:    [cyan]{law_entry.law_name_ar}[/]\n"
        f"ID:     [cyan]{law_entry.law_id}[/]\n"
        f"Source: [cyan]{source_label} → {source_path}[/]",
        border_style="blue",
    ))

    if not source_path.exists():
        console.print(Panel(
            f"[bold red]Source file not found.[/]\n\n"
            f"Expected location:\n  [yellow]{source_path}[/]\n\n"
            f"Place the file there and re-run:\n"
            f"  [green]python run_pilot.py {law_id}[/]",
            title=f"Missing {source_label}",
            border_style="red",
        ))
        sys.exit(1)

    cost_tracker = CostTracker()

    console.rule("[bold blue]Stage 1: Extraction")
    extraction_result = stage_1_extract.run(
        pdf_path=pdf_path,
        law_entry=law_entry,
        cost_tracker=cost_tracker,
    )

    if not extraction_result.success:
        console.print(f"[bold red]Extraction failed:[/] {extraction_result.error}")
        sys.exit(1)

    console.rule("[bold blue]Stage 1.5: Confidence Scoring")
    confidence_report = stage_1_5_val_extract.run(
        law_entry=law_entry,
        extraction_source=extraction_result.extraction_source,
    )

    _print_extraction_report(extraction_result)
    _print_confidence_report(confidence_report)
    _print_cost_report(cost_tracker)


def _print_extraction_report(result) -> None:
    console.print()
    console.print(Panel.fit("[bold]Extraction Report", border_style="green"))

    table = Table(box=box.SIMPLE, show_header=False)
    table.add_column("Field", style="dim", width=30)
    table.add_column("Value")

    source_color = "green" if result.extraction_source == "pymupdf" else "yellow"
    table.add_row("Extraction source", f"[{source_color}]{result.extraction_source}[/]")
    table.add_row("Characters extracted", f"{result.char_count:,}")
    table.add_row("PDF pages", str(result.page_count))
    table.add_row("Article markers found", str(result.article_markers_found))
    table.add_row("Structural headings found", str(result.structural_headings_found))
    table.add_row("Arabic character density", f"{result.arabic_density:.4f}")
    table.add_row("Replacement char density", f"{result.replacement_density:.6f}")
    if result.extraction_model:
        table.add_row("OCR model", result.extraction_model)
    table.add_row("Output file", str(Path(result.raw_text_path).name))

    console.print(table)


def _print_confidence_report(report) -> None:
    console.print()
    passed_label = "[bold green]PASS ✓[/]" if report.passed else "[bold red]FAIL ✗[/]"
    review_label = "[bold red]Yes — flagged for human review[/]" if report.manual_review else "[green]No[/]"

    console.print(Panel.fit("[bold]Confidence Report", border_style="green"))

    table = Table(box=box.SIMPLE, show_header=True)
    table.add_column("Factor", style="cyan")
    table.add_column("Raw", justify="right")
    table.add_column("Normalized", justify="right")
    table.add_column("Weight", justify="right")
    table.add_column("Contribution", justify="right")

    breakdown = report.factor_breakdown
    for factor, vals in breakdown.items():
        table.add_row(
            factor,
            f"{vals['raw']:.4f}",
            f"{vals['norm']:.4f}",
            f"{vals['weight']:.2f}",
            f"{vals['contribution']:.4f}",
        )

    console.print(table)

    summary = Table(box=box.SIMPLE, show_header=False)
    summary.add_column("", style="dim", width=30)
    summary.add_column("")
    summary.add_row("Confidence score", f"[bold]{report.confidence_score:.4f}[/]")
    summary.add_row("Threshold", f"{report.threshold:.2f}")
    summary.add_row("Result", passed_label)
    summary.add_row("Manual review required", review_label)
    console.print(summary)


def _print_cost_report(tracker: CostTracker) -> None:
    console.print()
    console.print(Panel.fit("[bold]Cost Report", border_style="green"))
    summary = tracker.summary()

    if summary["total_api_calls"] == 0:
        console.print("  [green]$0.00[/] — no Gemini API calls made (PyMuPDF extraction succeeded).")
        return

    table = Table(box=box.SIMPLE, show_header=False)
    table.add_column("", style="dim", width=30)
    table.add_column("")
    table.add_row("Total API calls", str(summary["total_api_calls"]))
    table.add_row("Input tokens", f"{summary['total_input_tokens']:,}")
    table.add_row("Output tokens", f"{summary['total_output_tokens']:,}")
    table.add_row("Total cost (USD)", f"[bold yellow]${summary['total_cost_usd']:.6f}[/]")
    console.print(table)

    if summary["by_stage"]:
        stage_table = Table(box=box.SIMPLE, show_header=True)
        stage_table.add_column("Stage")
        stage_table.add_column("Calls", justify="right")
        stage_table.add_column("In tokens", justify="right")
        stage_table.add_column("Out tokens", justify="right")
        stage_table.add_column("Cost (USD)", justify="right")
        for stage, vals in summary["by_stage"].items():
            stage_table.add_row(
                stage,
                str(vals["calls"]),
                f"{vals['input_tokens']:,}",
                f"{vals['output_tokens']:,}",
                f"${vals['cost_usd']:.6f}",
            )
        console.print(stage_table)


if __name__ == "__main__":
    main()

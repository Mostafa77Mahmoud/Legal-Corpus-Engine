#!/usr/bin/env python3
"""
Pilot runner — Stages 1 → 1.3 → 1.5

Usage:
    python run_pilot.py [LAW_ID]

LAW_ID defaults to EG_PDPL (Personal Data Protection Law).

Place the PDF at:  data/raw_pdfs/<pdf_filename>
The filename for each law is listed in config/law_registry.py.

Outputs:
    data/extracted_raw/<LAW_ID>.txt                    — raw extracted text
    data/extracted_raw/<LAW_ID>_meta.json              — extraction metadata
    data/extracted_clean/<LAW_ID>.txt                  — cleaned text
    data/cleanup_audit_logs/<LAW_ID>_cleanup_audit.json — cleanup diff log
    data/extracted_raw/<LAW_ID>_confidence.json        — confidence report
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
from pipeline import (
    stage_1_extract,
    stage_1_3_cleanup,
    stage_1_5_val_extract,
    stage_2_split,
    stage_2_5_val_split,
    stage_3_enrich,
    stage_3_7_chunk,
)
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

    pdf_path = RAW_PDFS_DIR / law_entry.pdf_filename

    console.print(Panel.fit(
        f"[bold]Nezam Legal Corpus — Pilot Runner[/]\n"
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

    # ── Stage 1: Extraction ───────────────────────────────────────────────────
    console.rule("[bold blue]Stage 1: Extraction")
    extraction_result = stage_1_extract.run(
        pdf_path=pdf_path,
        law_entry=law_entry,
        cost_tracker=cost_tracker,
    )

    if not extraction_result.success:
        console.print(f"[bold red]Extraction failed:[/] {extraction_result.error}")
        sys.exit(1)

    _print_extraction_report(extraction_result)

    # ── Stage 1.3: Arabic Cleanup ─────────────────────────────────────────────
    console.rule("[bold blue]Stage 1.3: Arabic Cleanup")
    cleanup_audit = stage_1_3_cleanup.run(
        law_entry=law_entry,
        extraction_source=extraction_result.extraction_source,
    )
    _print_cleanup_report(cleanup_audit)

    # ── Stage 1.5: Confidence Scoring ─────────────────────────────────────────
    console.rule("[bold blue]Stage 1.5: Confidence Scoring")
    confidence_report = stage_1_5_val_extract.run(
        law_entry=law_entry,
        extraction_source=extraction_result.extraction_source,
    )

    _print_confidence_report(confidence_report)
    _print_cost_report(cost_tracker)

    if not confidence_report.passed:
        console.print(Panel(
            f"[bold red]Confidence {confidence_report.confidence_score:.4f} is below "
            f"threshold {confidence_report.threshold}.[/]\n"
            f"This law is flagged for [bold]human review[/] before proceeding to Stage 2.",
            title="⚠ Quality Gate Failed",
            border_style="red",
        ))
        sys.exit(2)

    # ── Stage 2: Article Splitting ────────────────────────────────────────────
    console.rule("[bold blue]Stage 2: Article Splitting")
    articles, split_report = stage_2_split.run(law_entry=law_entry)
    _print_split_report(split_report)

    # ── Stage 2.5: Split Validation ───────────────────────────────────────────
    console.rule("[bold blue]Stage 2.5: Split Validation")
    val_report = stage_2_5_val_split.run(
        law_entry=law_entry,
        articles=articles,
        split_report=split_report,
    )
    _print_validation_report(val_report)

    if not val_report.passed:
        console.print(Panel(
            f"[bold red]{val_report.error_count} error(s) found in split output.[/]\n"
            f"Review [yellow]data/split_articles/{law_entry.law_id}/validation_report.json[/] "
            f"before proceeding.",
            title="⚠ Split Validation Failed",
            border_style="red",
        ))
        sys.exit(3)

    # ── Stage 3: Metadata Enrichment ─────────────────────────────────────────
    console.rule("[bold blue]Stage 3: Metadata Enrichment")
    enrich_report = stage_3_enrich.run(
        law_entry=law_entry,
        cost_tracker=cost_tracker,
    )
    _print_enrich_report(enrich_report)
    _print_cost_report(cost_tracker)

    # ── Stage 3.7: Chunking ───────────────────────────────────────────────────
    console.rule("[bold blue]Stage 3.7: Chunking")
    chunk_report = stage_3_7_chunk.run(law_entry=law_entry)
    _print_chunk_report(chunk_report)

    # ── Final summary ─────────────────────────────────────────────────────────
    enrich_status = "green" if enrich_report.failed == 0 else "yellow"
    console.print(Panel.fit(
        f"[bold green]✓ All stages passed.[/]\n"
        f"Confidence: [bold]{confidence_report.confidence_score:.4f}[/]  |  "
        f"Articles: [bold]{split_report.articles_found}[/]  |  "
        f"Enriched: [bold {enrich_status}]{enrich_report.enriched + enrich_report.skipped_cache}[/]  |  "
        f"Chunks: [bold]{chunk_report.total_chunks}[/]  |  "
        f"Cost: [bold yellow]${cost_tracker.summary()['total_cost_usd']:.4f}[/]",
        border_style="green",
    ))


def _print_split_report(report) -> None:
    console.print()
    table = Table(box=box.SIMPLE, show_header=False)
    table.add_column("Field", style="dim", width=30)
    table.add_column("Value")

    match_color = "green" if report.articles_found == report.expected_article_count else "yellow"
    table.add_row("Articles found", f"[{match_color}]{report.articles_found}[/]")
    table.add_row("Expected", str(report.expected_article_count))
    table.add_row("  Issuance articles", str(report.issuance_count))
    table.add_row("  Main articles", str(report.main_count))
    table.add_row("Orphan text (chars)", str(report.orphan_text_chars))
    table.add_row("Marker types found", str(report.marker_kinds))
    table.add_row("Split source", report.split_source)
    console.print(table)


def _print_validation_report(report) -> None:
    console.print()
    passed_label = "[bold green]PASS ✓[/]" if report.passed else "[bold red]FAIL ✗[/]"

    summary = Table(box=box.SIMPLE, show_header=False)
    summary.add_column("", style="dim", width=30)
    summary.add_column("")
    summary.add_row("Articles checked", str(report.articles_checked))
    summary.add_row("Errors", f"[{'red' if report.error_count else 'green'}]{report.error_count}[/]")
    summary.add_row("Warnings", f"[{'yellow' if report.warning_count else 'green'}]{report.warning_count}[/]")
    summary.add_row("Result", passed_label)
    console.print(summary)

    if report.issues:
        console.print()
        issue_table = Table(box=box.SIMPLE, show_header=True)
        issue_table.add_column("Code", style="dim", width=6)
        issue_table.add_column("Severity", width=8)
        issue_table.add_column("Name", width=22)
        issue_table.add_column("Description")
        for issue in report.issues:
            sev_color = "red" if issue.severity == "error" else "yellow"
            issue_table.add_row(
                issue.code,
                f"[{sev_color}]{issue.severity}[/]",
                issue.name,
                issue.description,
            )
        console.print(issue_table)


def _print_extraction_report(result) -> None:
    console.print()
    table = Table(box=box.SIMPLE, show_header=False)
    table.add_column("Field", style="dim", width=30)
    table.add_column("Value")

    source_color = "green" if result.extraction_source in ("pymupdf", "plaintext") else "yellow"
    table.add_row("Extraction source", f"[{source_color}]{result.extraction_source}[/]")
    table.add_row("Characters extracted", f"{result.char_count:,}")
    table.add_row("PDF pages", str(result.page_count))
    table.add_row("Article markers found", str(result.article_markers_found))
    table.add_row("Structural headings found", str(result.structural_headings_found))
    table.add_row("Arabic character density", f"{result.arabic_density:.4f}")
    table.add_row("Replacement char density", f"{result.replacement_density:.6f}")
    if result.extraction_model:
        table.add_row("OCR model", result.extraction_model)
    table.add_row("Output", str(Path(result.raw_text_path).name))

    console.print(table)


def _print_cleanup_report(audit) -> None:
    console.print()
    table = Table(box=box.SIMPLE, show_header=False)
    table.add_column("Transform", style="dim", width=30)
    table.add_column("Count", justify="right")

    pct = round((audit.chars_removed / audit.chars_before * 100), 2) if audit.chars_before else 0
    table.add_row("Characters before", f"{audit.chars_before:,}")
    table.add_row("Characters after", f"{audit.chars_after:,}")
    table.add_row(
        "Characters removed",
        f"[{'yellow' if audit.chars_removed > 0 else 'green'}]{audit.chars_removed:,} ({pct}%)[/]",
    )
    table.add_row("", "")
    table.add_row("NFC changed",         str(audit.nfc_changed))
    table.add_row("Tatweel removed",     str(audit.tatweel_removed))
    table.add_row("Diacritics removed",  str(audit.diacritics_removed))
    table.add_row("Hamza normalised",    str(audit.hamza_normalised))
    table.add_row("Yeh normalised",      str(audit.yeh_normalised))
    table.add_row("Control chars removed", str(audit.control_removed))
    table.add_row("Space runs collapsed", str(audit.spaces_collapsed))
    table.add_row("Newline runs collapsed", str(audit.newlines_collapsed))

    console.print(table)


def _print_confidence_report(report) -> None:
    console.print()
    passed_label = "[bold green]PASS ✓[/]" if report.passed else "[bold red]FAIL ✗[/]"
    review_label = "[bold red]Yes — flagged for human review[/]" if report.manual_review else "[green]No[/]"

    table = Table(box=box.SIMPLE, show_header=True)
    table.add_column("Factor", style="cyan")
    table.add_column("Raw", justify="right")
    table.add_column("Normalized", justify="right")
    table.add_column("Weight", justify="right")
    table.add_column("Contribution", justify="right")

    for factor, vals in report.factor_breakdown.items():
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


def _print_chunk_report(report) -> None:
    console.print()
    table = Table(box=box.SIMPLE, show_header=False)
    table.add_column("Field", style="dim", width=30)
    table.add_column("Value")

    table.add_row("Total articles", str(report.total_articles))
    table.add_row("Total chunks", f"[bold]{report.total_chunks}[/]")
    table.add_row("  Single-chunk articles", str(report.single_chunk_articles))
    table.add_row("  Multi-chunk articles",  str(report.multi_chunk_articles))
    table.add_row("Avg words / chunk", str(report.avg_chunk_words))
    table.add_row("Max words / chunk", str(report.max_chunk_words))
    table.add_row("Min words / chunk", str(report.min_chunk_words))
    console.print(table)


def _print_enrich_report(report) -> None:
    console.print()
    table = Table(box=box.SIMPLE, show_header=False)
    table.add_column("Field", style="dim", width=30)
    table.add_column("Value")

    enriched_ok = report.enriched + report.skipped_cache
    fail_color = "red" if report.failed else "green"
    table.add_row("Total articles", str(report.total_articles))
    table.add_row("  Newly enriched", str(report.enriched))
    table.add_row("  Loaded from cache", str(report.skipped_cache))
    table.add_row("  Failed", f"[{fail_color}]{report.failed}[/]")
    table.add_row("Coverage", f"[bold]{enriched_ok}/{report.total_articles}[/]")
    table.add_row("Model", report.model)
    table.add_row("Stage 3 cost (USD)", f"${report.total_cost_usd:.6f}")
    console.print(table)


def _print_cost_report(tracker: CostTracker) -> None:
    console.print()
    console.print(Panel.fit("[bold]Cost Report", border_style="dim"))
    summary = tracker.summary()

    if summary["total_api_calls"] == 0:
        console.print("  [green]$0.00[/] — no Gemini API calls made.")
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

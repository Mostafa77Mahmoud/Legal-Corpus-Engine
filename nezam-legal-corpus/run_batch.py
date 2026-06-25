#!/usr/bin/env python3
"""
Batch runner — executes the full pipeline (Stages 1→4) on multiple laws.

Usage:
    python run_batch.py                     # run all laws in BATCH_LAWS
    python run_batch.py EG_PDPL EG_CIVIL_CODE  # run specific laws

Laws are processed sequentially.  Per-law results and a combined cost
summary are printed at the end.
"""

import argparse
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table

from config.law_registry import LAW_REGISTRY, get_law
from config.settings import RAW_PDFS_DIR, RAW_TXTS_DIR
from pipeline import (
    stage_1_extract,
    stage_1_3_cleanup,
    stage_1_5_val_extract,
    stage_2_split,
    stage_2_5_val_split,
    stage_3_enrich,
    stage_3_7_chunk,
    stage_4_human_review,
    stage_5_validate,
    stage_6_assemble,
    stage_7_export,
)
from utils.cost_tracker import CostTracker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)
console = Console()

# ── Default batch: laws with PDFs present in data/raw_pdfs/ ──────────────────
BATCH_LAWS = ["EG_PDPL", "EG_ESIGN", "EG_CIVIL_CODE"]

# ── Valid stage keys ──────────────────────────────────────────────────────────
_ALL_STAGES = ["1", "1.3", "1.5", "2", "2.5", "3", "3.7", "4", "5", "6", "7"]


# ── Per-law result dataclass ──────────────────────────────────────────────────

@dataclass
class LawResult:
    law_id: str
    law_name_ar: str
    status: str              # "ok" | "fail" | "skipped"
    fail_stage: str | None
    fail_reason: str | None
    extraction_source: str | None
    confidence: float | None
    articles_found: int | None
    chunks: int | None
    cost_usd: float
    presentation_forms_normalized: int


# ── Helpers ───────────────────────────────────────────────────────────────────

def _source_exists(law_id: str) -> bool:
    entry = LAW_REGISTRY.get(law_id)
    if not entry:
        return False
    if entry.txt_filename:
        return (RAW_TXTS_DIR / entry.txt_filename).exists()
    return (RAW_PDFS_DIR / entry.pdf_filename).exists()


# ── Single-law runner ─────────────────────────────────────────────────────────

def run_law(
    law_id: str,
    cost_tracker: CostTracker,
    stages: set[str],
    force_reenrich: bool = False,
) -> LawResult:
    """
    Run the pipeline for *law_id*, executing only the stages listed in *stages*.

    When starting from a later stage (e.g. "3"), prior outputs are loaded from
    disk rather than re-computed.  Variables that would have been produced by
    skipped stages are initialised with sensible sentinel defaults so the rest
    of the runner can reference them without NameError.
    """
    law_entry = get_law(law_id)
    law_cost_before = cost_tracker.summary()["total_cost_usd"]

    pdf_path    = RAW_PDFS_DIR / law_entry.pdf_filename
    source_path = (
        RAW_TXTS_DIR / law_entry.txt_filename
        if law_entry.txt_filename
        else pdf_path
    )

    console.print(Rule(
        f"[bold cyan]{law_entry.law_name_ar}[/]  [dim]({law_id})[/]",
        style="blue",
    ))

    # ── Sentinel defaults (filled in if the stage is actually run) ────────────
    ext            = None   # ExtractionResult
    conf           = None   # ConfidenceResult — populated by Stage 1.5 or loaded
    articles       = None   # list[dict]        — populated by Stage 2 or loaded
    split          = None   # SplitReport
    chunk_report   = None   # ChunkReport
    pres_normalized = 0
    extraction_source = "unknown"

    # ── Source-file check only when Stage 1 is requested ─────────────────────
    if "1" in stages and not source_path.exists():
        console.print(f"  [red]✗ مصدر الملف غير موجود: {source_path}[/]")
        return LawResult(
            law_id=law_id, law_name_ar=law_entry.law_name_ar,
            status="skipped", fail_stage="source", fail_reason=f"File missing: {source_path}",
            extraction_source=None, confidence=None, articles_found=None,
            chunks=None, cost_usd=0.0, presentation_forms_normalized=0,
        )

    # Stage 1 ─────────────────────────────────────────────────────────────────
    if "1" in stages:
        try:
            ext = stage_1_extract.run(pdf_path=pdf_path, law_entry=law_entry, cost_tracker=cost_tracker)
            if not ext.success:
                raise RuntimeError(ext.error or "extraction failed")
            extraction_source = ext.extraction_source
            console.print(
                f"  [green]✓[/] Stage 1  — {ext.extraction_source}  "
                f"{ext.char_count:,} chars  {ext.page_count}p  "
                f"arabic={ext.arabic_density:.2%}"
            )
        except Exception as exc:
            console.print(f"  [red]✗ Stage 1 failed: {exc}[/]")
            return LawResult(
                law_id=law_id, law_name_ar=law_entry.law_name_ar,
                status="fail", fail_stage="stage_1", fail_reason=str(exc),
                extraction_source=None, confidence=None, articles_found=None,
                chunks=None,
                cost_usd=cost_tracker.summary()["total_cost_usd"] - law_cost_before,
                presentation_forms_normalized=0,
            )
    else:
        console.print(f"  [dim]— Stage 1  skipped[/]")

    # Stage 1.3 ───────────────────────────────────────────────────────────────
    if "1.3" in stages:
        try:
            audit = stage_1_3_cleanup.run(
                law_entry=law_entry,
                extraction_source=extraction_source,
            )
            pres_normalized = audit.presentation_forms_normalized
            console.print(
                f"  [green]✓[/] Stage 1.3 — cleanup  "
                f"{audit.chars_before:,} → {audit.chars_after:,} chars"
                + (f"  [yellow]NFKC={pres_normalized:,}[/]" if pres_normalized else "")
            )
        except Exception as exc:
            console.print(f"  [red]✗ Stage 1.3 failed: {exc}[/]")
            return LawResult(
                law_id=law_id, law_name_ar=law_entry.law_name_ar,
                status="fail", fail_stage="stage_1_3", fail_reason=str(exc),
                extraction_source=extraction_source, confidence=None,
                articles_found=None, chunks=None,
                cost_usd=cost_tracker.summary()["total_cost_usd"] - law_cost_before,
                presentation_forms_normalized=pres_normalized,
            )
    else:
        console.print(f"  [dim]— Stage 1.3 skipped[/]")

    # Stage 1.5 ───────────────────────────────────────────────────────────────
    if "1.5" in stages:
        try:
            conf = stage_1_5_val_extract.run(
                law_entry=law_entry, extraction_source=extraction_source
            )
            status_sym = "[green]✓[/]" if conf.passed else "[red]✗[/]"
            console.print(
                f"  {status_sym} Stage 1.5 — confidence {conf.confidence_score:.4f}  "
                f"({'PASS' if conf.passed else 'FAIL'})"
            )
            if not conf.passed:
                return LawResult(
                    law_id=law_id, law_name_ar=law_entry.law_name_ar,
                    status="fail", fail_stage="stage_1_5",
                    fail_reason=f"Low confidence: {conf.confidence_score:.4f}",
                    extraction_source=extraction_source,
                    confidence=conf.confidence_score,
                    articles_found=None, chunks=None,
                    cost_usd=cost_tracker.summary()["total_cost_usd"] - law_cost_before,
                    presentation_forms_normalized=pres_normalized,
                )
        except Exception as exc:
            console.print(f"  [red]✗ Stage 1.5 failed: {exc}[/]")
            return LawResult(
                law_id=law_id, law_name_ar=law_entry.law_name_ar,
                status="fail", fail_stage="stage_1_5", fail_reason=str(exc),
                extraction_source=extraction_source, confidence=None,
                articles_found=None, chunks=None,
                cost_usd=cost_tracker.summary()["total_cost_usd"] - law_cost_before,
                presentation_forms_normalized=pres_normalized,
            )
    else:
        console.print(f"  [dim]— Stage 1.5 skipped[/]")

    # Stage 2 ─────────────────────────────────────────────────────────────────
    if "2" in stages:
        try:
            articles, split = stage_2_split.run(law_entry=law_entry)
            console.print(
                f"  [green]✓[/] Stage 2  — {split.articles_found} مواد  "
                f"(متوقع: {split.expected_article_count})"
            )
        except Exception as exc:
            console.print(f"  [red]✗ Stage 2 failed: {exc}[/]")
            return LawResult(
                law_id=law_id, law_name_ar=law_entry.law_name_ar,
                status="fail", fail_stage="stage_2", fail_reason=str(exc),
                extraction_source=extraction_source,
                confidence=conf.confidence_score if conf else None,
                articles_found=None, chunks=None,
                cost_usd=cost_tracker.summary()["total_cost_usd"] - law_cost_before,
                presentation_forms_normalized=pres_normalized,
            )
    else:
        console.print(f"  [dim]— Stage 2  skipped[/]")
        # Load split articles from disk for later stages
        from config.settings import SPLIT_ARTICLES_DIR
        import json as _json
        _sp = SPLIT_ARTICLES_DIR / law_id / "articles.json"
        if _sp.exists():
            articles = _json.loads(_sp.read_text(encoding="utf-8"))

    # Stage 2.5 ───────────────────────────────────────────────────────────────
    if "2.5" in stages:
        if articles is None or split is None:
            console.print(f"  [yellow]⚠ Stage 2.5 skipped — no split data (run Stage 2 first)[/]")
        else:
            try:
                val = stage_2_5_val_split.run(
                    law_entry=law_entry, articles=articles, split_report=split
                )
                status_sym = "[green]✓[/]" if val.passed else "[yellow]⚠[/]"
                console.print(
                    f"  {status_sym} Stage 2.5 — validation  "
                    f"errors={val.error_count}  warnings={val.warning_count}  "
                    f"({'PASS' if val.passed else 'WARN'})"
                )
                if not val.passed:
                    console.print(f"  [red]  Stage 2.5 FAIL — {val.error_count} error(s)[/]")
                    return LawResult(
                        law_id=law_id, law_name_ar=law_entry.law_name_ar,
                        status="fail", fail_stage="stage_2_5",
                        fail_reason=f"{val.error_count} validation errors",
                        extraction_source=extraction_source,
                        confidence=conf.confidence_score if conf else None,
                        articles_found=split.articles_found, chunks=None,
                        cost_usd=cost_tracker.summary()["total_cost_usd"] - law_cost_before,
                        presentation_forms_normalized=pres_normalized,
                    )
            except Exception as exc:
                console.print(f"  [red]✗ Stage 2.5 failed: {exc}[/]")
                return LawResult(
                    law_id=law_id, law_name_ar=law_entry.law_name_ar,
                    status="fail", fail_stage="stage_2_5", fail_reason=str(exc),
                    extraction_source=extraction_source,
                    confidence=conf.confidence_score if conf else None,
                    articles_found=split.articles_found if split else None, chunks=None,
                    cost_usd=cost_tracker.summary()["total_cost_usd"] - law_cost_before,
                    presentation_forms_normalized=pres_normalized,
                )
    else:
        console.print(f"  [dim]— Stage 2.5 skipped[/]")

    # Stage 3 ─────────────────────────────────────────────────────────────────
    if "3" in stages:
        try:
            enrich = stage_3_enrich.run(
                law_entry=law_entry,
                cost_tracker=cost_tracker,
                force_reenrich=force_reenrich,
            )
            fail_color = "red" if enrich.failed else "green"
            console.print(
                f"  [green]✓[/] Stage 3  — enrichment  "
                f"{enrich.enriched + enrich.skipped_cache}/{enrich.total_articles}  "
                f"calls={enrich.total_api_calls}  "
                f"[{fail_color}]fail={enrich.failed}[/]"
            )
        except Exception as exc:
            console.print(f"  [red]✗ Stage 3 failed: {exc}[/]")
            return LawResult(
                law_id=law_id, law_name_ar=law_entry.law_name_ar,
                status="fail", fail_stage="stage_3", fail_reason=str(exc),
                extraction_source=extraction_source,
                confidence=conf.confidence_score if conf else None,
                articles_found=len(articles) if articles else None, chunks=None,
                cost_usd=cost_tracker.summary()["total_cost_usd"] - law_cost_before,
                presentation_forms_normalized=pres_normalized,
            )
    else:
        console.print(f"  [dim]— Stage 3  skipped[/]")

    # Stage 3.7 ───────────────────────────────────────────────────────────────
    if "3.7" in stages:
        try:
            chunk_report = stage_3_7_chunk.run(
                law_entry=law_entry, cost_tracker=cost_tracker
            )
            console.print(
                f"  [green]✓[/] Stage 3.7 — chunks  "
                f"{chunk_report.total_articles} مواد → {chunk_report.total_chunks} chunks  "
                f"avg={chunk_report.avg_chunk_words:.0f}w"
            )
        except Exception as exc:
            console.print(f"  [red]✗ Stage 3.7 failed: {exc}[/]")
            return LawResult(
                law_id=law_id, law_name_ar=law_entry.law_name_ar,
                status="fail", fail_stage="stage_3_7", fail_reason=str(exc),
                extraction_source=extraction_source,
                confidence=conf.confidence_score if conf else None,
                articles_found=len(articles) if articles else None, chunks=None,
                cost_usd=cost_tracker.summary()["total_cost_usd"] - law_cost_before,
                presentation_forms_normalized=pres_normalized,
            )
    else:
        console.print(f"  [dim]— Stage 3.7 skipped[/]")

    # Stage 4 ─────────────────────────────────────────────────────────────────
    if "4" in stages:
        try:
            review = stage_4_human_review.run(law_entry=law_entry)
            console.print(
                f"  [green]✓[/] Stage 4  — review files → {Path(review.output_dir).name}/"
            )
        except Exception as exc:
            console.print(f"  [yellow]⚠ Stage 4 failed: {exc}[/]")
    else:
        console.print(f"  [dim]— Stage 4  skipped[/]")

    # Stage 5 ─────────────────────────────────────────────────────────────────
    if "5" in stages:
        try:
            valid = stage_5_validate.run(law_entry=law_entry)
            status_sym = "[green]✓[/]" if valid.passed else "[red]✗[/]"
            console.print(
                f"  {status_sym} Stage 5  — validation  "
                f"errors={valid.error_count}  warnings={valid.warning_count}  "
                f"({'PASS' if valid.passed else 'FAIL'})"
            )
            if not valid.passed:
                return LawResult(
                    law_id=law_id, law_name_ar=law_entry.law_name_ar,
                    status="fail", fail_stage="stage_5",
                    fail_reason=f"{valid.error_count} validation errors",
                    extraction_source=extraction_source,
                    confidence=conf.confidence_score if conf else None,
                    articles_found=len(articles) if articles else None,
                    chunks=chunk_report.total_chunks if chunk_report else None,
                    cost_usd=cost_tracker.summary()["total_cost_usd"] - law_cost_before,
                    presentation_forms_normalized=pres_normalized,
                )
        except Exception as exc:
            console.print(f"  [yellow]⚠ Stage 5 failed: {exc}[/]")
    else:
        console.print(f"  [dim]— Stage 5  skipped[/]")

    # Stage 6 ─────────────────────────────────────────────────────────────────
    if "6" in stages:
        try:
            assembly = stage_6_assemble.run(law_entry=law_entry)
            console.print(
                f"  [green]✓[/] Stage 6  — assembly  "
                f"{assembly.total_articles_out} articles  {assembly.total_chunks_out} chunks"
            )
        except Exception as exc:
            console.print(f"  [yellow]⚠ Stage 6 failed: {exc}[/]")
    else:
        console.print(f"  [dim]— Stage 6  skipped[/]")

    # Stage 7 ─────────────────────────────────────────────────────────────────
    if "7" in stages:
        try:
            export = stage_7_export.run(law_entry=law_entry)
            mongo_note = "  [dim](+MongoDB)[/]" if export.mongodb_exported else ""
            console.print(
                f"  [green]✓[/] Stage 7  — export → releases/{law_id}/"
                + mongo_note
            )
        except Exception as exc:
            console.print(f"  [yellow]⚠ Stage 7 failed: {exc}[/]")
    else:
        console.print(f"  [dim]— Stage 7  skipped[/]")

    law_cost = cost_tracker.summary()["total_cost_usd"] - law_cost_before
    articles_found = (
        split.articles_found if split is not None
        else len(articles) if articles is not None
        else None
    )
    chunks_found = chunk_report.total_chunks if chunk_report is not None else None
    confidence_val = conf.confidence_score if conf is not None else None

    return LawResult(
        law_id=law_id, law_name_ar=law_entry.law_name_ar,
        status="ok", fail_stage=None, fail_reason=None,
        extraction_source=extraction_source,
        confidence=confidence_val,
        articles_found=articles_found,
        chunks=chunks_found,
        cost_usd=law_cost,
        presentation_forms_normalized=pres_normalized,
    )


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Nezam Legal Corpus — Batch Runner",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "law_ids",
        nargs="*",
        metavar="LAW_ID",
        help="Law IDs to process (default: all laws with source files present)",
    )
    parser.add_argument(
        "--stages",
        nargs="+",
        metavar="STAGE",
        default=_ALL_STAGES,
        help=(
            "Stages to run (default: all).\n"
            "Valid values: 1  1.3  1.5  2  2.5  3  3.7  4  5  6  7\n"
            "Example: --stages 3 4 5 6 7"
        ),
    )
    parser.add_argument(
        "--force-reenrich",
        action="store_true",
        default=False,
        help="Ignore Stage 3 cache and re-enrich all articles (useful after schema changes)",
    )
    args = parser.parse_args()

    law_ids: list[str] = args.law_ids or [
        lid for lid in BATCH_LAWS if _source_exists(lid)
    ]
    if not law_ids:
        console.print("[red]No source files found for default batch.[/]")
        sys.exit(1)

    stages: set[str] = set(args.stages)
    invalid_stages = stages - set(_ALL_STAGES)
    if invalid_stages:
        console.print(f"[red]Unknown stage(s): {sorted(invalid_stages)}[/]")
        console.print(f"Valid stages: {_ALL_STAGES}")
        sys.exit(1)

    console.print(Panel.fit(
        f"[bold]Nezam Legal Corpus — Batch Runner[/]\n"
        f"Laws: [cyan]{', '.join(law_ids)}[/]",
        border_style="blue",
    ))

    cost_tracker = CostTracker()
    results: list[LawResult] = []

    for law_id in law_ids:
        if law_id not in LAW_REGISTRY:
            console.print(f"[red]Unknown law ID: {law_id}[/]")
            continue
        result = run_law(
            law_id, cost_tracker,
            stages=stages,
            force_reenrich=args.force_reenrich,
        )
        results.append(result)
        console.print()

    # ── Combined summary ──────────────────────────────────────────────────────
    console.print(Rule("[bold]نتائج الـ Batch[/]", style="green"))

    summary_table = Table(box=box.ROUNDED, show_header=True, border_style="dim")
    summary_table.add_column("القانون",         style="cyan",  width=30)
    summary_table.add_column("المصدر",          style="dim",   width=10)
    summary_table.add_column("Confidence",      justify="right", width=11)
    summary_table.add_column("المواد",          justify="right", width=8)
    summary_table.add_column("Chunks",          justify="right", width=8)
    summary_table.add_column("NFKC",            justify="right", width=8)
    summary_table.add_column("التكلفة (USD)",   justify="right", width=14)
    summary_table.add_column("الحالة",          width=10)

    total_articles = 0
    total_chunks   = 0
    total_cost     = 0.0
    ok_count       = 0

    for r in results:
        status_str = (
            "[bold green]✓ OK[/]"   if r.status == "ok" else
            "[bold red]✗ FAIL[/]"   if r.status == "fail" else
            "[dim]SKIP[/]"
        )
        conf_str = f"{r.confidence:.4f}" if r.confidence is not None else "—"
        arts_str = str(r.articles_found) if r.articles_found is not None else "—"
        chk_str  = str(r.chunks)         if r.chunks  is not None else "—"
        nfkc_str = f"{r.presentation_forms_normalized:,}" if r.presentation_forms_normalized else "—"
        cost_str = f"${r.cost_usd:.4f}"

        summary_table.add_row(
            r.law_name_ar, r.extraction_source or "—",
            conf_str, arts_str, chk_str, nfkc_str, cost_str, status_str,
        )

        if r.status == "ok":
            ok_count     += 1
            total_articles += r.articles_found or 0
            total_chunks   += r.chunks or 0
        total_cost += r.cost_usd

    summary_table.add_section()
    summary_table.add_row(
        "[bold]المجموع[/]", "", "", f"[bold]{total_articles}[/]",
        f"[bold]{total_chunks}[/]", "", f"[bold yellow]${total_cost:.4f}[/]",
        f"[bold]{ok_count}/{len(results)}[/]",
    )
    console.print(summary_table)

    # Failures detail
    failures = [r for r in results if r.status == "fail"]
    if failures:
        console.print()
        for r in failures:
            console.print(
                f"  [red]✗ {r.law_id}[/] failed at "
                f"[yellow]{r.fail_stage}[/]: {r.fail_reason}"
            )

    # Overall cost breakdown
    console.print()
    overall = cost_tracker.summary()
    if overall["total_api_calls"] > 0:
        cost_table = Table(box=box.SIMPLE, show_header=True, border_style="dim")
        cost_table.add_column("Stage")
        cost_table.add_column("Calls",      justify="right")
        cost_table.add_column("In tokens",  justify="right")
        cost_table.add_column("Out tokens", justify="right")
        cost_table.add_column("Cost (USD)", justify="right")
        for stage, vals in overall["by_stage"].items():
            cost_table.add_row(
                stage,
                str(vals["calls"]),
                f"{vals['input_tokens']:,}",
                f"{vals['output_tokens']:,}",
                f"${vals['cost_usd']:.6f}",
            )
        console.print(cost_table)
        console.print(f"\n  [bold yellow]Total: ${overall['total_cost_usd']:.6f}[/]  "
                      f"({overall['total_input_tokens']:,} in + "
                      f"{overall['total_output_tokens']:,} out tokens)\n")
    else:
        console.print("  [green]$0.00[/] — no Gemini API calls made (all cached).\n")

    sys.exit(0 if ok_count == len(results) else 1)


if __name__ == "__main__":
    main()

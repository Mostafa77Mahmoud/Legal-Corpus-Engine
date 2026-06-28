# Pipeline Checkpoint System — Design

## Problem

Currently, pipeline progress is inferred from cached files:

```python
# What we do now — fragile:
if enriched_articles/EG_CIVIL_CODE.json has 300 entries:
    assume stage_3 is 300/1039 done

# What can go wrong:
# - File exists but entries have enrichment_status="" (killed mid-batch)
# - File missing entirely (stage never ran vs stage is pending)
# - No way to know which stages ran vs were skipped
```

## Proposed File Layout

```
nezam-legal-corpus/
  data/
    pipeline_state/
      EG_ESIGN.json
      EG_PDPL.json
      EG_CIVIL_CODE.json
      EG_ESIGN_LAW.json
      ...
```

## Schema: `pipeline_state/{LAW_ID}.json`

```json
{
  "law_id": "EG_CIVIL_CODE",
  "schema_version": "1.0",
  "updated_at": "2026-06-26T12:13:00Z",

  "stages": {

    "stage_1": {
      "status": "completed",
      "completed_at": "2026-06-10T09:00:00Z",
      "notes": null
    },

    "stage_1_3": {
      "status": "completed",
      "completed_at": "2026-06-10T09:01:00Z",
      "notes": null
    },

    "stage_1_5": {
      "status": "skipped",
      "reason": "no_pdf_available",
      "notes": null
    },

    "stage_2": {
      "status": "completed",
      "completed_at": "2026-06-10T09:05:00Z",
      "notes": null
    },

    "stage_2_5": {
      "status": "completed",
      "completed_at": "2026-06-10T09:06:00Z",
      "notes": null
    },

    "stage_3": {
      "status": "partial",
      "started_at": "2026-06-26T11:29:40Z",
      "last_activity_at": "2026-06-26T12:11:07Z",
      "completed_articles": 300,
      "total_articles": 1039,
      "remaining_articles": 739,
      "last_completed_article_id": "EG_CIVIL_CODE_337",
      "last_completed_article_num": 337,
      "failed_articles": [],
      "total_api_calls": 4,
      "total_cost_usd": 0.1520
    },

    "stage_4":  { "status": "pending" },
    "stage_5":  { "status": "pending" },
    "stage_6":  { "status": "pending" },
    "stage_7":  { "status": "pending" }
  },

  "totals": {
    "total_api_calls": 4,
    "total_cost_usd":  0.1520,
    "extraction_source": "txt",
    "confidence_score": null
  }
}
```

## Status State Machine

```
                 ┌─────────────────────────────┐
                 │          pending             │  (never started)
                 └──────────────┬──────────────┘
                                │  stage begins
                                ▼
                 ┌─────────────────────────────┐
                 │          running             │  (in progress now)
                 └──┬──────────────┬───────────┘
                    │ success      │ crash / quota kill
                    ▼              ▼
          ┌──────────────┐  ┌──────────────┐
          │  completed   │  │   partial    │  (resume point)
          └──────────────┘  └──────────────┘
                                 │  re-run
                                 └──────────► running → completed

Special:  skipped  — stage was explicitly not run (e.g. no PDF for stage_1_5)
          failed   — stage ran but produced an unrecoverable error
```

## Resume Logic in `run_batch.py`

```python
def should_run_stage(state: PipelineState, stage: str, force: bool) -> bool:
    status = state.stages[stage]["status"]
    if force:
        return True
    return status in ("pending", "partial", "running", "failed")
    # "running" treated as interrupted → always resume

def get_resume_article(state: PipelineState) -> str | None:
    """For stage_3 partial: return last_completed_article_id so
    stage_3 skips already-enriched articles."""
    s3 = state.stages.get("stage_3", {})
    if s3.get("status") == "partial":
        return s3.get("last_completed_article_id")
    return None
```

## Write Points

| Event | What is written |
|---|---|
| Stage begins | `status: "running"`, `started_at` |
| Stage 3 — after each batch | `completed_articles`, `last_completed_article_id`, `total_cost_usd` |
| Stage 3 — after each intra-batch checkpoint | same fields (granular) |
| Stage ends successfully | `status: "completed"`, `completed_at` |
| Stage skipped | `status: "skipped"`, `reason` |
| Stage errors | `status: "failed"`, error message |

## Relation to Existing Cache

```
enriched_articles/{LAW_ID}/articles.json
  → answers: "Is THIS article's data complete and trustworthy?"
  → field: enrichment_status = "completed" | "failed" | ""

pipeline_state/{LAW_ID}.json
  → answers: "Where is the pipeline as a whole? Which stage? How many done?"
  → field: stage_3.status = "partial", completed_articles = 300

They are complementary, not redundant.
The checkpoint never replaces the article cache — it indexes it.
```

## Implementation Plan (when ready)

1. Add `PipelineState` dataclass + `load_state()` / `save_state()` helpers in
   a new `pipeline/checkpoint.py` module.
2. Modify `run_batch.py` to read state before running each stage, and write
   state after each stage completes.
3. Modify `stage_3_enrich.py` to write `last_completed_article_id` inside the
   intra-batch checkpoint save (already triggered by `enrichment_status="completed"`).
4. Add `--resume` flag to `run_batch.py` that reads state and only runs
   stages that are `pending | partial | running | failed`.

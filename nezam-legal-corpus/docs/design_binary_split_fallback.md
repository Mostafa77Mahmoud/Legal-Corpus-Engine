# Binary Split Fallback — Algorithm Design

## Problem

Current fallback strategy when a batch JSON parse fails:

```
Batch(150) → Parse Error → 150 × Single(1)  = 150 API calls
```

If only 1 article caused the parse failure, 149 perfectly-good articles still get
re-called individually. This is the reason quota was exhausted during EG_CIVIL_CODE
Batch 3: a single malformed article triggered 150 fallback calls.

## Proposed Algorithm

```python
def enrich_with_hierarchical_fallback(
    batch: list[dict],
    law_entry: LawEntry,
    cost_tracker: CostTracker,
    model: str,
    min_batch: int = 1,
) -> dict[str, ArticleMetadata]:
    """
    Try to enrich `batch` in one API call.
    On JSON parse failure, binary-split and recurse.
    On quota error, re-raise immediately (do not recurse).
    """
    if not batch:
        return {}

    # ── Quota / 503 errors: never split, always re-raise ──────────────────────
    # Splitting won't help if the model itself is unavailable.
    try:
        results = _enrich_batch(batch, law_entry, cost_tracker, model)
    except (QuotaExhaustedError, TransientError):
        raise                              # let caller handle key rotation / retry

    # ── Full success ───────────────────────────────────────────────────────────
    if len(results) == len(batch):
        return results

    # ── Partial success or JSONParseError: binary split ───────────────────────
    missing = [a for a in batch if a["article_id"] not in results]

    if len(missing) <= min_batch:
        # Leaf: single-article call
        meta = _enrich_single(missing[0], law_entry, cost_tracker, model)
        return {**results, missing[0]["article_id"]: meta}

    mid   = len(missing) // 2
    left  = enrich_with_hierarchical_fallback(missing[:mid], law_entry, cost_tracker, model, min_batch)
    right = enrich_with_hierarchical_fallback(missing[mid:], law_entry, cost_tracker, model, min_batch)
    return {**results, **left, **right}
```

## Recursion Tree — Visualised

```
Batch(150) — parse OK for 149, 1 failure
    └── Batch(1) — single call  [TOTAL: 2 calls]

Batch(150) — parse fails entirely
    ├── Batch(75) — OK            [1 call]
    └── Batch(75) — fails
        ├── Batch(37) — OK        [1 call]
        └── Batch(38) — fails
            ├── Batch(19) — OK    [1 call]
            └── Batch(19) — fails
                ├── ...
                └── Single(1)     [1 call]
    TOTAL worst case: log₂(150) ≈ 8 calls per failing sub-tree
```

## Complexity Analysis

| Scenario | Failing articles (f) | Current calls | Binary calls | Speedup |
|---|---|---|---|---|
| 1 bad article | f = 1 | 150 | ~8 | **~19×** |
| 10 bad articles | f = 10 | 150 | ~80 | **~2×** |
| All bad articles | f = 150 | 150 | ≤ 300 | 0.5× (overhead) |
| Perfect batch | f = 0 | 1 | 1 | 1× |

**General formula:**

```
Binary Split:  T(n, f) = O(f · log n)   where f = failing articles
Current flat:  T(n, f) = O(n)           always
```

**Break-even point:** `f · log₂(n) = n` → `f = n / log₂(n)` ≈ 21 for n=150

Binary split wins whenever fewer than ~14% of articles in a batch fail.
This matches the real-world distribution: parse errors are almost always caused
by 1–3 articles with unusual characters, not by the whole batch.

## Key Design Rules

1. **Never binary-split on quota/503** — splitting won't fix model unavailability.
   Re-raise immediately and let the outer retry/rotation logic handle it.

2. **`min_batch` should stay at 1** — the leaf must always be a single-article call
   so the algorithm is guaranteed to terminate.

3. **Intra-split checkpoint** — after each leaf succeeds, write to the crash-safe
   checkpoint file (already implemented via `enrichment_status="completed"` saves).

4. **Partial batch success** — if the API returns 140/150 articles (valid JSON but
   incomplete), treat the 10 missing as a sub-batch to recurse on, not as a parse error.

## Implementation Note

Replace the `except Exception` catch at line ~609 in `stage_3_enrich.py` with this
recursive call. The main batch loop no longer needs the flat `for art in batch: _enrich_single(...)` fallback.

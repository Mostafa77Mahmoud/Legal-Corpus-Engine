# Nezam Legal Corpus

An automated pipeline that turns raw PDF/TXT sources of Egyptian laws into a structured, machine-readable legal corpus (article splitting, metadata enrichment, chunking, export to MongoDB/JSON) to power Arabic legal AI applications, alongside a small Node/TypeScript workspace (API server + component preview sandbox).

## Run & Operate

- `pnpm --filter @workspace/api-server run dev` — Express API server, port 8080 (workflow: "artifacts/api-server: API Server", managed by `artifacts/api-server/.replit-artifact/artifact.toml`); health check at `/api/healthz`
- `pnpm --filter @workspace/mockup-sandbox run dev` — component/canvas preview server, port 8081 (workflow: "artifacts/mockup-sandbox: Component Preview Server", managed by `artifacts/mockup-sandbox/.replit-artifact/artifact.toml`); currently just a scaffold, not an end-user UI
- `cd nezam-legal-corpus && PRIMARY_MODEL=gemini-2.5-flash python run_batch.py <LAW_ID> --stages 1 1.3 1.5 2 2.5 3 3.7 4 5 6 7` — run the legal-corpus pipeline for one law; this is a manual batch job, not a server, so the "Nezam Pipeline" workflow just prints the command rather than auto-running it
- `pnpm run typecheck` — typecheck across all Node packages
- `pnpm run build` — typecheck + build all packages
- Python deps are managed via `pyproject.toml` / `uv.lock` (run `uv sync` after pulling changes); Node deps via `pnpm install`
- Env: `GEMINI_API_KEYS` secret is configured (Nezam pipeline, Gemini calls); `PRIMARY_MODEL` defaults to `gemini-3.5-flash` via `[userenv.shared]` in `.replit`, override per-run

## Stack

- Node workspace: pnpm workspaces, Node.js 24, TypeScript 5.9, Express 5 API server, Vite-based component preview sandbox
- Python pipeline (`nezam-legal-corpus/`): Python 3.11, PyMuPDF, Google Gemini (`google-genai`/`google-generativeai`), rich (CLI tables), pytest

## Where things live

- `artifacts/api-server` — Express API (routes under `src/routes`, currently just a health check)
- `artifacts/mockup-sandbox` — Vite component/canvas preview server
- `nezam-legal-corpus/` — the legal corpus pipeline: `pipeline/` (stage scripts), `config/` (law registry, settings, taxonomy), `data/` (raw PDFs + pipeline outputs), `docs/` (stage-by-stage design docs, see `docs/00_PROJECT_OVERVIEW.md`), `run_batch.py` / `run_pilot.py` (entry points)

## Architecture decisions

- Nezam pipeline is a 9-stage process (Stage 1 → 7, see `docs/00_PROJECT_OVERVIEW.md`): extraction → Arabic cleanup → confidence scoring → article splitting → split validation → metadata enrichment → chunking → human review export → rule validation → assembly → export (Mongo + JSON).
- The Node "Frontend" workflow currently serves only the component preview sandbox — there is no built end-user web app yet in this repo.

## Product

- Nezam pipeline: ingests Egyptian law PDFs/TXT and produces structured article + chunk records for legal AI (retrieval, contract analysis, compliance checking).
- Node workspace: early-stage scaffold (API server with only a health route; no product UI yet).

## User preferences

_Populate as you build — explicit user instructions worth remembering across sessions._

## Gotchas

- `nezam-legal-corpus/data/raw_pdfs/` (source PDFs) is not committed to the repo (likely size/copyright), so on a fresh clone/import the "Nezam Pipeline" workflow's default law (`EG_RENT_1969`) will report FAILED — it can't find its source PDF even though earlier pipeline stages are already cached in `data/releases/EG_RENT_1969/`. To re-run it, add the PDF back to `data/raw_pdfs/EG_RENT_1969.pdf`, or edit the workflow command to target a different law ID.
- After importing/cloning, run both `pnpm install` (Node) and `uv sync` (Python) — the repo mixes both toolchains.

## Pointers

- See the `pnpm-workspace` skill for workspace structure, TypeScript setup, and package details

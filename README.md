# Atlas Research

Atlas turns a company name into an institutional-style research report: audited statements, a calculation
ledger, red flags, news, sentiment, peers, contradictions and a written verdict. Every number in the prose is
copied from a ledger row computed in Python `Decimal` code and carries a `[[citation]]` token; a verification
gate removes any sentence whose number cannot be traced. The LLM (Gemini, structured JSON) only chooses, maps
coordinates, classifies and writes; it never produces a figure.

It is a single container: FastAPI + Uvicorn serves the API, the Server-Sent Events stream and the built React UI.

## Quick start (replay mode, no keys needed)

```bash
uv sync --dev
(cd frontend && npm ci && npm run build)
uv run uvicorn backend.main:app --port 8080
# open http://localhost:8080
```

Replays stream a recorded run through the same client code a live run uses. The repository ships four fixtures:

* `sample_synthetic`: a **fictional** company used for development, shown with a "synthetic data" banner.
* `paytm_nse_seed`, `pine_labs_nse_seed`, `block_nyse_seed`: **partial** replays built from the seed figures in the
  build specification (Section 11.2), with no network access. They carry an "unconfirmed seed figures" banner (in
  the UI and in exports) because no figure has yet been checked against a filing page. They contain only revenue,
  profit and (for Block) cash-flow seeds; every other section is a stated gap. Replace each with the verified
  `<id>.yaml` build (below) once the figures are copied from the filings.

For frontend hot reload run `npm run dev` in `frontend/`; Vite proxies `/api` to `localhost:8080`.

With Docker: `docker compose up --build` (reads `.env` if present).

## Live mode

Copy `.env.example` to `.env` and set at least:

| Variable | Why |
| :--- | :--- |
| `GEMINI_API_KEY` | Structured LLM calls. Without it, `POST /api/v1/runs` in live mode returns `503 LLM_NOT_CONFIGURED`. |
| `SEC_EDGAR_USER_AGENT` | SEC requires an app name and a real, monitored contact email. Without it, SEC sources are skipped. |
| `LLM_PRICE_*_PER_MTOK_USD` | Current Gemini prices, so `DAILY_LLM_BUDGET_USD` is enforced correctly. |

Optional: `REDDIT_CLIENT_ID`/`REDDIT_CLIENT_SECRET` (Reddit), `FINNHUB_API_KEY` (US closing prices for P/E).
A missing optional source is recorded as a stated gap, not an error.

Scope: US SEC filers are fully supported live (XBRL companyfacts). Indian companies run best-effort from BSE
(annual-report PDFs, announcements, bhavcopy closes); NSE refuses non-browser clients and is only a fallback.
Pages behind logins (LinkedIn, Glassdoor, X) are never scraped.

### Pipeline

Twelve LangGraph stages (`backend/pipeline/graph.py`), each emitting a `running` and a terminal `StageEvent`:

```
S1 Entity → S2 Plan → S3 Filings → S4 Extract → S5 Metrics → S6 Red flags ┐
                    └→ S7 News ─────────────────────┬→ S8 Sentiment ─────────┼→ S10 Contradictions → S11 Synthesis → S12 Verify
                                         S5 Metrics ┴→ S9 Peers ─────────────┘
```

A failure in S1, S3 or S4 halts the run (the UI shows the error and offers a replay); any other stage failure is
reported on its card and the run continues.

## Tests

```bash
uv run pytest                      # backend: engines, sources (recorded HTTP), stages, full 12-stage graph
cd frontend && npx vitest run      # state projector and formatters
cd frontend && npx playwright test # browser smoke suite (starts the backend on port 8080 itself)
```

## Golden fixtures

1. **Human step.** Fill every `TODO` in `data/golden_inputs/<id>.yaml` by copying each figure, unit header and page
   number from the filing, with `verified_by`. The build refuses any `TODO`; figures are never typed from memory.
2. Build the replay from the verified YAML with the real engines:
   `uv run python -m scripts.build_golden_fixture --input data/golden_inputs/<id>.yaml --output data/golden_runs/<id>.jsonl.gz`
3. Once live sources and keys are available, re-record from a live run and compare against the YAML; the fixture
   is only overwritten if every line item matches:
   `uv run python -m scripts.record_golden_run --query "Block, Inc." --output data/golden_runs/block_nyse.jsonl.gz --compare data/golden_inputs/block_nyse.yaml`
4. `uv run python -m scripts.verify_golden_fixtures` (also run inside the Docker build).

## Deploy (Google Cloud Run)

```bash
export GCP_PROJECT_ID=your-project   # optional: GCP_REGION (default asia-south1), GCP_AR_REPO
./scripts/deploy_cloud_run.sh
```

The script runs the tests, builds with Cloud Build, and deploys with secrets `GEMINI_API_KEY` and
`SEC_EDGAR_USER_AGENT` from Secret Manager (create them first).

Limits worth knowing:

* Exactly one instance (`--max-instances 1 --session-affinity`): run state and the SQLite file live in the
  instance, so run history is lost when it scales to zero.
* The verification gate checks that every number matches its cited value. It does not check meaning: a sentence
  that cites two correct figures but calls the smaller one "above" the larger would pass. Review prose before
  presenting it.
* P/E uses the last close and trailing diluted EPS; market share is not computed.

## Demo-day failure drills

| If this happens | Do this |
| :--- | :--- |
| Live run returns `LLM_NOT_CONFIGURED` or `LLM_BUDGET_EXHAUSTED` | Switch the mode toggle to Replay and pick a company; replays need no key or budget. |
| A live run stops at Filings or Extract | The source refused or the PDF had no unit header; run the replay for the same company. |
| The stream drops mid-run | The client reconnects with `Last-Event-ID`; replays resume from the last event. |
| Health check fails after deploy | `curl $URL/api/v1/health`: `golden_runs_loaded` lists the fixtures found; `sqlite_wal` must be true. |
| Slow network on stage | Use Replay at 2x or instant from the speed control. |

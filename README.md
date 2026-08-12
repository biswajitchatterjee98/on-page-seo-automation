# On-Page SEO Automation

Crawl → deterministic rules → optional LLM suggestions → **human-approved CMS fixes** → history dashboard.

**Current stage: P0–P4 complete** (see [ARCHITECTURE_AND_PLAN.md](./ARCHITECTURE_AND_PLAN.md)).

Accepted LLM drafts are queued as `pending`. Rejected validations never enter the queue. Live apply re-crawls the URL and emits `alert_text` on score regression or `apply_failed`.

## Status

| Phase | Status |
|-------|--------|
| P0 Core | Done |
| P1 Pipeline / API / n8n | Done |
| P2 LLM + validator | Done |
| P3 Dashboard + trends | Done |
| P4 CMS queue + approve + dry-run/apply + audit + post-fix verify | Done |

## Quick start

```bash
cd on-page-seo-automation
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
pytest -q
```

### Audit / batch / dashboard / API

```bash
onpage-seo audit --url https://example.com --keyword "example" --llm --out report.json
onpage-seo batch --url-file urls.txt --keyword "example" --out batch.json
onpage-seo serve --port 8080
onpage-seo dashboard --port 8501
```

### Suggestion queue (P4)

```bash
onpage-seo queue list --status pending
onpage-seo queue approve 1 --actor biswajit
onpage-seo queue apply 1 --actor biswajit --dry-run    # audit only
onpage-seo queue apply 1 --actor biswajit --no-dry-run # live CMS (needs provider)
onpage-seo queue reject 2 --actor biswajit --reason "off-brand"
onpage-seo queue audit 1
```

API (Bearer `ONPAGE_SEO_API_TOKEN`):

- `GET /suggestions?status=pending`
- `POST /suggestions/{id}/approve`
- `POST /suggestions/{id}/reject`
- `POST /suggestions/{id}/apply` body: `{"actor":"…","dry_run":true}`

**Guarantees:** only `approved` items can apply; dry-run writes audit without CMS mutation; failures → `apply_failed` + audit.

### WordPress

```bash
export ONPAGE_SEO_CMS_PROVIDER=wordpress
export WP_BASE_URL=https://yoursite.example
export WP_USERNAME=...
export WP_APP_PASSWORD=...
export ONPAGE_SEO_CMS_DRY_RUN=0   # only after you trust dry-runs
```

P4 maps `title` → post/page title, `meta_description` → excerpt. Alt-text media apply is not implemented yet.

### Docker Compose

```bash
docker compose up --build
# API :8080 · Dashboard :8501 · n8n :5678
```

## Layout

```text
src/onpage_seo/
  queue.py          # enqueue accepted suggestions
  cms/              # adapters + approve/reject/apply
  storage/          # jobs, pages, reports, suggestions, audit_events
  ...
dashboard/app.py    # trends + approval UI
```

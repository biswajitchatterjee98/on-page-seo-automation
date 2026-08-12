# On-Page SEO Automation

Production-oriented pipeline that crawls a page, scores it with deterministic on-page SEO rules, and emits a JSON report.

**Current stage: P0** — single-URL crawl + rule engine + CLI. LLM, n8n, dashboard, and CMS auto-fix come later (see [ARCHITECTURE_AND_PLAN.md](./ARCHITECTURE_AND_PLAN.md)).

## What it checks

Title / meta length, keyword presence (title, H1, intro, URL), keyword density, image alt text, heading hierarchy, thin content, optional competitor length, schema (when enabled), duplicate title/meta (multi-page jobs).

## Quick start

```bash
cd on-page-seo-automation
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env        # optional; defaults work for local use
```

### Run an audit

```bash
onpage-seo audit \
  --url https://example.com \
  --keyword "example" \
  --out report.json
```

Useful flags:

| Flag | Purpose |
|------|---------|
| `--keyword` | Repeatable; first keyword is primary |
| `--render-mode auto\|static\|playwright` | Fetch strategy (default `auto`) |
| `--competitor-word-count N` | Enable competitor length compare |
| `--no-ssrf-guard` | Disable private-IP blocking (trusted URLs only) |
| `--ignore-robots` | Skip `robots.txt` (use sparingly; audited) |
| `--page-only` | Emit crawler JSON only (no rules) |
| `--out PATH` | Write report to a file (also prints stdout) |

Exit codes: `0` success, `2` crawl error (robots / timeout / SSRF / empty body / HTTP).

### Playwright (JS-rendered pages)

```bash
pip install -e ".[playwright]"
playwright install chromium
onpage-seo audit --url https://example.com --render-mode playwright --keyword example
```

### Tests

```bash
pytest -q
```

### Docker

```bash
docker build -t onpage-seo .
docker run --rm onpage-seo audit --url https://example.com --keyword example
```

## Project layout

```text
config/thresholds.yaml     # rule weights and thresholds (no secrets)
src/onpage_seo/
  crawl/                   # fetch, robots, extract
  rules/                   # versioned checklist + scoring
  report/                  # combined JSON report
  security/                # SSRF guard
  cli.py                   # onpage-seo entrypoint
tests/                     # rule + extract checks
ARCHITECTURE_AND_PLAN.md   # full architecture and phase plan
```

## Configuration

| Source | Examples |
|--------|----------|
| `config/thresholds.yaml` | Title/meta bounds, density band, weights, `rules_version` |
| Env (see `.env.example`) | `ONPAGE_SEO_USER_AGENT`, timeouts, crawl delay, SSRF guard, thresholds path |

Secrets stay in `.env` (gitignored). Do not commit real keys.

## Report shape (P0)

Successful audits include `job_id`, `rules_version`, `overall_score` / `max_score`, per-check `rules[]`, and `llm.status: "skipped"` until the LLM phase lands.

Crawl failures return a typed error (`robots_disallowed`, `ssrf_blocked`, `timeout`, `http_error`, `empty_body`, …) with score `0`.

## Roadmap

| Phase | Status |
|-------|--------|
| P0 Core (crawl + rules + CLI) | Done |
| P1 Multi-URL, Postgres, n8n | Planned |
| P2 LLM suggestions (validated) | Planned |
| P3 Dashboard | Planned |
| P4 CMS auto-fix + human approval | Planned |

Details, diagrams, and production guardrails: [ARCHITECTURE_AND_PLAN.md](./ARCHITECTURE_AND_PLAN.md).

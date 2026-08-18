"""Optional human-readable HTML summary from a report or batch summary."""

from __future__ import annotations

import html
from typing import Any


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def render_report_html(report: dict[str, Any]) -> str:
    rules_rows = []
    for check in report.get("rules") or []:
        rules_rows.append(
            "<tr>"
            f"<td>{_esc(check.get('id'))}</td>"
            f"<td>{_esc(check.get('status'))}</td>"
            f"<td>{_esc(check.get('score'))}/{_esc(check.get('weight'))}</td>"
            f"<td>{_esc(check.get('detail'))}</td>"
            "</tr>"
        )
    llm = report.get("llm") or {}
    notes = "".join(f"<li>{_esc(note)}</li>" for note in llm.get("notes") or [])
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/><title>SEO report {_esc(report.get('url'))}</title>
<style>
body{{font-family:system-ui,sans-serif;margin:2rem;line-height:1.4}}
table{{border-collapse:collapse;width:100%}} th,td{{border:1px solid #ccc;padding:.4rem;text-align:left}}
.status-error{{color:#a00}} .status-ok{{color:#060}}
</style></head><body>
<h1>On-page SEO report</h1>
<p><strong>URL:</strong> {_esc(report.get('url'))}<br/>
<strong>Job:</strong> {_esc(report.get('job_id'))}<br/>
<strong>Status:</strong> <span class="status-{_esc(report.get('status'))}">{_esc(report.get('status'))}</span><br/>
<strong>Score:</strong> {_esc(report.get('overall_score'))}/{_esc(report.get('max_score'))}<br/>
<strong>Rules:</strong> {_esc(report.get('rules_version'))}<br/>
<strong>Model:</strong> {_esc(report.get('model_version'))}</p>
<h2>Checks</h2>
<table><thead><tr><th>Id</th><th>Status</th><th>Score</th><th>Detail</th></tr></thead>
<tbody>{''.join(rules_rows) or '<tr><td colspan="4">No checks</td></tr>'}</tbody></table>
<h2>LLM</h2>
<p>Status: {_esc(llm.get('status'))} · Semantic coverage: {_esc(llm.get('semantic_coverage_score'))}</p>
<ul>{notes or '<li>None</li>'}</ul>
</body></html>
"""


def render_batch_html(summary: dict[str, Any]) -> str:
    cards = []
    for report in summary.get("reports") or []:
        cards.append(
            f"<section><h2>{_esc(report.get('url'))}</h2>"
            f"<p>score {_esc(report.get('overall_score'))}/{_esc(report.get('max_score'))} · "
            f"{_esc(report.get('status'))}</p></section>"
        )
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/><title>SEO batch {_esc(summary.get('job_id'))}</title>
<style>body{{font-family:system-ui,sans-serif;margin:2rem}} section{{margin-bottom:1.5rem}}</style>
</head><body>
<h1>Batch job {_esc(summary.get('job_id'))}</h1>
<p>Status: {_esc(summary.get('status'))} · Pages: {_esc(summary.get('page_count'))}</p>
{''.join(cards)}
</body></html>
"""

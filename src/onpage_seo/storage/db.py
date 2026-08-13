"""Job/page/report persistence — Postgres for prod, memory for local/tests."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass
class JobRecord:
    id: str
    status: str
    config_json: dict[str, Any]
    config_hash: str
    rules_version: str
    summary_json: dict[str, Any] | None = None
    created_at: str = field(default_factory=_utc_now)
    updated_at: str = field(default_factory=_utc_now)


class Store(Protocol):
    def ensure_schema(self) -> None: ...

    def find_completed_by_hash(self, config_hash: str) -> JobRecord | None: ...

    def create_job(self, job: JobRecord) -> None: ...

    def update_job(
        self,
        job_id: str,
        *,
        status: str,
        summary_json: dict[str, Any] | None = None,
    ) -> None: ...

    def get_job(self, job_id: str) -> JobRecord | None: ...

    def save_page(
        self,
        job_id: str,
        *,
        url: str,
        status: str,
        http_status: int | None,
        error_code: str | None,
        page_json: dict[str, Any] | None,
    ) -> int: ...

    def save_report(self, page_id: int, overall_score: int, report_json: dict[str, Any]) -> int: ...

    def list_recent_jobs(self, limit: int = 20) -> list[JobRecord]: ...

    def list_urls(self, limit: int = 100) -> list[str]: ...

    def url_score_history(self, url: str, limit: int = 50) -> list[dict[str, Any]]: ...

    def create_suggestion(
        self,
        *,
        report_id: int,
        job_id: str,
        url: str,
        field: str,
        payload_json: dict[str, Any],
        before_json: dict[str, Any] | None,
        status: str = "pending",
    ) -> int: ...

    def get_suggestion(self, suggestion_id: int) -> dict[str, Any] | None: ...

    def list_suggestions(
        self,
        *,
        status: str | None = None,
        job_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]: ...

    def update_suggestion_status(self, suggestion_id: int, *, status: str) -> None: ...

    def add_audit_event(
        self,
        suggestion_id: int,
        *,
        action: str,
        actor: str,
        before_json: dict[str, Any] | None,
        after_json: dict[str, Any] | None,
        detail: str | None,
    ) -> int: ...

    def list_audit_events(self, suggestion_id: int) -> list[dict[str, Any]]: ...


class MemoryStore:
    def __init__(self) -> None:
        self.jobs: dict[str, JobRecord] = {}
        self.pages: dict[str, list[dict[str, Any]]] = {}
        self.reports: list[dict[str, Any]] = []
        self.suggestions: dict[int, dict[str, Any]] = {}
        self.audit_events: list[dict[str, Any]] = []
        self._page_seq = 0
        self._report_seq = 0
        self._suggestion_seq = 0
        self._audit_seq = 0

    def ensure_schema(self) -> None:
        return None

    def find_completed_by_hash(self, config_hash: str) -> JobRecord | None:
        matches = [
            job
            for job in self.jobs.values()
            if job.config_hash == config_hash
            and job.status in {"completed", "completed_with_errors"}
        ]
        return matches[-1] if matches else None

    def create_job(self, job: JobRecord) -> None:
        self.jobs[job.id] = job
        self.pages[job.id] = []

    def update_job(
        self,
        job_id: str,
        *,
        status: str,
        summary_json: dict[str, Any] | None = None,
    ) -> None:
        job = self.jobs[job_id]
        job.status = status
        job.updated_at = _utc_now()
        if summary_json is not None:
            job.summary_json = summary_json

    def get_job(self, job_id: str) -> JobRecord | None:
        return self.jobs.get(job_id)

    def save_page(
        self,
        job_id: str,
        *,
        url: str,
        status: str,
        http_status: int | None,
        error_code: str | None,
        page_json: dict[str, Any] | None,
    ) -> int:
        self._page_seq += 1
        page_id = self._page_seq
        self.pages.setdefault(job_id, []).append(
            {
                "id": page_id,
                "url": url,
                "status": status,
                "http_status": http_status,
                "error_code": error_code,
                "page_json": page_json,
            }
        )
        return page_id

    def save_report(self, page_id: int, overall_score: int, report_json: dict[str, Any]) -> int:
        self._report_seq += 1
        report_id = self._report_seq
        self.reports.append(
            {
                "id": report_id,
                "page_id": page_id,
                "overall_score": overall_score,
                "report_json": report_json,
            }
        )
        return report_id

    def list_recent_jobs(self, limit: int = 20) -> list[JobRecord]:
        jobs = sorted(self.jobs.values(), key=lambda job: job.created_at, reverse=True)
        return jobs[:limit]

    def list_urls(self, limit: int = 100) -> list[str]:
        seen: list[str] = []
        for pages in self.pages.values():
            for page in pages:
                url = str(page.get("url") or "")
                if url and url not in seen:
                    seen.append(url)
                if len(seen) >= limit:
                    return seen
        return seen

    def url_score_history(self, url: str, limit: int = 50) -> list[dict[str, Any]]:
        from onpage_seo.trends import failed_check_ids

        page_id_to_meta: dict[int, dict[str, Any]] = {}
        for job_id, pages in self.pages.items():
            for page in pages:
                if page.get("url") == url:
                    page_id_to_meta[int(page["id"])] = {"job_id": job_id, "status": page.get("status")}
        rows: list[dict[str, Any]] = []
        for report in self.reports:
            meta = page_id_to_meta.get(int(report["page_id"]))
            if not meta:
                continue
            job = self.jobs.get(meta["job_id"])
            report_json = report.get("report_json") or {}
            rows.append(
                {
                    "job_id": meta["job_id"],
                    "url": url,
                    "overall_score": int(report["overall_score"]),
                    "page_status": meta["status"],
                    "created_at": job.created_at if job else "",
                    "failed_checks": failed_check_ids(report_json),
                    "report_json": report_json,
                }
            )
        rows.sort(key=lambda row: str(row.get("created_at") or ""))
        return rows[-limit:]

    def create_suggestion(
        self,
        *,
        report_id: int,
        job_id: str,
        url: str,
        field: str,
        payload_json: dict[str, Any],
        before_json: dict[str, Any] | None,
        status: str = "pending",
    ) -> int:
        self._suggestion_seq += 1
        suggestion_id = self._suggestion_seq
        now = _utc_now()
        self.suggestions[suggestion_id] = {
            "id": suggestion_id,
            "report_id": report_id,
            "job_id": job_id,
            "url": url,
            "field": field,
            "payload_json": payload_json,
            "before_json": before_json,
            "status": status,
            "created_at": now,
            "updated_at": now,
        }
        return suggestion_id

    def get_suggestion(self, suggestion_id: int) -> dict[str, Any] | None:
        item = self.suggestions.get(suggestion_id)
        return dict(item) if item else None

    def list_suggestions(
        self,
        *,
        status: str | None = None,
        job_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        rows = list(self.suggestions.values())
        if status:
            rows = [row for row in rows if row["status"] == status]
        if job_id:
            rows = [row for row in rows if row["job_id"] == job_id]
        rows.sort(key=lambda row: int(row["id"]))
        return [dict(row) for row in rows[:limit]]

    def update_suggestion_status(self, suggestion_id: int, *, status: str) -> None:
        item = self.suggestions[suggestion_id]
        item["status"] = status
        item["updated_at"] = _utc_now()

    def add_audit_event(
        self,
        suggestion_id: int,
        *,
        action: str,
        actor: str,
        before_json: dict[str, Any] | None,
        after_json: dict[str, Any] | None,
        detail: str | None,
    ) -> int:
        self._audit_seq += 1
        event_id = self._audit_seq
        self.audit_events.append(
            {
                "id": event_id,
                "suggestion_id": suggestion_id,
                "action": action,
                "actor": actor,
                "before_json": before_json,
                "after_json": after_json,
                "detail": detail,
                "created_at": _utc_now(),
            }
        )
        return event_id

    def list_audit_events(self, suggestion_id: int) -> list[dict[str, Any]]:
        return [dict(event) for event in self.audit_events if event["suggestion_id"] == suggestion_id]


class PostgresStore:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def _connect(self):
        import psycopg

        return psycopg.connect(self.database_url)

    def ensure_schema(self) -> None:
        sql = _SCHEMA_PATH.read_text(encoding="utf-8")
        with self._connect() as conn:
            conn.execute(sql)
            conn.commit()

    def find_completed_by_hash(self, config_hash: str) -> JobRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, status, config_json, config_hash, rules_version, summary_json,
                       created_at, updated_at
                FROM jobs
                WHERE config_hash = %s
                  AND status IN ('completed', 'completed_with_errors')
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (config_hash,),
            ).fetchone()
        if not row:
            return None
        return self._row_to_job(row)

    def create_job(self, job: JobRecord) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO jobs (id, status, config_json, config_hash, rules_version, summary_json)
                VALUES (%s, %s, %s::jsonb, %s, %s, %s::jsonb)
                """,
                (
                    job.id,
                    job.status,
                    json.dumps(job.config_json),
                    job.config_hash,
                    job.rules_version,
                    json.dumps(job.summary_json) if job.summary_json is not None else None,
                ),
            )
            conn.commit()

    def update_job(
        self,
        job_id: str,
        *,
        status: str,
        summary_json: dict[str, Any] | None = None,
    ) -> None:
        with self._connect() as conn:
            if summary_json is None:
                conn.execute(
                    "UPDATE jobs SET status = %s, updated_at = NOW() WHERE id = %s",
                    (status, job_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE jobs
                    SET status = %s, summary_json = %s::jsonb, updated_at = NOW()
                    WHERE id = %s
                    """,
                    (status, json.dumps(summary_json), job_id),
                )
            conn.commit()

    def get_job(self, job_id: str) -> JobRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, status, config_json, config_hash, rules_version, summary_json,
                       created_at, updated_at
                FROM jobs WHERE id = %s
                """,
                (job_id,),
            ).fetchone()
        return self._row_to_job(row) if row else None

    def save_page(
        self,
        job_id: str,
        *,
        url: str,
        status: str,
        http_status: int | None,
        error_code: str | None,
        page_json: dict[str, Any] | None,
    ) -> int:
        with self._connect() as conn:
            row = conn.execute(
                """
                INSERT INTO pages (job_id, url, status, http_status, error_code, page_json)
                VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                ON CONFLICT (job_id, url) DO UPDATE SET
                    status = EXCLUDED.status,
                    http_status = EXCLUDED.http_status,
                    error_code = EXCLUDED.error_code,
                    page_json = EXCLUDED.page_json
                RETURNING id
                """,
                (
                    job_id,
                    url,
                    status,
                    http_status,
                    error_code,
                    json.dumps(page_json) if page_json is not None else None,
                ),
            ).fetchone()
            conn.commit()
        return int(row[0])

    def save_report(self, page_id: int, overall_score: int, report_json: dict[str, Any]) -> int:
        with self._connect() as conn:
            row = conn.execute(
                """
                INSERT INTO reports (page_id, overall_score, report_json)
                VALUES (%s, %s, %s::jsonb)
                RETURNING id
                """,
                (page_id, overall_score, json.dumps(report_json)),
            ).fetchone()
            conn.commit()
        return int(row[0])

    def list_recent_jobs(self, limit: int = 20) -> list[JobRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, status, config_json, config_hash, rules_version, summary_json,
                       created_at, updated_at
                FROM jobs
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (limit,),
            ).fetchall()
        return [self._row_to_job(row) for row in rows]

    def list_urls(self, limit: int = 100) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT url FROM pages
                ORDER BY url
                LIMIT %s
                """,
                (limit,),
            ).fetchall()
        return [str(row[0]) for row in rows]

    def url_score_history(self, url: str, limit: int = 50) -> list[dict[str, Any]]:
        from onpage_seo.trends import failed_check_ids

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT j.id, p.url, r.overall_score, p.status, r.created_at, r.report_json
                FROM reports r
                JOIN pages p ON p.id = r.page_id
                JOIN jobs j ON j.id = p.job_id
                WHERE p.url = %s
                ORDER BY r.created_at ASC
                LIMIT %s
                """,
                (url, limit),
            ).fetchall()
        history: list[dict[str, Any]] = []
        for row in rows:
            report_json = row[5]
            if isinstance(report_json, str):
                report_json = json.loads(report_json)
            history.append(
                {
                    "job_id": row[0],
                    "url": row[1],
                    "overall_score": int(row[2]),
                    "page_status": row[3],
                    "created_at": str(row[4]),
                    "failed_checks": failed_check_ids(report_json or {}),
                    "report_json": report_json or {},
                }
            )
        return history

    def create_suggestion(
        self,
        *,
        report_id: int,
        job_id: str,
        url: str,
        field: str,
        payload_json: dict[str, Any],
        before_json: dict[str, Any] | None,
        status: str = "pending",
    ) -> int:
        with self._connect() as conn:
            row = conn.execute(
                """
                INSERT INTO suggestions
                    (report_id, job_id, url, field, payload_json, before_json, status)
                VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)
                RETURNING id
                """,
                (
                    report_id,
                    job_id,
                    url,
                    field,
                    json.dumps(payload_json),
                    json.dumps(before_json) if before_json is not None else None,
                    status,
                ),
            ).fetchone()
            conn.commit()
        return int(row[0])

    def get_suggestion(self, suggestion_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, report_id, job_id, url, field, payload_json, before_json,
                       status, created_at, updated_at
                FROM suggestions WHERE id = %s
                """,
                (suggestion_id,),
            ).fetchone()
        return self._row_to_suggestion(row) if row else None

    def list_suggestions(
        self,
        *,
        status: str | None = None,
        job_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses = ["1=1"]
        params: list[Any] = []
        if status:
            clauses.append("status = %s")
            params.append(status)
        if job_id:
            clauses.append("job_id = %s")
            params.append(job_id)
        params.append(limit)
        sql = f"""
            SELECT id, report_id, job_id, url, field, payload_json, before_json,
                   status, created_at, updated_at
            FROM suggestions
            WHERE {' AND '.join(clauses)}
            ORDER BY id ASC
            LIMIT %s
        """
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_suggestion(row) for row in rows]

    def update_suggestion_status(self, suggestion_id: int, *, status: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE suggestions
                SET status = %s, updated_at = NOW()
                WHERE id = %s
                """,
                (status, suggestion_id),
            )
            conn.commit()

    def add_audit_event(
        self,
        suggestion_id: int,
        *,
        action: str,
        actor: str,
        before_json: dict[str, Any] | None,
        after_json: dict[str, Any] | None,
        detail: str | None,
    ) -> int:
        with self._connect() as conn:
            row = conn.execute(
                """
                INSERT INTO audit_events
                    (suggestion_id, action, actor, before_json, after_json, detail)
                VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s)
                RETURNING id
                """,
                (
                    suggestion_id,
                    action,
                    actor,
                    json.dumps(before_json) if before_json is not None else None,
                    json.dumps(after_json) if after_json is not None else None,
                    detail,
                ),
            ).fetchone()
            conn.commit()
        return int(row[0])

    def list_audit_events(self, suggestion_id: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, suggestion_id, action, actor, before_json, after_json, detail, created_at
                FROM audit_events
                WHERE suggestion_id = %s
                ORDER BY id ASC
                """,
                (suggestion_id,),
            ).fetchall()
        events: list[dict[str, Any]] = []
        for row in rows:
            before = row[4]
            after = row[5]
            if isinstance(before, str):
                before = json.loads(before)
            if isinstance(after, str):
                after = json.loads(after)
            events.append(
                {
                    "id": row[0],
                    "suggestion_id": row[1],
                    "action": row[2],
                    "actor": row[3],
                    "before_json": before,
                    "after_json": after,
                    "detail": row[6],
                    "created_at": str(row[7]),
                }
            )
        return events

    @staticmethod
    def _row_to_suggestion(row: Any) -> dict[str, Any]:
        payload = row[5]
        before = row[6]
        if isinstance(payload, str):
            payload = json.loads(payload)
        if isinstance(before, str):
            before = json.loads(before)
        return {
            "id": int(row[0]),
            "report_id": int(row[1]),
            "job_id": row[2],
            "url": row[3],
            "field": row[4],
            "payload_json": payload or {},
            "before_json": before,
            "status": row[7],
            "created_at": str(row[8]),
            "updated_at": str(row[9]),
        }

    @staticmethod
    def _row_to_job(row: Any) -> JobRecord:
        config = row[2]
        summary = row[5]
        if isinstance(config, str):
            config = json.loads(config)
        if isinstance(summary, str):
            summary = json.loads(summary)
        return JobRecord(
            id=row[0],
            status=row[1],
            config_json=config or {},
            config_hash=row[3],
            rules_version=row[4],
            summary_json=summary,
            created_at=str(row[6]),
            updated_at=str(row[7]),
        )


def open_store(database_url: str | None) -> Store:
    if database_url and (database_url.startswith("postgres://") or database_url.startswith("postgresql://")):
        try:
            store = PostgresStore(database_url)
            store.ensure_schema()
            return store
        except Exception as exc:
            import logging
            logging.getLogger("onpage_seo").warning(
                "PostgreSQL connection failed (%s) — falling back to local in-memory store", exc
            )
            return MemoryStore()
    return MemoryStore()

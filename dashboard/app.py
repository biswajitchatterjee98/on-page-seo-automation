"""Streamlit trend dashboard over stored SEO job results."""

from __future__ import annotations

import os

import streamlit as st

from onpage_seo.config import load_settings
from onpage_seo.dashboard_auth import dashboard_credentials_configured, verify_dashboard_login
from onpage_seo.storage import open_store
from onpage_seo.trends import detect_regressions


def _require_login() -> bool:
    if st.session_state.get("authenticated"):
        return True
    if not dashboard_credentials_configured():
        st.error(
            "Dashboard auth is not configured. Set DASHBOARD_USER and DASHBOARD_PASSWORD."
        )
        st.stop()
        return False

    st.title("On-page SEO dashboard")
    st.caption("Sign in required")
    username = st.text_input("Username")
    password = st.text_input("Password", type="password")
    if st.button("Sign in"):
        if verify_dashboard_login(username, password):
            st.session_state.authenticated = True
            st.session_state.dash_user = username
            st.rerun()
        st.error("Invalid credentials")
    st.stop()
    return False


def main() -> None:
    st.set_page_config(page_title="On-page SEO", layout="wide")
    _require_login()

    settings = load_settings()
    store = open_store(settings.database_url)
    drop_points = int(os.environ.get("ONPAGE_SEO_REGRESSION_POINTS", "5"))

    st.title("On-page SEO trends")
    if not settings.database_url:
        st.warning(
            "DATABASE_URL is not set — using in-memory store for this process "
            "(history will be empty unless jobs ran in this same process)."
        )

    urls = store.list_urls(limit=200)
    jobs = store.list_recent_jobs(limit=15)

    col_a, col_b = st.columns(2)
    with col_a:
        st.subheader("Recent jobs")
        if not jobs:
            st.info("No jobs stored yet. Run `onpage-seo batch` or the Job API.")
        else:
            st.dataframe(
                [
                    {
                        "job_id": job.id,
                        "status": job.status,
                        "created_at": job.created_at,
                        "rules_version": job.rules_version,
                    }
                    for job in jobs
                ],
                use_container_width=True,
            )

    with col_b:
        st.subheader("Regressions")
        regressions = []
        for url in urls:
            history = store.url_score_history(url, limit=20)
            result = detect_regressions(history, drop_points=drop_points)
            if result and result.get("regressed"):
                regressions.append({"url": url, **result})
        if regressions:
            st.error(f"{len(regressions)} URL(s) dropped ≥ {drop_points} points")
            st.dataframe(regressions, use_container_width=True)
        else:
            st.success(f"No regressions (≥ {drop_points} point drops) detected")

    st.subheader("URL history")
    if not urls:
        st.info("No audited URLs yet.")
        return

    selected = st.selectbox("URL", urls)
    history = store.url_score_history(selected, limit=50)
    if len(history) < 2:
        st.warning("Need at least two runs of this URL to show a trend.")
    regression = detect_regressions(history, drop_points=drop_points)
    if regression and regression.get("regressed"):
        st.error(
            f"Regression: {regression['previous_score']} → {regression['latest_score']} "
            f"(Δ {regression['delta']})"
        )
    elif regression:
        st.caption(
            f"Latest change: {regression['previous_score']} → {regression['latest_score']} "
            f"(Δ {regression['delta']})"
        )

    chart_rows = [
        {"created_at": row["created_at"], "overall_score": row["overall_score"]}
        for row in history
    ]
    if chart_rows:
        st.line_chart(chart_rows, x="created_at", y="overall_score")
        st.dataframe(
            [
                {
                    "created_at": row["created_at"],
                    "job_id": row["job_id"],
                    "score": row["overall_score"],
                    "failed_checks": ", ".join(row.get("failed_checks") or []),
                }
                for row in history
            ],
            use_container_width=True,
        )

    st.subheader("Suggestion queue (P4)")
    pending = store.list_suggestions(status="pending", limit=50)
    approved = store.list_suggestions(status="approved", limit=50)
    st.caption(f"Pending: {len(pending)} · Approved: {len(approved)}")
    if pending:
        st.dataframe(
            [
                {
                    "id": row["id"],
                    "url": row["url"],
                    "field": row["field"],
                    "proposed": (row.get("payload_json") or {}).get("value"),
                    "before": (row.get("before_json") or {}).get("value"),
                }
                for row in pending
            ],
            use_container_width=True,
        )
        selected_id = st.number_input("Suggestion id", min_value=1, step=1, value=int(pending[0]["id"]))
        actor = st.text_input("Actor", value=st.session_state.get("dash_user") or "dashboard")
        c1, c2, c3 = st.columns(3)
        from onpage_seo.cms import apply_suggestion, approve_suggestion, reject_suggestion

        if c1.button("Approve"):
            try:
                approve_suggestion(store, int(selected_id), actor=actor)
                st.success(f"Approved #{selected_id}")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
        if c2.button("Reject"):
            try:
                reject_suggestion(store, int(selected_id), actor=actor, reason="dashboard reject")
                st.success(f"Rejected #{selected_id}")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
        dry = c3.checkbox("Dry-run apply", value=True)
        if c3.button("Apply approved"):
            try:
                result = apply_suggestion(
                    store,
                    int(selected_id),
                    actor=actor,
                    settings=settings,
                    dry_run=dry,
                )
                if result.get("error"):
                    st.error(result["error"])
                else:
                    st.success("Apply recorded" + (" (dry-run)" if dry else ""))
                st.json(result)
            except ValueError as exc:
                st.error(str(exc))
    else:
        st.info("No pending suggestions. LLM-accepted drafts appear here after jobs run.")

    if st.button("Sign out"):
        st.session_state.authenticated = False
        st.rerun()


if __name__ == "__main__":
    main()

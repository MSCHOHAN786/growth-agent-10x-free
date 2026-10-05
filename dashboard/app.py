"""Growth Agent 10x Free — Streamlit dashboard.

Tabs: Kanalen, Goedkeuring, Analytics, LLM-gebruik.
All Supabase calls are wrapped in try/except and surface st.error on failure.
Secrets: Streamlit Cloud secrets (st.secrets) with os.environ fallback.
A simple password gate (DASHBOARD_PASSWORD) protects the dashboard.
"""

import os
from datetime import date, datetime, timezone

import streamlit as st

st.set_page_config(page_title="Growth Agent 10x Free", layout="wide")


# ---------------------------------------------------------------- secrets
def get_secret(name, default=""):
    try:
        return st.secrets.get(name, os.environ.get(name, default))
    except Exception:
        return os.environ.get(name, default)


SUPABASE_URL = get_secret("SUPABASE_URL")
SUPABASE_SERVICE_KEY = get_secret("SUPABASE_SERVICE_KEY")
DASHBOARD_PASSWORD = get_secret("DASHBOARD_PASSWORD")

# ------------------------------------------------------------ password gate
if DASHBOARD_PASSWORD:
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False
    if not st.session_state.authenticated:
        st.title("Growth Agent 10x Free")
        pw = st.text_input("Wachtwoord", type="password")
        if st.button("Inloggen"):
            if pw == DASHBOARD_PASSWORD:
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("Onjuist wachtwoord.")
        st.stop()

# --------------------------------------------------------------- supabase
try:
    from supabase import create_client

    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        st.error("SUPABASE_URL / SUPABASE_SERVICE_KEY ontbreken in secrets.")
        st.stop()
    sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
except Exception as e:
    st.error(f"Supabase client kon niet worden aangemaakt: {e}")
    st.stop()


def fetch_table(table, select="*", order=None, limit=500):
    try:
        q = sb.table(table).select(select).limit(limit)
        if order:
            q = q.order(order)
        data = q.execute().data or []
        # Normalize defensively: only lists of dicts are usable below.
        # (Guards against client-version quirks returning a bare dict
        # or mixed row shapes, which crashed the dashboard before.)
        if isinstance(data, dict):
            data = [data]
        return [row for row in data if isinstance(row, dict)]
    except Exception as e:
        st.error(f"Fout bij ophalen van '{table}': {e}")
        return []


st.title("Growth Agent 10x Free")
tab_channels, tab_approval, tab_analytics, tab_llm = st.tabs(
    ["Kanalen", "Goedkeuring", "Analytics", "LLM-gebruik"]
)

# ----------------------------------------------------------------- Kanalen
with tab_channels:
    st.header("Kanalen")
    channels = fetch_table("channels", "id,name,sub_niche,status", order="name")
    if channels:
        st.dataframe(
            [
                {
                    "Naam": c.get("name"),
                    "Sub-niche": c.get("sub_niche"),
                    "Status": c.get("status"),
                }
                for c in channels
            ],
            use_container_width=True,
        )
    else:
        st.info("Geen kanalen gevonden.")

# -------------------------------------------------------------- Goedkeuring
with tab_approval:
    st.header("Goedkeuring")
    pending = fetch_table("approval_queue", "*", order="requested_at")
    pending = [a for a in pending if not a.get("decision")]
    if not pending:
        st.success("Geen items wachten op goedkeuring.")
    else:
        videos = {v.get("id"): v for v in fetch_table("videos", "id,title,channel_id") if v.get("id") is not None}
        channels_map = {c.get("id"): c.get("name", "?") for c in fetch_table("channels", "id,name") if c.get("id") is not None}
        for item in pending:
            video = videos.get(item.get("video_id"), {})
            ch_name = channels_map.get(video.get("channel_id"), "?")
            with st.container(border=True):
                st.subheader(video.get("title", "(geen titel)"))
                st.caption(f"Kanaal: {ch_name} | Item: {item.get('id')}")
                col1, col2 = st.columns(2)
                with col1:
                    if st.button("Goedkeuren", key=f"approve-{item['id']}"):
                        try:
                            now_iso = datetime.now(timezone.utc).isoformat()
                            sb.table("approval_queue").update(
                                {"decision": "approved", "decided_at": now_iso}
                            ).eq("id", item["id"]).execute()
                            if item.get("video_id"):
                                # 'scheduled' = klaar voor publish_run.py
                                # (videos.status CHECK staat alleen
                                # draft/pending_approval/scheduled/published toe)
                                sb.table("videos").update(
                                    {"status": "scheduled", "scheduled_at": now_iso}
                                ).eq("id", item["video_id"]).execute()
                            st.success("Goedgekeurd.")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Goedkeuren mislukt: {e}")
                with col2:
                    if st.button("Afwijzen", key=f"reject-{item['id']}"):
                        try:
                            now_iso = datetime.now(timezone.utc).isoformat()
                            sb.table("approval_queue").update(
                                {"decision": "rejected", "decided_at": now_iso}
                            ).eq("id", item["id"]).execute()
                            if item.get("video_id"):
                                # Terug naar 'draft' zodat hij opnieuw
                                # bewerkt kan worden ('rejected' is geen
                                # geldige videos.status).
                                sb.table("videos").update({"status": "draft"}).eq(
                                    "id", item["video_id"]
                                ).execute()
                            st.warning("Afgewezen.")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Afwijzen mislukt: {e}")

# --------------------------------------------------------------- Analytics
with tab_analytics:
    st.header("Analytics — views per kanaal")
    try:
        videos = fetch_table("videos", "id,title,channel_id")
        analytics = fetch_table("analytics", "video_id,views")
        channels = fetch_table("channels", "id,name")
        ch_name = {c.get("id"): c.get("name", "?") for c in channels if c.get("id") is not None}
        vid_channel = {v.get("id"): v.get("channel_id") for v in videos if v.get("id") is not None}
        totals = {}
        for row in analytics:
            cid = vid_channel.get(row.get("video_id"))
            if cid:
                totals[cid] = totals.get(cid, 0) + (row.get("views") or 0)
        if totals:
            st.bar_chart(
                {ch_name.get(cid, str(cid)): views for cid, views in totals.items()}
            )
        else:
            st.info("Nog geen analytics-data.")
    except Exception as e:
        st.error(f"Analytics laden mislukt: {e}")

# ------------------------------------------------------------- LLM-gebruik
with tab_llm:
    st.header("LLM-gebruik (vandaag)")
    today = date.today().isoformat()
    try:
        usage = fetch_table("llm_usage", "provider,requests,quota")
        usage = [u for u in usage if str(u.get("date", today)) == today or "date" not in u]
        if usage:
            for u in usage:
                provider = u.get("provider", "?")
                req = u.get("requests") or 0
                quota = u.get("quota") or 0
                label = f"{provider}: {req}/{quota}" if quota else f"{provider}: {req}"
                st.write(label)
                if quota:
                    st.progress(min(req / quota, 1.0))
        else:
            st.info("Geen LLM-gebruik geregistreerd voor vandaag.")
    except Exception as e:
        st.error(f"LLM-gebruik laden mislukt: {e}")

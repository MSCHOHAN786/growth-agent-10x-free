# Growth Agent 10x Free 🌱

**A completely free, serverless YouTube automation system for 10 Dutch-language psychology channels — with a human always in the loop.**

[![Cost](https://img.shields.io/badge/Cost-%240.00%2Fmonth-brightgreen)](README.md)
[![Python](https://img.shields.io/badge/Python-3.11-blue)](README.md)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

No credit card. No paid APIs. No servers to babysit. Ten channels, one repo, zero dollars.

---

## 1. What it does

**Growth Agent 10x Free** runs **10 Dutch-language psychology niche channels** (`psy_nl_01` … `psy_nl_10`) end-to-end:

1. **Research** — finds trending psychology topics per channel (sleep, anxiety, habits, relationships, …)
2. **Content** — drafts Dutch scripts, generates voiceover + visuals via free LLM tiers
3. **SEO** — writes Dutch titles, descriptions, tags and chapters
4. **Human approval** — every video pauses for your ✅ / ✏️ / ❌ in Telegram or the Streamlit dashboard before anything is published
5. **Publish** — uploads approved videos via the YouTube Data API
6. **Engage** — queues comments and community replies for your review
7. **Analyze** — tracks performance per channel and feeds winning patterns back into the niche engine

Nothing goes live without a human saying yes. The system does the repetitive work; you keep creative control.

---

## 2. Architecture

```
┌─────────────────────────────────────────────────────────┐
│ 1. GitHub Actions        5 cron workflows (orchestration)│
└────────────────────────┬────────────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────────────┐
│ 2. Cloudflare Workers    /health, /webhook/youtube,     │
│                          /telegram, /trigger/colab       │
└────────────────────────┬────────────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────────────┐
│ 3. Colab / Kaggle        heavy lifting: TTS, assembly,  │
│                          rendering (free GPU notebooks)  │
└────────────────────────┬────────────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────────────┐
│ 4. Supabase              Postgres: 9 tables (channels,  │
│                          videos, queues, usage, …)      │
└────────────────────────┬────────────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────────────┐
│ 5. LLM router            Groq → Gemini → Cohere           │
│                          (automatic free-tier fallback)  │
└────────────────────────┬────────────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────────────┐
│ 6. Streamlit             approval dashboard (10 channels)│
└────────────────────────┬────────────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────────────┐
│ 7. Telegram              ✅ approve / ✏️ edit / ❌ reject │
│                          human-in-the-loop               │
└─────────────────────────────────────────────────────────┘
```

GitHub Actions schedules the work. Cloudflare Workers handle real-time events (YouTube webhooks, Telegram bot). Colab does the heavy compute. Supabase is the single source of truth. The LLM router spreads load across three free providers so no single quota kills the pipeline. Streamlit + Telegram keep a human in charge of every publish.

---

## 3. Free-tier stack

| Service | Free limit | Used for |
|---|---|---|
| GitHub Actions (public repo) | Unlimited minutes | 5 cron workflows |
| Cloudflare Workers | 100,000 requests/day | Webhooks, Telegram bot, Colab trigger |
| Google Colab / Kaggle | Free GPU sessions | TTS, video assembly, rendering |
| Supabase | 500 MB DB, 50k monthly active users | Postgres: channels, videos, queues, usage |
| Groq | Free tier, rate-limited | Primary LLM (fast inference) |
| Google Gemini | Free tier via AI Studio | LLM fallback #1 |
| Cohere | Free tier | LLM fallback #2 |
| Telegram Bot API | Free, no message cap | Approval messages, alerts |
| Streamlit Community Cloud | 1 app free | Approval dashboard |
| YouTube Data API v3 | 10,000 quota units/day **per project** | Uploads, analytics (×10 projects = 100k units/day) |

---

## 4. Repo structure

```
growth-agent-10x-free/
├── .github/
│   └── workflows/            # 5 cron workflows
│       ├── daily_research.yml   # 06:00 PKT: research + queue drafts for approval
│       ├── daily_publish.yml    # 10:00 PKT: upload approved+scheduled videos
│       ├── hourly_comments.yml  # every 3h: draft comment replies (approval only)
│       ├── keepalive.yml        # every 3 days: ping Supabase + dashboard
│       └── weekly_report.yml    # Sun 21:00 PKT: analytics + weekly report
├── agents/                   # 7 agents (all Dutch psychology prompts)
│   ├── __init__.py
│   ├── base_agent.py         # BaseAgent: logging, alerts, YouTube service, DB helpers
│   ├── research_agent.py     # trend topics per channel (idempotent/day)
│   ├── content_agent.py      # 5 titles + full Dutch script → videos.status='draft'
│   ├── seo_agent.py          # Dutch title/description/tags/pinned comment
│   ├── publish_agent.py      # uploads ONLY approved videos, YouTube quota guard
│   ├── engagement_agent.py   # drafts Dutch replies → approval queue (never auto-posts)
│   └── analytics_agent.py    # per-channel stats + weekly summary
├── cloudflare/
│   ├── worker.js             # /health, /webhook/youtube, /telegram, /trigger/colab
│   └── wrangler.toml
├── colab/
│   └── content_worker.ipynb  # nbformat 4: free-GPU content generation → Supabase
├── config/
│   ├── channels.yaml         # 10 channels: psy_nl_01 … psy_nl_10
│   ├── niche_library.yaml    # 10 Dutch sub-niches (naam, beschrijving, pijlers)
│   └── settings.yaml         # crons, free-tier limits, LLM priority
├── core/                     # shared building blocks
│   ├── __init__.py
│   ├── crypto.py             # Fernet encrypt/decrypt for YouTube OAuth tokens
│   ├── supabase_client.py    # queries + retry/backoff + quota counters
│   ├── llm_router.py         # Groq → Gemini → Cohere free-tier fallback
│   ├── telegram_client.py    # approval buttons, reports, error alerts
│   └── niche_engine.py       # Dutch Jinja2 prompt rendering
├── dashboard/
│   └── app.py                # Streamlit: channels, approvals, analytics, LLM usage
├── db/
│   └── schema.sql            # 9 tables: channels, videos, analytics, approval_queue,
│                             #   engagement_queue, job_queue, llm_usage, niche_patterns, api_usage
├── scripts/
│   ├── research_run.py       # 06:00 PKT trend research
│   ├── approval_run.py       # queue ready drafts → pending_approval + Telegram
│   ├── publish_run.py        # 10:00 PKT upload approved videos
│   ├── comments_run.py       # draft comment replies
│   ├── analytics_run.py      # fetch channel metrics
│   ├── report_run.py         # weekly Dutch report via Telegram
│   ├── keepalive.py          # anti-sleep pings
│   ├── setup_oauth.py        # interactive YouTube OAuth per channel (run 10x)
│   └── test_all.py           # smoke-test suite (10 checks)
├── templates/
│   └── prompts/              # Dutch Jinja2 prompts
│       ├── research.j2
│       ├── content.j2
│       ├── seo.j2
│       └── engagement.j2
├── tests/
│   └── test_agents.py        # pytest suite (12 tests, graceful skips)
├── .env.example              # all 12 env vars (empty — fill from free dashboards)
├── requirements.txt
├── runtime.txt               # python-3.11
└── README.md
```

---

## 5. Configuration

All secrets live in **GitHub Secrets** (workflows), **wrangler secrets** (worker), or **Streamlit secrets** (dashboard). Never in the repo.

| # | Variable | Where to get it | Stored in |
|---|---|---|---|
| 1 | `SUPABASE_URL` | [supabase.com](https://supabase.com) → project settings | GitHub Secrets |
| 2 | `SUPABASE_SERVICE_KEY` | Supabase → API → `service_role` (server-side only) | GitHub Secrets |
| 3 | `GROQ_API_KEY` | [console.groq.com](https://console.groq.com) → API keys | GitHub Secrets |
| 4 | `GEMINI_API_KEY` | [aistudio.google.com](https://aistudio.google.com) → Get API key | GitHub Secrets |
| 5 | `COHERE_API_KEY` | [dashboard.cohere.com](https://dashboard.cohere.com) → API keys | GitHub Secrets |
| 6 | `TELEGRAM_BOT_TOKEN` | [@BotFather](https://t.me/BotFather) → `/newbot` | GitHub Secrets + wrangler |
| 7 | `TELEGRAM_ADMIN_CHAT_ID` | Message [@userinfobot](https://t.me/userinfobot) | GitHub Secrets + wrangler |
| 8 | `ENCRYPTION_KEY` | Generated via `core.crypto.generate_key()` (Fernet) | GitHub Secrets |
| 9 | `DASHBOARD_URL` | Your Streamlit Cloud URL after deploy | GitHub Secrets |
| 10 | `DASHBOARD_PASSWORD` | You choose it | GitHub Secrets + Streamlit secrets |
| 11 | `YOUTUBE_CLIENT_SECRETS_DIR` | Google Cloud → OAuth client JSONs (×10 projects, local only) | Local env |
| 12 | `COLAB_TRIGGER_TOKEN` | You choose it (shared secret) | GitHub Secrets + wrangler |

---

## 6. Schedule

All cron times are UTC. PKT = UTC+5.

| Workflow | Cron (UTC) | PKT time | What it does |
|---|---|---|---|
| `daily_research.yml` | `0 1 * * *` | 06:00 daily | Trend research per channel → `scripts/approval_run.py` queues ready drafts for human approval (Telegram buttons) |
| `daily_publish.yml` | `0 5 * * *` | 10:00 daily | Uploads approved + scheduled videos to YouTube (10k units/day guard) |
| `hourly_comments.yml` | `0 */3 * * *` | every 3h | Drafts Dutch comment replies → approval queue (never auto-posts) |
| `keepalive.yml` | `0 8 */3 * *` | every 3 days | Pings Supabase + Streamlit dashboard so free tiers never sleep |
| `weekly_report.yml` | `0 16 * * 0` | 21:00 Sunday | Analytics fetch + Dutch weekly report via Telegram |

The human-in-the-loop flow: `draft` → `pending_approval` (Telegram/dashboard buttons) → `approved` → `scheduled` → `published`. Only approved videos ever reach YouTube.

---

## 7. The 12 free deployment steps

1. **Create the GitHub repo (public).** A public repo gets **unlimited** GitHub Actions minutes — private repos don't. Push this code.
2. **Get your free API keys.** Groq: [console.groq.com](https://console.groq.com) · Gemini: [aistudio.google.com](https://aistudio.google.com) · Cohere: [dashboard.cohere.com](https://dashboard.cohere.com) · Supabase: [supabase.com](https://supabase.com) (new project) · Telegram: [@BotFather](https://t.me/BotFather) → `/newbot`.
3. **Run `db/schema.sql`** in the Supabase SQL editor. This creates all 9 tables. Verify: `select count(*) from channels;` should return 10 after seeding.
4. **Add GitHub Secrets.** Repository → Settings → Secrets and variables → Actions. Add all 12 variables from the table above.
5. **Deploy the worker.** `npx wrangler deploy` in `cloudflare/`, then `npx wrangler secret put` for the 4 secrets: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `SUPABASE_URL`, `TOKEN_ENCRYPTION_KEY`.
6. **Deploy the dashboard** to [Streamlit Community Cloud](https://streamlit.io/cloud) (1 app free). Point it at `dashboard/app.py`; add `DASHBOARD_PASSWORD` + `SUPABASE_URL`/`SUPABASE_SERVICE_KEY` in the app's Secrets.
7. **Create 10 Google Cloud projects** (one per channel), enable **YouTube Data API v3** in each, and create an OAuth 2.0 client per project. This gives you 10 × 10,000 = 100,000 quota units/day.
8. **Run `python scripts/setup_oauth.py`** once per channel (`--channel psy_nl_01` … `--channel psy_nl_10`). Each run completes the OAuth flow, Fernet-encrypts the refresh token, and stores it in the `channels` table.
9. **Enable GitHub Actions.** Repo → Actions → "I understand my workflows, go ahead and enable them".
10. **Manually trigger `daily_research.yml`.** Actions → daily_research → Run workflow. Watch it go green.
11. **Verify the Telegram approval message arrives** on your phone when `publish.yml` first queues a video.
12. **Monitor via the dashboard.** Open your Streamlit URL — you should see all 10 channels with their queues.

---

## 8. Cost breakdown

| Component | Monthly cost |
|---|---|
| GitHub Actions (public repo) | $0.00 |
| Cloudflare Workers | $0.00 |
| Google Colab / Kaggle | $0.00 |
| Supabase | $0.00 |
| Groq + Gemini + Cohere | $0.00 |
| Telegram Bot API | $0.00 |
| Streamlit Community Cloud | $0.00 |
| YouTube Data API v3 (10 projects) | $0.00 |
| **Total** | **$0.00** |

---

## 9. Security

- **Token encryption:** YouTube OAuth refresh tokens are encrypted with Fernet (`TOKEN_ENCRYPTION_KEY`) before storage. Plaintext tokens never touch the database.
- **Secrets hygiene:** secrets live only in GitHub Secrets, `wrangler secret`, and `st.secrets`. Nothing secret is committed — the repo is public by design.
- **GDPR note (Dutch audience):** the system stores no viewer personal data — only your channels' public video metadata and aggregate analytics. Honor deletion requests by deleting the relevant rows; keep data minimization as the default.
- **No fake engagement:** no purchased views, no bots, no comment spam. The engagement agent drafts replies for *your* review only — everything complies with YouTube's Terms of Service.

---

## 10. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `LLMExhaustedError` in workflow logs | All three free LLM quotas hit | Wait for the quota reset (usually hourly/daily); the router already tried Groq → Gemini → Cohere in order. Consider spacing out the content workflow. |
| Supabase "project paused" | Free tier pauses after inactivity | The weekly `analytics.yml` keepalive ping prevents this. If paused, click **Restore** in the Supabase dashboard — data is kept. |
| Streamlit dashboard shows "asleep" | Community Cloud sleeps idle apps | Same keepalive job pings the dashboard URL weekly. First load after sleep takes ~30s. |
| `publish.yml` fails with 401 | YouTube OAuth refresh token expired/revoked | Re-run `python scripts/setup_oauth.py --channel psy_nl_XX` for the affected channel. |
| Telegram approval never arrives | Wrong `TELEGRAM_CHAT_ID` or bot not started | Message your bot once (any text), then re-check the chat ID via [@userinfobot](https://t.me/userinfobot). |
| Worker returns 404 on `/telegram` | Webhook URL not registered with Telegram | Set the webhook: `https://api.telegram.org/bot<TOKEN>/setWebhook?url=<WORKER_URL>/telegram`. |
| Workflow YAML fails to parse | Indentation or `on:` key issue | Validate with `python -c "import yaml; yaml.safe_load(open('file.yml'))"`. |

---

## 11. Free tier deployment checklist

- [ ] GitHub repo created as **public** and code pushed
- [ ] Free API keys obtained: Groq, Gemini, Cohere, Supabase, Telegram bot token
- [ ] `db/schema.sql` executed in Supabase SQL editor (9 tables created)
- [ ] All 12 variables added to GitHub Secrets
- [ ] `wrangler deploy` succeeded; 4 secrets set via `wrangler secret put`
- [ ] Streamlit dashboard deployed with `DASHBOARD_PASSWORD` in app secrets
- [ ] 10 Google Cloud projects created, YouTube Data API v3 enabled in each
- [ ] `python scripts/setup_oauth.py` completed for all 10 channels (`psy_nl_01`…`psy_nl_10`)
- [ ] GitHub Actions enabled on the repo
- [ ] `daily_research.yml` manually triggered and green
- [ ] Workflows green (Actions tab shows passing runs)
- [ ] `GET <WORKER_URL>/health` returns HTTP 200
- [ ] Supabase `channels` table contains 10 rows
- [ ] Telegram approval message received on first publish run
- [ ] Dashboard loads and shows all 10 channels
- [ ] LLM fallback works (temporarily unset `GROQ_API_KEY` → Gemini/Cohere picks up)
- [ ] Total monthly cost verified: **$0.00**

---

## License

MIT — free as in beer, free as in freedom. See [LICENSE](LICENSE).

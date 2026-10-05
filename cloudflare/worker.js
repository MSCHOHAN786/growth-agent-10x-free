// growth-agent-10x-free — Cloudflare Worker
// Routes:
//   GET  /health          -> liveness check
//   POST /webhook/youtube -> YouTube PubSubHubbub notifications -> job_queue
//   POST /telegram        -> Telegram bot updates (approve/reject/edit callbacks)
//   GET  /trigger/colab   -> token-gated: enqueue a colab_content job
// No secrets are hardcoded here. All secrets come from Worker env (wrangler secret put).

function sbHeaders(env) {
  return {
    apikey: env.SUPABASE_SERVICE_KEY,
    Authorization: "Bearer " + env.SUPABASE_SERVICE_KEY,
    "Content-Type": "application/json",
    Prefer: "return=representation",
  };
}

async function sbPost(env, table, row) {
  const res = await fetch(`${env.SUPABASE_URL}/rest/v1/${table}`, {
    method: "POST",
    headers: sbHeaders(env),
    body: JSON.stringify(row),
  });
  const text = await res.text();
  return { ok: res.ok, status: res.status, body: text };
}

async function sbPatch(env, table, matchQuery, patch) {
  const res = await fetch(`${env.SUPABASE_URL}/rest/v1/${table}?${matchQuery}`, {
    method: "PATCH",
    headers: sbHeaders(env),
    body: JSON.stringify(patch),
  });
  const text = await res.text();
  return { ok: res.ok, status: res.status, body: text };
}

async function telegramCall(env, method, payload) {
  const res = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/${method}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return res.ok;
}

function extractYoutubeHints(raw) {
  const hints = {};
  const videoMatch = raw.match(/<yt:videoId>([^<]+)<\/yt:videoId>/);
  const channelMatch = raw.match(/<yt:channelId>([^<]+)<\/yt:channelId>/);
  const titleMatch = raw.match(/<title>([^<]+)<\/title>/);
  const jsonVideo = raw.match(/"videoId"\s*:\s*"([^"]+)"/);
  const jsonChannel = raw.match(/"channelId"\s*:\s*"([^"]+)"/);
  if (videoMatch) hints.video_id = videoMatch[1];
  else if (jsonVideo) hints.video_id = jsonVideo[1];
  if (channelMatch) hints.channel_id = channelMatch[1];
  else if (jsonChannel) hints.channel_id = jsonChannel[1];
  if (titleMatch) hints.title = titleMatch[1];
  return hints;
}

async function handleYoutubeWebhook(env, request) {
  const raw = await request.text();
  const hints = extractYoutubeHints(raw);
  const result = await sbPost(env, "job_queue", {
    job_type: "youtube_notification",
    payload: { raw: raw.slice(0, 2000), hints: hints },
    status: "pending",
  });
  return Response.json(
    { ok: result.ok, queued: result.ok, hints: hints },
    { status: result.ok ? 200 : 502 }
  );
}

async function handleTelegramUpdate(env, request) {
  const update = await request.json().catch(() => ({}));
  const cq = update.callback_query;
  if (!cq || !cq.data) {
    return Response.json({ ok: true, note: "no callback_query" });
  }
  const parts = String(cq.data).split(":");
  const action = parts[0]; // approve | reject | edit
  const kind = parts[1]; // video | comment
  const id = parts[2];
  const decidedAt = new Date().toISOString();
  let table, patch, label;

  if (kind === "comment") {
    table = "engagement_queue";
    patch = {
      status: action === "approve" ? "approved" : action === "reject" ? "rejected" : "needs_edit",
      decided_at: decidedAt,
    };
    label = "comment";
  } else {
    // default: video approval
    table = "approval_queue";
    patch = {
      decision: action === "approve" ? "approved" : action === "reject" ? "rejected" : "needs_edit",
      decided_at: decidedAt,
    };
    label = "video";
  }

  const dbResult = await sbPatch(env, table, `id=eq.${encodeURIComponent(id)}`, patch);
  const text =
    action === "approve"
      ? `✅ ${label} goedgekeurd.`
      : action === "reject"
      ? `❌ ${label} afgewezen.`
      : `✏️ ${label} gemarkeerd voor bewerking.`;

  await telegramCall(env, "answerCallbackQuery", {
    callback_query_id: cq.id,
    text: text,
    show_alert: false,
  });

  if (cq.message && cq.message.chat && cq.message.chat.id) {
    await telegramCall(env, "sendMessage", {
      chat_id: cq.message.chat.id,
      text: text + (dbResult.ok ? "" : " (DB-update mislukt, check logs)"),
    });
  }

  return Response.json({ ok: dbResult.ok, action: action, kind: label, id: id });
}

async function handleColabTrigger(env, request) {
  const token = request.headers.get("x-colab-token");
  if (!token || token !== env.COLAB_TRIGGER_TOKEN) {
    return Response.json({ ok: false, error: "unauthorized" }, { status: 401 });
  }
  const result = await sbPost(env, "job_queue", {
    job_type: "colab_content",
    payload: {},
    status: "pending",
  });
  return Response.json(
    {
      ok: result.ok,
      note: "Colab worker picks up pending jobs",
    },
    { status: result.ok ? 200 : 502 }
  );
}

export default {
  async fetch(request, env, ctx) {
    try {
      const url = new URL(request.url);
      const path = url.pathname;

      if (request.method === "GET" && path === "/health") {
        return Response.json({ ok: true, service: "growth-agent-10x-free" });
      }

      if (request.method === "POST" && path === "/webhook/youtube") {
        return await handleYoutubeWebhook(env, request);
      }

      if (request.method === "POST" && path === "/telegram") {
        return await handleTelegramUpdate(env, request);
      }

      if (request.method === "GET" && path === "/trigger/colab") {
        return await handleColabTrigger(env, request);
      }

      return Response.json({ ok: false, error: "not_found" }, { status: 404 });
    } catch (err) {
      return Response.json(
        { ok: false, error: String(err && err.message ? err.message : err) },
        { status: 500 }
      );
    }
  },
};

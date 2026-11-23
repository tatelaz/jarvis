// La logica del server delle segnalazioni (worker.js la usa; worker.test.mjs la prova senza rete).
// Le segnalazioni di Jarvis (jarvis/feedback.py): arrivano qui e diventano issue in un repository privato di GitHub.
//
// - Chi usa Jarvis non ha bisogno di nessun account: la chiave di GitHub sta solo qui, come segreto del Worker.
// - Non si salva niente: né l'indirizzo IP (serve solo a contare le richieste) né altro. Ciò che arriva è già
//   ripulito da Jarvis; qui si controlla solo che sia fatto bene e non troppo grande.
// - Un "@nome" nel testo farebbe arrivare una notifica a quella persona su GitHub, e "#12" collegherebbe un'altra
//   issue: tutti e due si rendono innocui.
//
// Configurazione: GITHUB_REPO in wrangler.toml ("proprietario/nome"); GITHUB_TOKEN come segreto
// (npx wrangler secret put GITHUB_TOKEN): una chiave a grana fine, solo quel repository, Issues in lettura e scrittura.

export const MAX_BODY = 70_000; // la issue ne tiene 65 536; Jarvis ne manda al massimo 60 000
const KINDS = new Set(["segnalazione", "caduta"]);
const VERSION = /^\d{1,4}\.\d{1,4}\.\d{1,4}$|^\?$/;

export function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status, headers: { "content-type": "application/json; charset=utf-8" },
  });
}

// "@nome" e "#12" con uno spazio invisibile in mezzo: si leggono uguali, ma GitHub non avvisa nessuno
export function defuse(text) {
  return String(text).replace(/@(?=[A-Za-z0-9_-])/g, "@\u200b").replace(/#(?=\d)/g, "#\u200b");
}

export function check(data) {
  if (!data || typeof data !== "object" || Array.isArray(data)) return "serve un oggetto JSON";
  if (!KINDS.has(data.tipo)) return "tipo va scritto segnalazione o caduta";
  for (const field of ["titolo", "testo", "versione"]) {
    if (typeof data[field] !== "string" || !data[field].trim()) return `manca ${field}`;
  }
  if (!VERSION.test(data.versione)) return "versione scritta male";
  if (data.titolo.length > 300) return "titolo troppo lungo";
  return null;
}

export function issueFor(data) {
  const title = defuse(`[${data.tipo}] ${data.titolo.replace(/\s+/g, " ").trim()}`).slice(0, 200);
  const body = `${defuse(data.testo)}\n\n---\nJarvis ${data.versione} · arrivata ${new Date().toISOString().slice(0, 16).replace("T", " ")} UTC`;
  return { title, body, labels: [data.tipo, `v${data.versione}`] };
}

export async function allowed(env, ip) {
  // il limite di Cloudflare (approssimato, per località): se il binding non c'è, si va avanti senza
  for (const [limiter, key] of [[env.PER_PC, `pc:${ip}`], [env.TUTTI, "tutti"]]) {
    if (limiter && !(await limiter.limit({ key })).success) return false;
  }
  return true;
}

// post: la fetch vera nel Worker, una finta nelle prove
export async function handle(request, env, post) {
  const url = new URL(request.url);
  if (url.pathname !== "/segnala") return json({ ok: false, errore: "qui si mandano solo segnalazioni di Jarvis" }, 404);
  if (request.method !== "POST") return json({ ok: false, errore: "si manda con POST" }, 405);
  if (!env.GITHUB_TOKEN || !env.GITHUB_REPO) return json({ ok: false, errore: "server non configurato" }, 503);
  if (!(await allowed(env, request.headers.get("cf-connecting-ip") || "?"))) {
    return json({ ok: false, errore: "troppe segnalazioni insieme: riprova più tardi" }, 429);
  }
  const raw = await request.text();
  if (raw.length > MAX_BODY) return json({ ok: false, errore: "segnalazione troppo grande" }, 413);
  let data;
  try {
    data = JSON.parse(raw);
  } catch {
    return json({ ok: false, errore: "non è JSON" }, 400);
  }
  const why = check(data);
  if (why) return json({ ok: false, errore: why }, 400);
  const r = await post(`https://api.github.com/repos/${env.GITHUB_REPO}/issues`, {
    method: "POST",
    headers: {
      authorization: `Bearer ${env.GITHUB_TOKEN}`, accept: "application/vnd.github+json",
      "x-github-api-version": "2022-11-28", "user-agent": "jarvis-segnalazioni", "content-type": "application/json",
    },
    body: JSON.stringify(issueFor(data)),
  });
  if (r.status !== 201) return json({ ok: false, errore: `GitHub ha risposto ${r.status}` }, 502);
  const issue = await r.json();
  return json({ ok: true, numero: issue.number }, 201);
}

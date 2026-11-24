// Prove del server delle segnalazioni, senza rete: GitHub e i limiti di Cloudflare sono finti.
// node --test servizi/segnalazioni/worker.test.mjs  (le lancia anche tests/test_feedback_server.py)
import assert from "node:assert/strict";
import { test } from "node:test";

import { check, defuse, handle, issueFor, MAX_BODY } from "./logica.js";

const ENV = { GITHUB_TOKEN: "chiave-finta", GITHUB_REPO: "esempio/segnalazioni" };
const GOOD = { tipo: "caduta", titolo: "Caduta: ValueError in jarvis/audio/vad.py:45", testo: "rapporto", versione: "0.1.0" };

function request(body, { method = "POST", path = "/segnala", ip = "203.0.113.7" } = {}) {
  return new Request(`https://jarvis-segnalazioni.esempio.workers.dev${path}`, {
    method, headers: { "cf-connecting-ip": ip }, body: method === "POST" ? body : undefined,
  });
}

function github(status = 201, number = 12) {
  const calls = [];
  const post = async (url, init) => {
    calls.push({ url, init, body: JSON.parse(init.body) });
    return new Response(JSON.stringify({ number }), { status });
  };
  return { calls, post };
}

test("una segnalazione fatta bene diventa una issue nel repository giusto", async () => {
  const gh = github();
  const r = await handle(request(JSON.stringify(GOOD)), ENV, gh.post);
  assert.equal(r.status, 201);
  assert.deepEqual(await r.json(), { ok: true, numero: 12 });
  assert.equal(gh.calls.length, 1);
  assert.equal(gh.calls[0].url, "https://api.github.com/repos/esempio/segnalazioni/issues");
  assert.equal(gh.calls[0].init.headers.authorization, "Bearer chiave-finta");
  assert.equal(gh.calls[0].body.title, "[caduta] Caduta: ValueError in jarvis/audio/vad.py:45");
  assert.deepEqual(gh.calls[0].body.labels, ["caduta", "v0.1.0"]);
  assert.match(gh.calls[0].body.body, /^rapporto\n\n---\nJarvis 0\.1\.0 · arrivata \d{4}-\d\d-\d\d \d\d:\d\d UTC$/);
});

test("@nome e #numero non avvisano nessuno e non collegano altre issue", () => {
  assert.equal(defuse("scrivi a @mario e vedi #12, email a@b.it"), "scrivi a @\u200bmario e vedi #\u200b12, email a@\u200bb.it");
  const issue = issueFor({ ...GOOD, titolo: "ciao @mario #3", testo: "@tutti" });
  assert.ok(!/@[a-z]/.test(issue.title + issue.body) && !/#\d/.test(issue.title));
});

test("si rifiuta ciò che non è una segnalazione di Jarvis, senza chiamare GitHub", async () => {
  const gh = github();
  const cases = [
    [request("{non json"), 400],
    [request(JSON.stringify({ ...GOOD, tipo: "pubblicità" })), 400],
    [request(JSON.stringify({ ...GOOD, testo: "  " })), 400],
    [request(JSON.stringify({ ...GOOD, versione: "1.0; rm -rf" })), 400],
    [request(JSON.stringify([GOOD])), 400],
    [request("x".repeat(MAX_BODY + 1)), 413],
    [request(JSON.stringify(GOOD), { path: "/altro" }), 404],
    [request(null, { method: "GET" }), 405],
  ];
  for (const [req, status] of cases) assert.equal((await handle(req, ENV, gh.post)).status, status);
  assert.equal(gh.calls.length, 0);
  assert.equal(check({ ...GOOD, titolo: "x".repeat(301) }), "titolo troppo lungo");
  assert.equal(check(GOOD), null);
});

test("troppe richieste: 429 prima di leggere e senza chiamare GitHub; senza limiti si va avanti", async () => {
  const seen = [];
  const limiter = (ok) => ({ limit: async ({ key }) => (seen.push(key), { success: ok }) });
  const gh = github();
  const r = await handle(request(JSON.stringify(GOOD)), { ...ENV, PER_PC: limiter(false), TUTTI: limiter(true) }, gh.post);
  assert.equal(r.status, 429);
  assert.deepEqual(seen, ["pc:203.0.113.7"]);
  assert.equal((await handle(request(JSON.stringify(GOOD)), { ...ENV, TUTTI: limiter(false) }, gh.post)).status, 429);
  assert.equal(gh.calls.length, 0);
  assert.equal((await handle(request(JSON.stringify(GOOD)), ENV, gh.post)).status, 201); // binding assenti
});

test("senza chiave o se GitHub dice di no, Jarvis lo sa (e la tiene per dopo)", async () => {
  assert.equal((await handle(request(JSON.stringify(GOOD)), { GITHUB_REPO: "esempio/x" }, github().post)).status, 503);
  const r = await handle(request(JSON.stringify(GOOD)), ENV, github(401).post);
  assert.equal(r.status, 502);
  assert.equal((await r.json()).errore, "GitHub ha risposto 401");
  // il motivo di GitHub arriva fino a Jarvis: così si capisce se è la chiave (visto il 24/11 con una chiave sbagliata)
  const forbidden = async () => new Response(JSON.stringify({ message: "Resource not accessible by personal access token" }),
    { status: 403 });
  const why = await handle(request(JSON.stringify(GOOD)), ENV, forbidden);
  assert.equal((await why.json()).errore, "GitHub ha risposto 403: Resource not accessible by personal access token");
});

test("il testo arriva tale e quale: Jarvis lo ha già ripulito, qui non si aggiunge niente", async () => {
  const gh = github();
  const testo = "### Il PC\n- Jarvis 0.1.0\n```\nERRORE\n```";
  await handle(request(JSON.stringify({ ...GOOD, testo })), ENV, gh.post);
  assert.ok(gh.calls[0].body.body.startsWith(testo));
  assert.ok(!gh.calls[0].body.body.includes("203.0.113.7")); // l'IP serve solo a contare: non finisce nella issue
});

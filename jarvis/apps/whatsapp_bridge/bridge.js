'use strict';
// Ponte tra Jarvis e un WhatsApp Web invisibile (whatsapp-web.js su Chrome senza finestra).
//
// Protocollo a righe JSON, una per messaggio:
//   da Jarvis (stdin):  {"id": 1, "op": "messages", "args": {"chat": "...", "limit": 10}}
//   verso Jarvis (stdout): {"id": 1, "ok": true, "result": ...} oppure {"id": 1, "ok": false, "error": "..."}
//   eventi (stdout): {"event": "qr" | "authenticated" | "ready" | "auth_failure" | "disconnected" | "state" |
//                     "message" | "fatal", "data": ...}
// stdout è solo del protocollo: i log della libreria e di Chrome vanno su stderr.
// Variabili: WA_DATA_DIR (sessione), WA_CHROME (chrome.exe), WA_USER_AGENT, WA_VISIBLE=1 (finestra per il QR).
// Se Jarvis chiude stdin il ponte chiude Chrome ed esce.

const fs = require('fs');
const path = require('path');
const readline = require('readline');

const send = (obj) => process.stdout.write(JSON.stringify(obj) + '\n');
for (const k of ['log', 'info', 'warn', 'debug']) {
    console[k] = (...a) => process.stderr.write(a.map(String).join(' ') + '\n');
}

const { Client, LocalAuth, MessageMedia } = require('whatsapp-web.js');

const visible = process.env.WA_VISIBLE === '1';
const options = {
    authStrategy: new LocalAuth({ dataPath: process.env.WA_DATA_DIR }),
    // la copia della pagina di WhatsApp Web sta coi dati, non nella cartella da cui parte Jarvis (./.wwebjs_cache)
    webVersionCache: { type: 'local', path: path.join(process.env.WA_DATA_DIR || '.', 'versioni') },
    puppeteer: {
        headless: !visible,
        executablePath: process.env.WA_CHROME || undefined,
        args: ['--no-first-run', '--no-default-browser-check', '--disable-extensions', '--mute-audio',
            ...(visible ? ['--window-size=1000,800'] : ['--disable-gpu'])],
    },
};
if (process.env.WA_USER_AGENT) options.userAgent = process.env.WA_USER_AGENT;
const client = new Client(options);
let ready = false;

// WhatsApp Web a settembre 2026 ha rinominato id._serialized in id.$1: si accettano tutti e due
const ser = (id) => (typeof id === 'string' ? id : (id && (id._serialized || id.$1)) || '');
const clip = (s, n) => (s || '').length > n ? s.slice(0, n) + '…' : (s || '');

async function chatById(id) {
    const c = await client.getChatById(id);
    if (!c) throw new Error(`chat non trovata: ${id}`);
    return c;
}

async function msgById(id) {
    const m = await client.getMessageById(id);
    if (!m) throw new Error('messaggio non trovato (forse cancellato)');
    return m;
}

// Chi è l'utente: il numero (wid) e il LID. Nei gruppi WhatsApp ora indica gli autori col LID, e i messaggi che
// l'utente scrive dal telefono arrivano con fromMe falso e il suo LID come autore (27/10: un suo messaggio letto come
// "66039618482205@lid" tra i non letti).
const me = { wid: '', lid: '' };

// "150...939:15@lid" e "150...939@lid" sono la stessa persona: il ":15" è il dispositivo
const bare = (id) => (id || '').replace(/:\d+@/, '@');

function isMe(id) {
    const a = bare(id);
    return !!a && (a === bare(me.wid) || (!!me.lid && a === bare(me.lid)));
}

function mine(m) {
    return !!m.fromMe || isMe(ser(m.author));  // l'autore c'è solo nei gruppi: in una chat a due è sempre l'altro
}

const names = new Map();  // id -> nome detto, una ricerca per persona

async function nameOf(raw) {
    const id = bare(raw);
    if (!id) return '';
    if (names.has(id)) return names.get(id);
    let name = '';
    try {
        const c = await client.getContactById(id);
        name = c.name || c.pushname || c.verifiedName || '';
        if (!name && id.endsWith('@lid')) {  // un LID senza nome: si risale al numero, se WhatsApp lo dice
            const [r] = await client.getContactLidAndPhone([id]);
            if (r && r.pn) {
                const pc = await client.getContactById(r.pn);
                name = pc.name || pc.pushname || '+' + r.pn.split('@')[0];
            }
        }
        if (!name && id.endsWith('@c.us')) name = '+' + id.split('@')[0];
    } catch { /* resta senza nome */ }
    name = name || 'un numero nascosto';  // mai il codice LID: non dice niente a nessuno
    names.set(id, name);
    return name;
}

function chatInfo(c) {
    const last = c.lastMessage;
    return {
        id: ser(c.id), name: c.name || ser(c.id), group: !!c.isGroup, unread: c.unreadCount || 0,
        archived: !!c.archived, pinned: !!c.pinned, muted: !!c.isMuted, ts: c.timestamp || 0,
        last: last ? { from_me: mine(last), type: last.type, body: clip(last.body, 200) } : null,
    };
}

async function msgInfo(m) {
    const d = m._data || {};
    const fromMe = mine(m);
    const q = d.quotedMsg;  // la risposta che cita un messaggio: si dice a chi e cosa
    const qWho = q ? ser(d.quotedParticipant) : '';
    return {
        quoted_text: q ? clip(q.body || q.caption || '', 120) : null,
        quoted_author: q ? (isMe(qWho) ? 'tu' : await nameOf(qWho)) : null,
        id: ser(m.id), chat: m.id && m.id.remote ? ser(m.id.remote) : '', from_me: fromMe, author_id: ser(m.author),
        author: fromMe ? '' : (d.notifyName || await nameOf(ser(m.author) || ser(m.from))), type: m.type,
        body: m.body || '', ts: m.timestamp || 0, has_media: !!m.hasMedia, quoted: !!m.hasQuotedMsg, ack: m.ack,
        duration: m.duration ? Number(m.duration) : null, filename: d.filename || null,
    };
}

function safeName(s) {
    return String(s).replace(/[<>:"/\\|?*\x00-\x1f]/g, '_').slice(0, 120);
}

const ops = {
    status: async () => ({
        ready,
        state: ready ? await client.getState().catch(() => null) : null,
        me: ready && client.info ? { name: client.info.pushname, number: client.info.wid.user } : null,
    }),
    directory: async () => {
        const chats = (await client.getChats()).map(chatInfo);
        const contacts = (await client.getContacts())
            .filter((c) => c.isMyContact && !c.isGroup && c.id && c.id.server === 'c.us')
            .map((c) => ({ id: ser(c.id), name: c.name || c.pushname || c.number, pushname: c.pushname || '',
                number: c.number }));
        return { chats, contacts };
    },
    messages: async ({ chat, limit }) => {
        const c = await chatById(chat);
        const ms = await c.fetchMessages({ limit: Math.max(1, Math.min(Number(limit) || 20, 100)) });
        return { chat: chatInfo(c), messages: await Promise.all(ms.map(msgInfo)) };
    },
    search: async ({ query, chat, limit }) =>
        Promise.all((await client.searchMessages(query, { chatId: chat || undefined, limit: Number(limit) || 20 }))
            .map(msgInfo)),
    number_id: async ({ number }) => {
        const r = await client.getNumberId(String(number));
        return r ? ser(r) : null;
    },
    send: async ({ chat, text, quote }) => {
        const m = await client.sendMessage(chat, text, quote ? { quotedMessageId: quote } : {});
        return { id: ser(m.id) };
    },
    send_file: async ({ chat, file, caption, document }) => {
        const media = MessageMedia.fromFilePath(file);
        const m = await client.sendMessage(chat, media, { caption: caption || undefined, sendMediaAsDocument: !!document });
        return { id: ser(m.id) };
    },
    post_status: async ({ text }) => {
        const m = await client.sendMessage('status@broadcast', text);
        return { id: ser(m.id) };
    },
    react: async ({ msg, emoji }) => { await (await msgById(msg)).react(emoji); return true; },
    forward: async ({ msg, to }) => { await (await msgById(msg)).forward(to); return true; },
    delete_msg: async ({ msg, everyone }) => { await (await msgById(msg)).delete(!!everyone); return true; },
    edit_msg: async ({ msg, text }) => {
        const r = await (await msgById(msg)).edit(text);
        if (!r) throw new Error('WhatsApp non ha accettato la modifica (si può solo entro 15 minuti dall\'invio)');
        return true;
    },
    star: async ({ msg, on }) => { const m = await msgById(msg); await (on === false ? m.unstar() : m.star()); return true; },
    seen: async ({ chat }) => (await chatById(chat)).sendSeen(),
    mark_unread: async ({ chat }) => { await (await chatById(chat)).markUnread(); return true; },
    archive: async ({ chat, on }) => { const c = await chatById(chat); await (on === false ? c.unarchive() : c.archive()); return true; },
    pin: async ({ chat, on }) => { const c = await chatById(chat); return on === false ? c.unpin() : c.pin(); },
    mute: async ({ chat, until }) => {
        const c = await chatById(chat);
        await c.mute(until ? new Date(Number(until) * 1000) : undefined);
        return true;
    },
    unmute: async ({ chat }) => { await (await chatById(chat)).unmute(); return true; },
    clear: async ({ chat }) => (await chatById(chat)).clearMessages(),
    delete_chat: async ({ chat }) => (await chatById(chat)).delete(),
    info: async ({ chat }) => {
        const c = await chatById(chat);
        if (c.isGroup) {
            return {
                group: true, name: c.name, description: c.description || '', owner: ser(c.owner),
                created: c.createdAt ? Math.round(c.createdAt.getTime() / 1000) : null,
                participants: (c.participants || []).map((p) => ({ id: ser(p.id), admin: !!(p.isAdmin || p.isSuperAdmin) })),
            };
        }
        const ct = await c.getContact();
        return {
            group: false, name: ct.name || ct.pushname || ct.number, pushname: ct.pushname || '', number: ct.number,
            about: await ct.getAbout().catch(() => null), blocked: !!ct.isBlocked, business: !!ct.isBusiness,
            my_contact: !!ct.isMyContact,
        };
    },
    block: async ({ chat, on }) => {
        const ct = await client.getContactById(chat);
        return on === false ? ct.unblock() : ct.block();
    },
    group_create: async ({ name, participants }) => {
        const r = await client.createGroup(name, participants);
        if (typeof r === 'string') throw new Error(r);
        return { id: ser(r.gid), title: r.title };
    },
    group_members: async ({ chat, participants, action }) => {
        const c = await chatById(chat);
        if (!c.isGroup) throw new Error('non è un gruppo');
        const fn = { add: 'addParticipants', remove: 'removeParticipants', promote: 'promoteParticipants',
            demote: 'demoteParticipants' }[action];
        if (!fn) throw new Error(`azione sui membri sconosciuta: ${action}`);
        return c[fn](participants);
    },
    group_subject: async ({ chat, text }) => (await chatById(chat)).setSubject(text),
    group_description: async ({ chat, text }) => (await chatById(chat)).setDescription(text),
    group_invite: async ({ chat }) => 'https://chat.whatsapp.com/' + await (await chatById(chat)).getInviteCode(),
    group_leave: async ({ chat }) => { await (await chatById(chat)).leave(); return true; },
    set_about: async ({ text }) => { await client.setStatus(text); return true; },
    set_name: async ({ name }) => client.setDisplayName(name),
    download: async ({ msg, dir }) => {
        const m = await msgById(msg);
        if (!m.hasMedia) throw new Error('quel messaggio non ha allegati');
        const media = await m.downloadMedia();
        if (!media) throw new Error('allegato non più disponibile sul telefono');
        const ext = ((media.mimetype || '').split('/')[1] || 'bin').split(';')[0];
        fs.mkdirSync(dir, { recursive: true });
        let file = path.join(dir, safeName(media.filename || `whatsapp_${m.timestamp || Date.now()}.${ext}`));
        for (let i = 2; fs.existsSync(file); i++) {
            const p = path.parse(file);
            file = path.join(p.dir, `${p.name.replace(/ \(\d+\)$/, '')} (${i})${p.ext}`);
        }
        const data = Buffer.from(media.data, 'base64');
        fs.writeFileSync(file, data);
        return { file, mimetype: media.mimetype, size: data.length };
    },
    logout: async () => { await client.logout(); return true; },
};

const always = new Set(['status']);  // le altre solo a WhatsApp pronto

readline.createInterface({ input: process.stdin }).on('line', async (line) => {
    let req;
    try { req = JSON.parse(line); } catch { return; }
    const fn = ops[req.op];
    if (!fn) return send({ id: req.id, ok: false, error: `operazione sconosciuta: ${req.op}` });
    if (!ready && !always.has(req.op)) return send({ id: req.id, ok: false, error: 'WhatsApp non è ancora pronto' });
    try {
        const result = await fn(req.args || {});
        send({ id: req.id, ok: true, result: result === undefined ? null : result });
    } catch (e) {
        send({ id: req.id, ok: false, error: String((e && e.message) || e) });
    }
}).on('close', async () => {  // Jarvis se n'è andato: si chiude anche Chrome
    try { await client.destroy(); } catch { /* già chiuso */ }
    process.exit(0);
});

client.on('qr', (qr) => send({ event: 'qr', data: qr }));
client.on('authenticated', () => send({ event: 'authenticated' }));
client.on('loading_screen', (percent) => send({ event: 'loading', data: percent }));
client.on('auth_failure', (m) => send({ event: 'auth_failure', data: String(m) }));
client.on('ready', async () => {
    me.wid = ser(client.info && client.info.wid);
    try {
        const [r] = await client.getContactLidAndPhone([me.wid]);
        me.lid = (r && r.lid) || '';
    } catch { /* senza LID si riconoscono solo i messaggi scritti da qui */ }
    ready = true;
    send({ event: 'ready', data: client.info ? { name: client.info.pushname, number: client.info.wid.user,
        lid: me.lid } : null });
});
client.on('disconnected', (r) => { ready = false; send({ event: 'disconnected', data: String(r) }); });
client.on('change_state', (s) => send({ event: 'state', data: String(s) }));
client.on('message', async (m) => {  // solo i messaggi in arrivo, non i propri né gli stati
    try {
        if (mine(m) || m.isStatus) return;  // anche quelli scritti dall'utente sul telefono
        const c = await m.getChat();
        send({ event: 'message', data: { ...(await msgInfo(m)), body: clip(m.body, 300), chat_name: c.name,
            group: !!c.isGroup, muted: !!c.isMuted } });
    } catch { /* un avviso perso non ferma il ponte */ }
});

client.initialize().catch((e) => {
    send({ event: 'fatal', data: String((e && e.message) || e) });
    process.exit(1);
});

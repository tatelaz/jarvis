"""Un ponte di WhatsApp finto che parla lo stesso protocollo di jarvis/apps/whatsapp_bridge/bridge.js.

FAKE_MODE: ready (si collega subito), qr (chiede il QR), fatal (non parte). Operazioni: status, echo, fail, slow,
notify (manda un evento messaggio), die (cade a metà richiesta). Scrive anche rumore su stdout e stderr, come Chrome.
"""

import json
import os
import sys
import time


def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


for stream in (sys.stdin, sys.stdout, sys.stderr):  # come Node: sempre UTF-8, qualunque sia la codifica di Windows
    stream.reconfigure(encoding="utf-8")
mode = os.environ.get("FAKE_MODE", "ready")
sys.stderr.write("rumore di Chrome su stderr\n")
sys.stdout.write("riga che non è JSON\n")
sys.stdout.flush()
if mode == "fatal":
    send({"event": "fatal", "data": "Chrome non trovato"})
    sys.exit(1)
if mode == "qr":
    send({"event": "qr", "data": "2@codice"})
else:
    send({"event": "authenticated"})
    send({"event": "ready", "data": {"name": "Andrea", "number": "393331112222", "visible": os.environ.get("WA_VISIBLE")}})

for line in sys.stdin:
    req = json.loads(line)
    op, args, rid = req["op"], req.get("args") or {}, req["id"]
    if op == "status":
        send({"id": rid, "ok": True, "result": {"ready": mode == "ready"}})
    elif op == "echo":
        send({"id": rid, "ok": True, "result": args})
    elif op == "fail":
        send({"id": rid, "ok": False, "error": "chat non trovata: x"})
    elif op == "slow":
        time.sleep(3)
        send({"id": rid, "ok": True, "result": "tardi"})
    elif op == "notify":
        send({"event": "message", "data": {"author": "Luca", "chat_name": "Luca", "body": "ciao", "group": False}})
        send({"id": rid, "ok": True, "result": None})
    elif op == "die":
        sys.stderr.write("TypeError: qualcosa si è rotto\n")
        sys.stderr.flush()
        sys.exit(3)
# stdin chiuso da Jarvis: si esce puliti

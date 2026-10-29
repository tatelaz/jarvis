"""Politica di sicurezza per le azioni dei sotto-agenti.

Principio: lavorano liberi dove non possono fare danni irreparabili, e chiedono a voce prima di
tutto ciò che non si può annullare o che esce dal PC (cancellazioni massive, registro di sistema,
spegnimenti, invii, pubblicazioni, download eseguiti al volo, scritture fuori dalle cartelle di
lavoro, acquisti e invii nel browser). Una regola non riconosciuta non blocca il lavoro: la
classificazione è prudente solo dove serve.

È una rete di sicurezza in più, non una garanzia (revisione del 27/10): le espressioni regolari si possono
aggirare e i clic con mouse e tastiera sul desktop non dicono cosa colpiscono. Il primo argine resta il
sotto-agente stesso, che ha l'ordine di chiedere conferma per tutto ciò che è irreversibile.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

READ_ONLY_TOOLS = {"Read", "Glob", "Grep", "LS", "WebSearch", "WebFetch", "TodoWrite", "NotebookRead", "BashOutput",
                   "ListMcpResourcesTool", "ReadMcpResourceTool", "Task", "Agent", "Skill", "ToolSearch", "Monitor"}
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
SHELL_TOOLS = {"Bash", "PowerShell"}
# caricare file del PC su un sito: è la via più semplice per farli uscire, si chiede sempre
UPLOAD_TOOLS = {"mcp__browser__browser_file_upload", "mcp__claude-in-chrome__file_upload"}

# comandi distruttivi, irreversibili o che toccano il sistema: chiedere sempre
DANGEROUS = [
    (r"\brm\s+(-[a-z]*r[a-z]*f|-[a-z]*f[a-z]*r)\b", "cancellazione ricorsiva forzata"),
    (r"\brm\s+(-[a-z]*r|--recursive)\b", "cancellazione ricorsiva"),  # anche senza -f (27/10)
    (r"\brmdir\b.*\s/s\b|\brd\s+/s\b", "cancellazione di cartelle con tutto il contenuto"),
    (r"\bdel\b.*\s/[sq]\b|\berase\b", "cancellazione di file in blocco"),
    # Remove-Item e i suoi alias, con -Recurse o una sua abbreviazione (-r, -rec...)
    (r"\b(remove-item|ri|rm|del|rd|rmdir)\b[^|;\n]*\s-r[a-z]*\b", "cancellazione ricorsiva"),
    # cancellazioni scritte in un linguaggio di programmazione (python -c, node -e, script)
    (r"shutil\.rmtree|\brmtree\(|os\.(remove|unlink|rmdir|removedirs)\(|\.unlink\(|\.rmdir\(|send2trash|"
     r"\.(rm|rmdir|unlink)(sync)?\(", "cancellazione di file da codice"),  # fs.rmSync, require('fs').rm(...)
    (r"\bformat(-volume)?\b\s+[a-z]:|\bdiskpart\b|\bclear-disk\b|\binitialize-disk\b", "operazioni sul disco"),
    (r"\breg(\.exe)?\s+(delete|add|import)\b|set-itemproperty\s+.*hk(lm|cu)|new-itemproperty\s+.*hk(lm|cu)|"
     r"remove-itemproperty", "modifica del registro di sistema"),
    (r"\bbcdedit\b|\bshutdown\b|\bstop-computer\b|\brestart-computer\b", "spegnimento o avvio del sistema"),
    (r"\bcipher\s+/w\b|\bsdelete\b", "cancellazione sicura irreversibile"),
    (r"\btakeown\b|\bicacls\b.*\s/(grant|deny|setowner)", "modifica dei permessi"),
    (r"\bnet\s+user\b|\bnet\s+localgroup\b|new-localuser|add-localgroupmember", "account utente"),
    (r"\bsc(\.exe)?\s+(delete|config|create)\b|\bset-service\b|\bnew-service\b", "servizi di Windows"),
    (r"\bschtasks\b.*\s/create\b|register-scheduledtask", "attività pianificate"),
    (r"set-executionpolicy|set-mppreference|netsh\s+advfirewall|disable-netadapter", "impostazioni di sicurezza"),
    (r"\bgit\s+push\b.*(--force|-f\b)|\bgit\s+reset\s+--hard\b|\bgit\s+clean\s+-[a-z]*f", "operazione git distruttiva"),
    (r"\bgit\s+push\b", "pubblicazione su un repository remoto"),
    (r"\b(npm|pnpm|yarn)\s+publish\b|\btwine\s+upload\b|\bgh\s+(release|repo)\s+(create|delete)\b",
     "pubblicazione di un pacchetto o repository"),
    (r"(iwr|invoke-webrequest|curl|wget)\b.*\|\s*(iex|invoke-expression|sh|bash|python)\b|\biex\s*\(", "esecuzione di codice scaricato"),
    (r"send-mailmessage|\bsmtp\b", "invio di email"),
    (r"\bdrop\s+(table|database)\b|\btruncate\s+table\b", "cancellazione di dati da un database"),
    (r"stop-process\b.*-force|\btaskkill\b.*\s/f\b", "chiusura forzata di programmi"),
]

# script lanciati da riga di comando: se il file esiste se ne controlla il contenuto (scriverlo e poi
# eseguirlo era il modo più semplice per aggirare i controlli sui comandi)
SCRIPT = re.compile(r"""["']?([^\s"'|;&<>]+\.(?:py|ps1|psm1|bat|cmd|js|mjs|cjs|sh))["']?""", re.I)
MAX_SCRIPT_BYTES = 512 * 1024

# nel browser: clic o compilazioni che comprano, pagano, inviano, pubblicano o cancellano
CONSEQUENTIAL = re.compile(
    r"acquist|compra|ordin[ae]|\bordine\b|pag(a|are|amento)\b|checkout|\bcassa\b|bonifico|abbonat|iscri(viti|zione)|"
    r"invi(a|are)\b|spedisc|pubblic|\bposta\b|elimin|cancella\b|cancellare|\bbuy\b|purchase|\border\b|place order|"
    r"\bpay\b|payment|subscribe|\bsend\b|submit|publish|\bpost\b|delete|transfer", re.I)
# strumenti del browser che agiscono sulla pagina: (nome, campi che descrivono l'azione)
BROWSER_ACTIONS = {
    # solo la descrizione dell'effetto: il testo digitato può contenere "pagamento" senza pagare niente
    "mcp__claude-in-chrome__computer": ("action_summary",),
    "mcp__claude-in-chrome__form_input": ("action_summary",),
    "mcp__browser__browser_click": ("element",),
    "mcp__browser__browser_type": ("element",),
    "mcp__browser__browser_press_key": ("element",),
    "mcp__browser__browser_select_option": ("element",),
}
# codice eseguito nella pagina con gli accessi dell'utente: può fare qualunque cosa (anche comprare)
USER_BROWSER_CODE = {"mcp__claude-in-chrome__javascript_tool"}


@dataclass
class Verdict:
    allow: bool
    ask: bool = False
    reason: str = ""


def _inside(path: Path, roots: list[Path]) -> bool:
    try:
        rp = path.resolve()
    except OSError:
        return False
    return any(rp == r or r in rp.parents for r in roots)


class SafetyPolicy:
    def __init__(self, write_roots: list[Path], confirm: bool = True) -> None:
        self.write_roots = [Path(r).resolve() for r in write_roots]
        self.confirm = confirm

    def check(self, tool: str, args: dict, cwd: Path | None = None) -> Verdict:
        if not self.confirm:
            return Verdict(True)
        if tool in READ_ONLY_TOOLS or tool.startswith("mcp__jarvis"):
            return Verdict(True)
        if tool in USER_BROWSER_CODE:
            return Verdict(False, ask=True, reason=f"eseguire codice nel tuo browser, con i tuoi accessi: "
                                                   f"{str(args.get('text', ''))[:160]}")
        if tool in BROWSER_ACTIONS or tool == "mcp__claude-in-chrome__browser_batch":
            what = self._browser_consequence(tool, args)
            if what:
                return Verdict(False, ask=True, reason=f"nel browser: {what}")
            return Verdict(True)
        if tool in WRITE_TOOLS:
            target = args.get("file_path") or args.get("notebook_path") or ""
            if not target:
                return Verdict(True)
            if _inside(Path(target), self.write_roots):
                return Verdict(True)
            return Verdict(False, ask=True, reason=f"scrivere fuori dalle cartelle di lavoro: {target}")
        if tool in UPLOAD_TOOLS:
            files = args.get("paths") or args.get("files") or args.get("path") or args
            return Verdict(False, ask=True, reason=f"caricare file su un sito web: {files}")
        if tool in SHELL_TOOLS:
            cmd = str(args.get("command", ""))
            why = dangerous(cmd)
            if why:
                return Verdict(False, ask=True, reason=f"{why}: {cmd[:200]}")
            for script, content in self._scripts(cmd, cwd):
                why = dangerous(content)
                if why:
                    return Verdict(False, ask=True, reason=f"lo script {script.name} contiene {why}: {cmd[:160]}")
            return Verdict(True)
        return Verdict(True)

    @staticmethod
    def _browser_consequence(tool: str, args: dict) -> str | None:
        """La descrizione dell'azione (es. "Clicca Acquista ora") dice se compra, paga, invia o cancella."""
        steps = ([(s.get("name", ""), s.get("input") or {}) for s in args.get("actions") or [] if isinstance(s, dict)]
                 if tool == "mcp__claude-in-chrome__browser_batch" else [(tool, args)])
        for name, a in steps:
            fields = BROWSER_ACTIONS.get(name if name.startswith("mcp__") else f"mcp__claude-in-chrome__{name}", ())
            text = " ".join(str(a.get(f, "")) for f in fields)
            if CONSEQUENTIAL.search(text):
                return text.strip()[:160]
        return None

    @staticmethod
    def _scripts(cmd: str, cwd: Path | None) -> list[tuple[Path, str]]:
        out = []
        for m in SCRIPT.finditer(cmd):
            p = Path(m.group(1))
            if not p.is_absolute() and cwd is not None:
                p = Path(cwd) / p
            try:
                if p.is_file() and p.stat().st_size <= MAX_SCRIPT_BYTES:
                    out.append((p, p.read_text(encoding="utf-8", errors="replace")))
            except OSError:
                continue
        return out


def dangerous(text: str) -> str | None:
    low = text.lower()
    return next((why for pattern, why in DANGEROUS if re.search(pattern, low)), None)

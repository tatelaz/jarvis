"""Dal testo del cervello al testo da pronunciare.

Due compiti:
1. `SentenceChunker`: spezza il testo che arriva in streaming in pezzi pronunciabili. Il primo
   pezzo esce il prima possibile (anche a una virgola), così Jarvis inizia a parlare mentre il
   modello sta ancora scrivendo; i successivi seguono le frasi.
2. `speakable`: toglie ciò che non si legge ad alta voce (markdown, codice, link, emoji) e rende
   leggibili numeri, orari, percentuali, valute e sigle in italiano.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable

from num2words import num2words

# ---------------------------------------------------------------- numeri


def _int_it(n: int) -> str:
    s = num2words(n, lang="it")
    # forme standard: "centuno", "centotto" restano; num2words scrive "centouno"/"centootto"
    return s.replace("centouno", "centuno").replace("centootto", "centotto")


def number_it(token: str) -> str:
    """'4.892' -> quattromilaottocentonovantadue, '3,5' -> tre virgola cinque."""
    t = token.strip()
    neg = t.startswith("-")
    t = t.lstrip("+-")
    if re.fullmatch(r"\d{1,3}(\.\d{3})+", t):  # migliaia all'italiana: 4.892 / 1.250.000
        t = t.replace(".", "")
    if re.fullmatch(r"\d+([.,]\d+)", t):
        ip, dp = re.split(r"[.,]", t)
        dec = "zero " * (len(dp) - len(dp.lstrip("0"))) + (_int_it(int(dp)) if dp.strip("0") else "")
        out = f"{_int_it(int(ip))} virgola {dec.strip() or 'zero'}"
    elif t.isdigit():
        out = _int_it(int(t))
    else:
        return token
    return ("meno " if neg else "") + out


_MONTHS = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto",
           "settembre", "ottobre", "novembre", "dicembre"]

_UNITS = {
    "km/h": "chilometri orari", "km": "chilometri", "m": "metri", "cm": "centimetri", "mm": "millimetri",
    "kg": "chili", "g": "grammi", "°C": "gradi", "°": "gradi", "€": "euro", "$": "dollari", "£": "sterline",
    "%": "per cento", "GB": "gigabyte", "MB": "megabyte", "KB": "kilobyte", "TB": "terabyte",
    "GHz": "gigahertz", "MHz": "megahertz", "Hz": "hertz", "ms": "millisecondi", "h": "ore",
    "min": "minuti", "s": "secondi", "W": "watt", "kW": "kilowatt", "V": "volt", "fps": "fotogrammi al secondo",
}

# Sigle che vanno pronunciate lettera per lettera, con i nomi italiani delle lettere.
_LETTERS = {"A": "a", "B": "bi", "C": "ci", "D": "di", "E": "e", "F": "effe", "G": "gi", "H": "acca",
            "I": "i", "J": "i lunga", "K": "cappa", "L": "elle", "M": "emme", "N": "enne", "O": "o",
            "P": "pi", "Q": "cu", "R": "erre", "S": "esse", "T": "ti", "U": "u", "V": "vu",
            "W": "doppia vu", "X": "ics", "Y": "ipsilon", "Z": "zeta"}
# Sigle che si leggono come parole (o che il modello pronuncia già bene): non vanno spelate.
_WORD_ACRONYMS = {"NASA", "UNESCO", "FIAT", "ANSA", "RAM", "ROM", "LED", "WIFI", "OK", "ISTAT", "INPS",
                  "IRPEF", "COVID", "LASER", "RADAR", "NATO", "ONU", "UEFA", "FIFA", "IKEA", "AI", "IA"}
_FIXED = {"AI": "a i", "IA": "i a", "OK": "okay", "WIFI": "uaifai", "Wi-Fi": "uaifai", "wifi": "uaifai",
          "e-mail": "email", "PC": "pi ci", "SSD": "esse esse di", "GPU": "gi pi u", "CPU": "ci pi u",
          "USB": "u esse bi", "URL": "u erre elle", "PDF": "pi di effe", "API": "a pi i", "TV": "ti vu",
          "vs": "contro", "ecc.": "eccetera", "etc.": "eccetera", "es.": "per esempio",
          "Sig.": "signor", "Dott.": "dottor", "Ing.": "ingegner", "Prof.": "professor"}


def spell(acronym: str) -> str:
    return " ".join(_LETTERS.get(c, c) for c in acronym.upper())


_EMOJI = re.compile("[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF‍️]")
_URL = re.compile(r"\b(?:https?://|www\.)[^\s)>\]]+", re.I)
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_CODE_BLOCK = re.compile(r"```.*?(```|$)", re.S)
_INLINE_CODE = re.compile(r"`([^`]*)`")
# "/" apre un percorso solo se non segue una lettera o una cifra: "5/10" e "km/h" non sono percorsi
_PATH = re.compile(r"(?:[A-Za-z]:\\|\\\\|(?<![\w.])~?/)[^\s,;]+")


def _url_to_speech(m: re.Match) -> str:
    host = re.sub(r"^(https?://)?(www\.)?", "", m.group(0), flags=re.I).split("/")[0]
    return host.replace(".", " punto ")


def _time(m: re.Match) -> str:
    h, mi = int(m.group(1)), int(m.group(2))
    hs = "mezzanotte" if h == 0 else ("l'una" if h == 1 else _int_it(h))
    if mi == 0:
        return hs
    if mi == 30:
        return f"{hs} e mezza"
    if mi == 15:
        return f"{hs} e un quarto"
    return f"{hs} e {_int_it(mi)}"


def _date(m: re.Match) -> str:
    d, mo = int(m.group(1)), int(m.group(2))
    y = m.group(3)
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return m.group(0)
    day = "primo" if d == 1 else _int_it(d)
    out = f"{day} {_MONTHS[mo - 1]}"
    if y:
        yy = int(y)
        out += f" {_int_it(yy + 2000 if yy < 100 else yy)}"
    return out


def _unit(m: re.Match) -> str:
    num, unit = m.group(1), m.group(2)
    words = number_it(num)
    if unit in ("€", "$", "£") and words == "uno":
        words = "un"
    name = _UNITS[unit]
    if words in ("uno", "un") and unit in ("km", "m", "cm", "mm", "kg", "g", "h", "min", "s", "ms"):
        name = {"km": "chilometro", "m": "metro", "cm": "centimetro", "mm": "millimetro", "kg": "chilo",
                "g": "grammo", "h": "ora", "min": "minuto", "s": "secondo", "ms": "millisecondo"}[unit]
        words = "un"
    elif words == "uno" and unit in ("°C", "°"):
        name = "grado"
    return f"{words} {name}"


_UNIT_RE = re.compile(
    r"(?<![\w.,])([+-]?\d+(?:[.,]\d+)?)\s?(" + "|".join(re.escape(u) for u in sorted(_UNITS, key=len, reverse=True))
    + r")(?![\w])"
)
_CURRENCY_PREFIX = re.compile(r"([€$£])\s?(\d+(?:[.,]\d+)?)")


def speakable(text: str) -> str:
    """Testo pronto per il TTS. Il contenuto non leggibile viene tolto, non inventato."""
    t = unicodedata.normalize("NFC", text)
    t = _CODE_BLOCK.sub(" ", t)
    t = _MD_LINK.sub(r"\1", t)
    t = _URL.sub(_url_to_speech, t)
    t = _INLINE_CODE.sub(r"\1", t)
    t = _PATH.sub(lambda m: m.group(0).rstrip("\\/").replace("\\", "/").split("/")[-1], t)
    t = _EMOJI.sub("", t)
    t = re.sub(r"^\s{0,3}#{1,6}\s*", "", t, flags=re.M)  # titoli
    t = re.sub(r"^\s*(?:[-*+•]|\d+[.)])\s+", "", t, flags=re.M)  # elenchi
    t = re.sub(r"^\s*>\s?", "", t, flags=re.M)  # citazioni
    t = re.sub(r"(\*\*|__|\*|_|~~)(.+?)\1", r"\2", t)  # grassetto/corsivo
    t = re.sub(r"\|", ", ", t)  # tabelle
    t = re.sub(r"[ \t]*\n+[ \t]*", ". ", t)
    t = re.sub(r"\.\s*\.(?!\.)", ".", t)
    # forme fisse e sigle
    for k, v in _FIXED.items():
        t = re.sub(rf"(?<![\w]){re.escape(k)}(?![\w])", v, t)
    t = _CURRENCY_PREFIX.sub(lambda m: f"{m.group(2)} {m.group(1)}", t)
    t = re.sub(r"\b([01]?\d|2[0-3])[:.]([0-5]\d)\b(?![.,]\d)", _time, t)
    t = re.sub(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2}|\d{4}))?\b", _date, t)
    t = _UNIT_RE.sub(_unit, t)
    t = re.sub(r"(?<![\w])[+-]?\d{1,3}(?:\.\d{3})+(?:,\d+)?(?![\w])|(?<![\w.])[+-]?\d+(?:[.,]\d+)?(?![\w])",
               lambda m: number_it(m.group(0)), t)
    t = re.sub(r"\b([A-Z]{2,5})\b", lambda m: m.group(1) if m.group(1) in _WORD_ACRONYMS else spell(m.group(1)), t)
    t = t.replace("&", " e ").replace("+", " più ").replace("=", " uguale ").replace("→", ", poi ")
    t = re.sub(r"[\"“”«»<>{}\[\]^]", "", t)
    t = re.sub(r"\s+([,.;:!?])", r"\1", t)
    t = re.sub(r"([,;:])(?=\S)", r"\1 ", t)
    t = re.sub(r"\s{2,}", " ", t).strip(" ,;")
    return t


# ---------------------------------------------------------------- segmentazione

_ABBREV = {"sig", "sigg", "dott", "dr", "ing", "prof", "avv", "geom", "rag", "ecc", "etc", "es", "pag", "cap",
           "art", "vol", "n", "nr", "tel", "ca", "c.a", "st", "mr", "mrs", "ms", "vs", "fig", "max", "min",
           "p.es", "cfr", "gen", "feb", "mar", "apr", "mag", "giu", "lug", "ago", "set", "ott", "nov", "dic"}
_SENT_END = re.compile(r"[.!?…]+[\"')\]»]*(?=\s|$)")
_CLAUSE_END = re.compile(r"[,;:—–](?=\s)")


def _is_abbrev(text: str, idx: int) -> bool:
    """Vero se il punto in posizione idx chiude un'abbreviazione o un numero, non una frase."""
    if text[idx] != ".":
        return False
    m = re.search(r"([\w.]+)$", text[:idx])
    if not m:
        return False
    word = m.group(1).lower()
    if word in _ABBREV or (len(word) == 1 and word.isalpha() and word.isupper()):
        return True
    return bool(re.fullmatch(r"\d+", word)) and idx + 1 < len(text) and text[idx + 1 : idx + 2].isdigit()


class SentenceChunker:
    """Riceve testo a pezzi (streaming) ed emette porzioni da far pronunciare."""

    def __init__(self, first_min_chars: int = 12, first_max_chars: int = 60, min_chars: int = 30,
                 max_chars: int = 220, instant: Callable[[str], bool] | None = None,
                 clause_min_chars: int = 24) -> None:
        # Dopo il primo, i pezzi escono anche alle virgole (almeno clause_min caratteri e 4 parole): è lo
        # speaker a riunirli quando c'è tempo, così una frase lunga non lascia mai un buco a metà.
        self.clause_min = clause_min_chars
        # instant(frase): vero se la frase ha già l'audio pronto (cache). Come primo pezzo esce da sola
        # anche se corta ("Subito."): suona all'istante mentre si sintetizza il resto.
        self.instant = instant
        self.first_min = first_min_chars
        self.first_max = first_max_chars
        self.min = min_chars
        self.max = max_chars
        self._buf = ""
        self._emitted = 0
        self._in_code = False

    def _strip_code(self, piece: str) -> str:
        """Elimina i blocchi di codice anche quando arrivano spezzati tra un pezzo e l'altro."""
        out = []
        while piece:
            i = piece.find("```")
            if i < 0:
                if not self._in_code:
                    out.append(piece)
                break
            if not self._in_code:
                out.append(piece[:i])
            self._in_code = not self._in_code
            piece = piece[i + 3 :]
        return "".join(out)

    def feed(self, piece: str) -> list[str]:
        self._buf += self._strip_code(piece)
        return self._drain(final=False)

    def flush(self) -> list[str]:
        out = self._drain(final=True)
        rest = self._buf.strip()
        self._buf = ""
        if rest and speakable(rest):
            out.append(rest)
            self._emitted += 1
        return out

    def _drain(self, final: bool) -> list[str]:
        out: list[str] = []
        while True:
            cut = self._find_cut(final)
            if cut is None:
                return out
            chunk, self._buf = self._buf[:cut].strip(), self._buf[cut:]
            if chunk and speakable(chunk):
                out.append(chunk)
                self._emitted += 1

    @staticmethod
    def _tail_ok(rest: str, final: bool, min_tail: int = 15) -> bool:
        """Niente moncherini ("A sua disposizione," + "signore."): dopo la virgola devono restare almeno
        min_tail caratteri prima del punto. Se il punto non è ancora arrivato si aspetta di vederne abbastanza."""
        stop = _SENT_END.search(rest)
        tail = rest[: stop.end()] if stop else rest
        return bool(stop or final or len(rest.strip()) >= min_tail) and len(tail.strip()) >= min_tail

    def _find_cut(self, final: bool) -> int | None:
        buf = self._buf
        first = self._emitted == 0
        lo = self.first_min if first else self.min
        sentence, pending = None, False
        for m in _SENT_END.finditer(buf):
            end = m.end()
            if _is_abbrev(buf, m.start()):
                continue
            if len(buf[:end].strip()) < lo and not (first and self.instant and self.instant(buf[:end].strip())):
                continue
            if end == len(buf) and not final:
                pending = True  # la frase potrebbe continuare ("3." di "3.5")
            else:
                sentence = end
            break
        # una pausa naturale (virgola, punto e virgola, due punti) dopo qualche parola
        clause = None
        for m in _CLAUSE_END.finditer(buf):
            piece = buf[: m.end()]
            if first:
                long_enough = self.first_min <= m.end() <= self.first_max and len(piece.split()) >= 3
            else:
                long_enough = len(piece.strip()) >= self.clause_min and len(piece.split()) >= 4
            if long_enough and self._tail_ok(buf[m.end():], final):
                clause = m.end()
                break
        cuts = [c for c in (sentence, clause) if c is not None]
        if cuts:
            return min(cuts)  # il taglio più vicino: si parte prima
        if pending:
            return None
        if len(buf) > self.max:
            window = buf[: self.max]
            cands = [m.end() for m in _CLAUSE_END.finditer(window)]
            if cands:
                return cands[-1]
            sp = window.rfind(" ")
            return sp if sp > self.min else self.max
        return None

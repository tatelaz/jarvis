"""L'aspetto della finestra come lo vede la pagina: tema, colore d'accento, grandezza del testo, effetti.

Jarvis cambia queste impostazioni a voce ("metti la grafica cinema", "fai l'accento rosso") e la finestra
le applica subito, senza ricaricare. I colori si dicono in italiano: qui diventano codici esadecimali, così
la pagina riceve sempre un valore valido e c'è una sola tabella dei nomi.
"""

from __future__ import annotations

import re

from ..config import Config

THEMES = {"mark3": "Mark III: uno strumento al centro, bianco su nero, la voce azzurra",
          "cinema": "Cinema: una linea di luce che vibra con la voce, sottotitoli da film"}

COLORS = {
    "azzurro": "#8fd8ff", "blu": "#5b8cff", "ciano": "#5ff2ff", "turchese": "#3fe0c5", "verde": "#6fe39a",
    "giallo": "#ffe066", "oro": "#e6b85c", "ambra": "#ffb547", "arancione": "#ff7a2f", "rosso": "#ff4d4d",
    "rosa": "#ff7eb6", "viola": "#a98bff", "lilla": "#c7a6ff", "bianco": "#f3f2ee", "argento": "#c9ced6",
}
_HEX = re.compile(r"#?([0-9a-fA-F]{6})")


def color(value: str) -> str:
    """Nome italiano o #rrggbb -> #rrggbb. Vuoto = colore del tema. ValueError se non lo riconosce."""
    v = str(value or "").strip().lower()
    if v in ("", "predefinito", "tema", "normale", "nessuno", "none"):
        return ""
    if v in COLORS:
        return COLORS[v]
    m = _HEX.fullmatch(v)
    if m:
        return "#" + m.group(1).lower()
    raise ValueError(f"colore sconosciuto '{value}' (es. {', '.join(list(COLORS)[:8])}… o #rrggbb)")


def ui_config(cfg: Config) -> dict:
    """Quello che la pagina deve sapere per disegnarsi."""
    try:
        accent = color(cfg.ui.accent)
    except ValueError:  # un valore scritto a mano nel file che non vale: si usa quello del tema
        accent = ""
    return {"type": "ui_config", "theme": cfg.ui.theme, "accent": accent,
            "text_scale": max(0.7, min(1.8, cfg.ui.text_scale)), "effects": cfg.ui.effects,
            "user": cfg.persona.user_name, "brain": cfg.brain.claude_model}

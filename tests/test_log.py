"""Mai segreti nei registri (jarvis/log.py). Il 27/10 il collegamento di Gmail ha scritto in chiaro nel registro
codice di autorizzazione, token di accesso e refresh token. I valori qui sotto sono inventati, con la forma di quelli
veri di Google."""

import logging

import pytest

from jarvis import log

ACCESS = "ya29.a0AX07CmuixPp0jAJgS45wTXxk-IMEpyWoFe9QIqt4eaP0cckEdB_ObU6M3dZ0g7"
REFRESH = "1//09abcDEFghiJKLmnoPQRstuVWXyz-0123456789_abcdef"
CODE = "4/0AUn6Hd8rwwpMhSJfXeOFL5_AjnXvdgcGbLIxLy7kNXjLc8m"
SECRET = "GOCSPX-AbCdEfGhIjKlMnOpQrStUvWxYz12"


@pytest.mark.parametrize("line", [
    f"Obtained token {{'access_token': '{ACCESS}', 'expires_in': 3599, 'refresh_token': '{REFRESH}', "
    f"'scope': ['https://www.googleapis.com/auth/gmail.modify'], 'token_type': 'Bearer'}}",
    f'"GET /?state=FTFe&iss=https://accounts.google.com&code={CODE}&scope=gmail.modify HTTP/1.1" 200 65',
    f'{{"refresh_token": "{REFRESH}", "client_id": "123.apps.googleusercontent.com", "client_secret": "{SECRET}"}}',
    f"Authorization: Bearer {ACCESS}",
    f"token={ACCESS}&refresh_token={REFRESH}",
])
def test_google_secrets_never_reach_the_log(line):
    out = log.redact(line)
    for secret in (ACCESS, REFRESH, CODE, SECRET, "a0AX07Cm", "09abcDEF", "0AUn6Hd8", "AbCdEfGh"):
        assert secret not in out, out
    assert "[REDATTO]" in out


ZEN_KEY = "sk-zen-Q2xhdWRlIGRpY2UgY2lhbyBhIE1hbnVlbA"  # inventata, con la forma delle chiavi delle guide
SERVER_PW = "kR3v9_Zx-mQ8wLp2Tn6YbVc4"  # come secrets.token_urlsafe(24)
BRIDGE_TOKEN = "Jx8vQ2mN5kL9pR3tW7yB1cF4hD6gS0aE2iU8oZ5nM3q"  # come secrets.token_urlsafe(32)


@pytest.mark.parametrize("line", [
    f"OPENCODE_API_KEY={ZEN_KEY}",
    f'{{"provider": {{"opencode": {{"options": {{"apiKey": "{ZEN_KEY}"}}}}}}}}',
    f"chiave letta: {ZEN_KEY}",
    f"env: {{'OPENCODE_SERVER_PASSWORD': '{SERVER_PW}'}}",
    "Authorization: Basic b3BlbmNvZGU6a1IzdjlfWngtbVE4d0xwMlRuNlliVmM0",
    f"headers={{'Authorization': 'Bearer {BRIDGE_TOKEN}'}}",
])
def test_opencode_secrets_never_reach_the_log(line):
    out = log.redact(line)
    for secret in (ZEN_KEY, "Q2xhdWRl", SERVER_PW, "b3BlbmNvZGU6", BRIDGE_TOKEN):
        assert secret not in out, out
    assert "[REDATTO]" in out


def test_ordinary_lines_are_untouched():
    for line in ("strumento usa_app({\"app\": \"gmail\", \"azione\": \"non_lette\"}) -> 1. da Google: «Avviso»",
                 "codice di uscita 0", "WhatsApp in caricamento: 99", "1/2 frasi lette",
                 "OpenCode, modello glm-5.3-flash-free: modello occupato", "OpenCode pronto su http://127.0.0.1:52101",
                 "task-sk-numero 3"):
        assert log.redact(line) == line


def test_the_formatter_redacts_and_auth_libraries_are_quiet(tmp_path):
    record = logging.LogRecord("requests_oauthlib.oauth2_session", logging.DEBUG, __file__, 1,
                               "Obtained token %s", ({"access_token": ACCESS, "refresh_token": REFRESH},), None)
    out = log.RedactingFormatter("%(name)s: %(message)s").format(record)
    assert ACCESS not in out and REFRESH not in out
    for name in ("oauthlib", "requests_oauthlib", "google_auth_oauthlib", "google.auth"):
        assert name in log.QUIET

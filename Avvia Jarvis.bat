@echo off
REM Avvia Jarvis sotto il guardiano: se cade o si blocca si riavvia da solo, e se una versione nuova
REM continua a cadere torna all'ultima che funzionava (rapporti in data\logs\cadute).
REM Tasti: Ctrl+Alt+J = parla senza dire il nome, Ctrl+Alt+K = ferma tutto.
REM Per spegnere Jarvis: Ctrl+C qui, oppure chiudi questa finestra.
cd /d "%~dp0"
".venv\Scripts\python.exe" -m jarvis.supervisor --console
pause

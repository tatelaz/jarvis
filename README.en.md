# Jarvis

[Italiano](README.md) · **English**

Jarvis is a voice assistant for Windows. You call it by name, talk to it normally and it answers out loud, in
English or Italian. It can use the PC for you: it opens programs and websites, reads and writes on WhatsApp and
Gmail, searches the web, watches a video and tells you what is in it, and remembers what you tell it. The name and
style come from JARVIS in the Iron Man films.

Ears, voice, memory and speaker recognition run on your PC. To understand and answer it needs a language model, the
"brain": Claude (with your subscription or an API key) or a free OpenCode model, which does not even ask for an
account.

> Early version. It works every day on the PC where it was born, but it has been tried on few other PCs. If
> something goes wrong, tell Jarvis "report a problem" (see [Reporting a problem](#reporting-a-problem)) or open an
> issue on GitHub. Do not attach the raw `data\logs\jarvis.log` to a public issue: it contains your conversations.

## What you can ask

A few examples, said the way you would say them to a person:

- "Jarvis, what emails did I get today?" and then "reply that I'll be there too".
- "Read me the latest messages in the football group."
- "Watch this YouTube video and tell me what it is about."
- "Look at the screen: what is this error?" (it takes a screenshot of what you see, only when you ask, and does not
  keep it; with OpenCode it only sees the window title).
- "Remind me tomorrow at nine to call the dentist."
- "Every morning at eight give me a news summary."
- "Open the downloads folder", "turn the volume up", "pause the music".
- "Speak more slowly", "switch to the cinema look".

Anything other people see (a message, an email, a file sent) leaves only after you say yes.

Some things it does on its own:

- You can interrupt it while it speaks. It understands when a sentence is not meant for it and is not fooled by its
  own voice, the TV or a video playing, even with headphones.
- Long jobs (filling in a form on a website, searching and comparing things) go to a helper that uses the browser,
  mouse and keyboard, and meanwhile you can keep talking to it. It tells you when it is done.
- It remembers what matters and finds it again by meaning, not by matching words. At the end of a conversation it
  updates its memories by itself.
- It recognizes voices. You can do everything, people it knows can do what you allow them to, guests only the basics
  and with no access to your data. Permissions are enforced by the program, not by the model.
- Every setting can be changed by voice. For bigger changes it can modify its own code: it does it in a separate
  copy, runs the tests and asks you before using it.
- If it freezes, a guardian restarts it, and if a new version does not start it goes back to the last one that
  worked. When the Claude subscription runs out it switches to the free models by itself.
- If something goes wrong you can report it by voice or from the window: a technical report cleaned of your data
  is sent, only if you want (see [Reporting a problem](#reporting-a-problem)).

## Requirements

| | Tested | Notes |
|---|---|---|
| System | Windows 11, 64-bit | Windows 10 not tested yet |
| Graphics card | NVIDIA with 8 GB (RTX 4060) | an NVIDIA card is needed for now: voice and ears together use about 5.6 GB |
| Memory | 16 GB | |
| Disk | about 10 GB | 5 GB more for the music and sound model |
| Brain | Claude Pro or Max subscription, or an API key | or OpenCode, free and without an account |
| Other | microphone and speakers or headphones | WhatsApp needs Chrome or Edge |

You do not need to install Python, Node.js, Git or OpenCode first: Jarvis downloads them if they are missing.

## Installation

1. Download `Jarvis-Setup-<version>.exe` from the Releases page and open it.
2. Windows shows "Windows protected your PC" because the installer is not signed (see below why): click "More info"
   and then "Run anyway".
3. It installs for your user, without administrator rights, in `%LOCALAPPDATA%\Programs\Jarvis`. It takes about a
   minute: meanwhile it prepares Python and the libraries. Before installing it shows what stays on your PC and what
   leaves it.
4. At the end the first start opens: it checks the PC, lets you choose the brain, downloads the models (about 5 GB
   from GitHub and Hugging Face, resuming where it stopped if the connection drops) and tests microphone and
   speakers.
5. Jarvis turns on and introduces itself. It asks your name, learns your voice and helps you connect the apps. You
   can say "later" and continue with "let's continue the setup".

If you prefer the source code: `git clone`, then `uv sync` and `python -m jarvis` (you need
[uv](https://docs.astral.sh/uv/)). It leads to the same first start. Each release also has the source as a zip, but
without the git history Jarvis cannot modify its own code: for that use `git clone`.

To talk to it, call it by name ("Jarvis, ...") or press Ctrl+Alt+J and speak. Ctrl+Alt+K stops everything. If you
prefer typing, just start typing in its window.

## The brain

You choose it at first start and can change it whenever you want, by voice too.

- **Claude with your subscription** (Pro or Max). Jarvis uses the official Claude Code login: the browser opens on
  claude.ai and you sign in with your account. Jarvis never sees your password. It uses your subscription limits.
- **Claude with an API key**. You pay Anthropic per use. You paste the key in the first-start page; it is checked
  right away and saved encrypted.
- **OpenCode**. Free models, without an account or a key. They answer a bit worse than Claude, but they are fine to
  start. Jarvis also uses them as a fallback when Claude does not answer.

If you already use Ollama you can connect it later (on the PC or in the cloud).

## WhatsApp, Gmail and games

**WhatsApp**: say "connect WhatsApp" and scan the QR code with your phone, as for WhatsApp Web. The first time Jarvis
downloads the parts it needs (a few minutes). WhatsApp is used through WhatsApp Web in an unofficial way: if
WhatsApp changes something it may stop working until Jarvis is updated.

**Gmail**: say "connect Gmail". The quick way is a Google app password, which it helps you create in a minute: the
account needs 2-step verification turned on. Jarvis opens the Google page, you create a password called "Jarvis" and
paste it in the Jarvis window (never by voice). Then it reads, searches, replies, writes, forwards, adds labels and
moves to the bin. Filters and auto-reply are rarely needed and require the full connection, with a free Google
project: the steps are in [docs/GMAIL.md](docs/GMAIL.md) (in Italian).

**Games**: it opens Steam, Epic and Xbox games by name ("open Rocket League").

## What stays on your PC and what leaves it

Audio never leaves the PC: speech recognition, speech synthesis, memory and known voices are all local. What leaves:

- the text of what you ask, sent to the brain you chose (Anthropic for Claude, OpenCode for the free models);
- web searches and the things you ask it to do online;
- once a day a question to GitHub to check for a new version (turn it off with "updates off");
- at first start, the model downloads from GitHub and Hugging Face;
- reports to the developers, only when you want (see below).

### Reporting a problem

If something goes wrong you can tell Jarvis ("report a problem: you didn't understand me when...") or write it in the
window, at the bottom of the log, under "report a problem". A technical report that helps fix the bug is sent:
version, Windows, graphics card, the errors in the log and your words about the problem. Before it leaves, the
report is cleaned: no sentences from your conversations or Jarvis's answers, no name of yours or of your folder,
emails, phone numbers or contacts. In the window you see the exact text before sending it; by voice Jarvis tells you
what it is about to send and it leaves only after your yes.

After a crash Jarvis asks whether to send the report. You can change this by voice ("crash reports always" or
"never"). The report arrives as an issue in a private repository of the developers, through a small server that
stores nothing, not even your IP address (`servizi/segnalazioni`). If there is no network, it is sent at the next
start.

Keys, app passwords and Gmail permissions are saved encrypted by Windows: only your user on this PC can read them.
Your data is in `%LOCALAPPDATA%\Programs\Jarvis\data`.

## The voice

By default Jarvis speaks with one of two included voices, taken from LibriVox readings (public domain) and processed
with an effect reminiscent of the films. You can create others from an audio or video file ("create a voice from
this interview"): Jarvis picks 10-14 seconds of clean speech by itself and transcribes them. The resulting voice is
inspired by the real one but is not the real voice: it is an imitation made by the model from a few seconds. Use it
only for yourself, and never to make people believe someone said something.

## Updates and uninstalling

Copies installed with the `.exe` update by themselves from the GitHub releases: Jarvis only takes files with the
published SHA-256 fingerprint and updates when it has been idle for a quarter of an hour. If you prefer to decide,
tell it to ask you first ("ask me before updating"). Copies taken with git update with `git pull`.

The installed copy is also a git repository (it becomes one at first start), so the changes Jarvis makes to its own
code at your request are not lost with updates: the new version is merged with your changes in a separate copy,
with the tests. If both touch the same places, a sub-agent fixes it and Jarvis asks you to confirm; if it cannot be
done you stay on your version, which works, and it tells you.

To uninstall: Settings, Apps, Installed apps, Jarvis. It asks whether to keep data and models: if you say yes, a new
installation finds them again.

## Current limits

- An NVIDIA card is needed: the voice engine only uses CUDA for now. On AMD and Intel cards it does not start, and on
  the processor alone it would take about 13 seconds per sentence. The first start says so before downloading
  anything.
- Tested only on Windows 11 and on few PCs.
- It speaks only Italian and English.
- The installer is not signed yet (see [Code signing policy](#code-signing-policy)). Meanwhile every release has the
  SHA-256 fingerprints of its files (`SHA256SUMS.txt`), so you can check they are exactly the published ones.

## Development

You need [uv](https://docs.astral.sh/uv/). On a Windows without the
[Visual C++ Redistributable](https://learn.microsoft.com/cpp/windows/latest-supported-vc-redist) (usually already
there) install it, or onnxruntime does not load: the installer brings it along instead.

```
uv sync
bash scripts/check.sh
```

`python -m jarvis` opens the first start by itself if something essential is missing; `python -m jarvis --setup`
runs it again. `python scripts/primo_avvio_prova.py` shows the first-start page with the real data of the PC without
touching anything (fake download, configuration in a temporary folder).

`scripts/check.sh` checks the style (ruff) and runs the tests that do not need the audio engines, Claude or the
hardware; those that do are marked `engine`, `claude`, `hardware` and `desktop`. `python scripts/mutation_check.py`
deliberately puts bugs in the code and checks that the tests notice: a check that stays green with a bug inside
protects nothing.

Every commit goes through the same tests on GitHub Actions ([prove.yml](.github/workflows/prove.yml)). A new version
is published like this: version in `pyproject.toml`, notes in `docs/versioni/X.Y.Z.md`, then the tag `vX.Y.Z`; the
rest (installer, bundle for updates, fingerprints, release) is done by
[installatore.yml](.github/workflows/installatore.yml).

Code, comments and documents are in Italian. The other documents:

- [docs/ARCHITETTURA.md](docs/ARCHITETTURA.md): how it is built and why.
- [docs/STATO.md](docs/STATO.md): what works, the measurements, the problems already met, the next steps. Jarvis's
  own sub-agents read it, with the architecture, before changing its code.
- [docs/GMAIL.md](docs/GMAIL.md): how to connect Gmail.
- [CLAUDE.md](CLAUDE.md): the rules for whoever works on the project with an AI assistant.

## Code signing policy

A certificate to sign programs costs about 200 € a year, which this project does not have. The free signing that
[SignPath Foundation](https://signpath.org/) offers to open source projects is requested after the first release:
until it arrives the installer is not signed and Windows shows "Windows protected your PC". If it is granted, this
will read: *Free code signing provided by [SignPath.io](https://signpath.io), certificate by
[SignPath Foundation](https://signpath.org)*.

- The installer is built only on GitHub Actions, from the code of this repository and the version tag
  ([.github/workflows/installatore.yml](.github/workflows/installatore.yml)), never on a PC.
- Roles: author, reviewer (every change proposed by others goes through a review) and signing approver:
  [tatelaz](https://github.com/tatelaz).
- Privacy: Jarvis sends nothing to other systems except what is described in
  [What stays on your PC and what leaves it](#what-stays-on-your-pc-and-what-leaves-it), which the installer shows
  before installing; what leaves by itself (the check for new versions, crash reports if you make them automatic) can
  be turned off by voice.

## License

The code is released under the [Apache 2.0](LICENSE) license. Models, engines and libraries have their own licenses,
listed in [LICENZE_TERZE_PARTI.md](LICENZE_TERZE_PARTI.md) (in Italian): some forbid commercial use.

Independent project, not affiliated with Marvel, Disney, Anthropic, NVIDIA, Google or Meta.

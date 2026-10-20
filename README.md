# TRACE (`tracemem`)

A local-only perceptual memory of your own life that **never lies to you**.
It watches your Mac's screen (and, through the phone, the physical world),
reads what it sees into immutable text, thinks about it while you are idle,
and answers questions from your life with citations — or says "not in
memory" and means it. Nothing it remembers leaves your machines.

- **Architecture:** [NAPKIN.md](NAPKIN.md) — the founder's drawing, transcribed,
  plus its amendments. It outranks everything else in this repo.

This repository is the product's **code only**. Nothing anyone has captured
is in it: every store lives in `data/` on the machine that recorded it and
is git-ignored, and so is the founder's planning workspace.

---

## How it fits together

```
 Mac screen ──► eye (ScreenCaptureKit, blur gate, change gate, Vision OCR,
                     Accessibility titles, local VLM "macro" pass, helpers;
                     stores only text it has not already stored)
 iPhone camera + ear ► phone/TracePhone (Vision + speech, on-device)
                       ──LAN, sealed, per-phone key──────────────────┐
 Mac mic (opt-in) ─► ear (faster-whisper, CPU) ──────────────────────┤
 your files ───► owned/ (cold start: broad index, then deep reads) ──┤
                                                                     ▼
                 ClaimQueue ─► ClaimLog  (TEXT: immutable, hash-chained,
                                          per-claim encrypted SQLite)
                                 │
                                 ▼
                 Binder (episodes: time × place, flicker hysteresis,
                         versioned late-boundary reconciliation)
                                 │
                                 ▼
                 Graph (nodes = beliefs; encrypted)  ◄── the night:
                         birth → accrual → mining raw → combination →
                         judge → prune (meta only; raw is never touched)
                                 │
                                 ▼
                 Supplier (just enough context) ─► local LLM (ollama)
                                 │
                 ~/.trace/ask.sock (UNIX socket) ─► ./trace, TRACE.app
```

| Napkin box | Code |
|---|---|
| Extraction black box | `src/tracemem/extraction/`, `src/tracemem/capture/` |
| TEXT (raw, immutable) | `src/tracemem/text/` |
| Binder | `src/tracemem/binder/` |
| Node creation (cells, night, encryption) | `src/tracemem/nodes/`, `src/tracemem/container/` |
| Information supplier | `src/tracemem/supplier/` |
| LLM | `src/tracemem/llm/` |
| Daemon, organs, socket, faces | `src/tracemem/app/` |
| Your own corpus (cold start) | `src/tracemem/owned/` |
| Measurement spine | `src/tracemem/evalx/`, `src/tracemem/prediction/` |
| Phone (wearable) | `phone/TracePhone/` (Swift) ⇄ `container/phone_link.py` |

## Laws the code enforces (and tests pin)

- **Raw is immutable.** There is no update path for a stored claim; the night
  changes beliefs (meta), never observations.
- **Pixels and waveforms die on the device that captured them.** Only text is
  stored — on the Mac and on the phone.
- **One store writer.** Every organ submits to a queue; one owner thread
  appends (hash chain + durability are stated for one connection).
- **One model on the Metal GPU at a time.** CPU helpers may run in parallel.
- **A founder's grade is evidence, never a tuning knob.**
- **Refusals:** no keylogging, no telemetry, no cloud processing
  (`tests/unit/test_refusals.py`).

## Security model

- Per-user master key lives in the login Keychain; macOS releases it only to
  the **signed** `TRACE.app` helper (`packaging/TraceHelper.swift`).
  Purpose keys are derived from it (store, graph, phone link).
- No egress: every request passes `llm/egress.require_loopback`; the only
  HTTP peer is the local model server on `127.0.0.1` (`tests/unit/test_egress.py`).
  The app is not sandboxed, so this — not the `network.client` entitlement —
  is what enforces it.
- One listener, for the paired phone on the LAN (founder ruling F-LAN):
  private/link-local peers only, at most 4 sessions, 10s to authenticate,
  a separate revocable key per phone (`./trace phones`,
  `./trace revoke-phone ID`), every batch sealed with ChaCha20-Poly1305. Justification per entitlement: [packaging/entitlements.md](packaging/entitlements.md).
- Third-party helpers run under `sandbox-exec`: no network, no writes, no
  reads of `/Users`, `/Volumes` or the temp roots.
- **Not encrypted at rest.** Claim text is sealed; the columns the store
  must sort and aggregate on are not: time (`t_ms`), device id, grade, the
  bidder stamps, and `place_key`, which is a ~110 m GPS cell or an unsalted
  16-hex hash of the Wi-Fi name (`binder/cross_pillar.py`). Someone holding
  the file without the key can still see when, and roughly where, the
  devices were in use.
- **The microphone.** The phone's ear hears everyone near you, not only you.
  Recording conversations without consent is illegal in many jurisdictions
  (for example German StGB §201, and US two-party-consent states). It stays
  off until you tap **Start hearing**, and using it lawfully is on you.

**About the comments.** Many comments cite measurements taken on the
maintainer's own memory. The numbers are kept; every name, site, place and
title from that memory has been replaced with a stand-in.

**License.** None yet. The code is public so it can be read, cloned and run
for evaluation; all other rights are reserved until a license is chosen.

---

## Requirements

- macOS on Apple silicon (developed and tested on macOS 15), Xcode 26 (Swift toolchain for the signed helper
  and the phone app), an **Apple Development** signing identity.
- Python **3.9** exactly: `/usr/bin/python3` from Xcode's command-line tools.
  `TRACE.app` runs on that interpreter and seals the `.venv`'s 3.9
  site-packages, so a Homebrew 3.12 venv passes the tests but cannot be bundled.
- [ollama](https://ollama.com) with `gemma3:12b-it-qat` for answers and the
  night's judgments. Without a model the memory still captures and binds;
  prose answers are refused rather than invented.

## Set up

```bash
git clone https://github.com/zer00318/trace-memory.git
cd trace-memory
/usr/bin/python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install -e .
.venv/bin/python -m pytest tests -q

# One-time model downloads. TRACE never fetches anything at runtime
# (models load with local_files_only), so this step is required.
.venv/bin/python -c "from sentence_transformers import SentenceTransformer as S; S('sentence-transformers/all-MiniLM-L6-v2')"
ollama pull gemma3:12b-it-qat
# Only if you turn on the Mac's microphone ("ear": true):
.venv/bin/python -c "from faster_whisper import WhisperModel as W; W('base', device='cpu', compute_type='int8')"
```

Runtime dependencies live only in `requirements.txt`, pinned to the versions
the suite passes with (deliberately not duplicated into `pyproject.toml`).
`sentence-transformers` pulls in PyTorch, so the first install is large.

## Run

```bash
./trace install          # build + sign TRACE.app, install to /Applications,
                         # offer the cold start, start at login (launchd)
./trace                  # ask questions
./trace "what was I doing an hour ago?"
./trace start | stop | status | log
./trace digest 24        # what it did and what it now believes
./trace pair-phone       # draw the code the iPhone app scans (F-LAN)
./trace ingest PATH      # cold start: read your own files (daemon stopped)
./trace falsify --author # write questions from memory, then:
./trace falsify FILE     # does the belief tier beat plain retrieval?
./trace reconcile        # version quarantined late boundaries (daemon stopped)
```

First run needs two macOS grants for `TRACE.app`: **Screen Recording** and
**Accessibility** (window titles). `./trace status` says which are missing.
Settings live in `~/.trace/settings.json` (`./trace settings`); the ear is
opt-in (`"ear": true`), the phone listener opt-out (`"phone": false`).

### The phone

```bash
cd phone/TracePhone
xcodebuild -project TracePhone.xcodeproj -scheme TracePhone \
  -destination "id=<your iPhone's xcodebuild id>" -allowProvisioningUpdates \
  DEVELOPMENT_TEAM=<your Apple team id> \
  PRODUCT_BUNDLE_IDENTIFIER=<your.reverse.dns>.trace.phone \
  -derivedDataPath build/dd build
xcrun devicectl device install app --device <devicectl UDID> \
  build/dd/Build/Products/Debug-iphoneos/TracePhone.app
```

On the iPhone: Developer Mode on, trust the developer certificate
(Settings → General → VPN & Device Management), open TRACE, scan the code
from `./trace pair-phone`, tap **Start seeing** and/or **Start hearing**. iOS only allows the camera
in the foreground on an unlocked phone; the red **SEEING** band is always
shown while it runs. The ear transcribes speech **on the phone only** (it
refuses rather than send audio to a server), uses the built-in microphone
so headphones are not switched into call mode, keeps no audio, and shows an
orange **HEARING** band; it keeps listening with the screen off. Claims queue on the phone and sync when it shares a
network with the Mac. The wire protocol is pinned from both sides by
`tests/unit/test_phone_link.py`.

## Test

```bash
.venv/bin/python -m pytest tests -q
```

Witnessed 2026-10-20 as a new user would run it: an anonymous clone of the
public repo, a fresh `python3 -m venv`, `pip install -r requirements.txt`
(exit 0), then the suite: **`2070 passed, 9 skipped, 1 xfailed`**, 0 failed.
The skips need machine-local things: banked frames in the git-ignored
`gold/`, an on-screen window, the owner's planning workspace.

- green = the regression wall · xfailed = the TODO ledger · FAILED = a real
  bug; fix it before anything else.

## Status

Built and running daily on one Mac since July 2026. What works: capture
(screen OCR, window titles, a local VLM pass, the iPhone camera, an optional
ear), an encrypted hash-chained claim store, episodes, a night process that
derives beliefs, and cited answers or honest refusals.

**The health check** is `tools/cockpit.py executable_checks()`: facts read
from your own stores and the running signed app, not from documents.

```bash
.venv/bin/python -c "import sys; sys.path.insert(0,'tools'); import cockpit; \
  [print(c['state'], c['name'], '-', c['detail']) for c in cockpit.executable_checks()]"
```

**Eval-only frames.** The eye banks one downscaled frame every 4 minutes in
`gold/frames/` (git-ignored, owner-only, newest 1,000 kept; opt out with
`"gold_frames": false`). They are never memory: they are the ground truth
the usefulness experiment's questions are written from, so a question is
never authored by reading the store it tests.

It measures what each sense is doing now (window titles need the macOS
Accessibility grant; the ear is opt-in), whether every claim is bound,
whether anything under `data/` is readable at rest, and more.

**Not yet proven:**

- **Usefulness.** `./trace falsify` compares the belief tier against plain
  retrieval over raw text; it needs 30+ questions written by the owner of
  the memory *before* looking at it, and has not yet run on real data.
- **Belief depth.** Derived beliefs do not yet combine with each other.
- **Volume.** Before 2026-10-20 raw grew by roughly 100+ MB/day on an active
  Mac, half of it the eye re-reading unchanged text. After the re-read fix,
  14 minutes of active use stored 0.92 MB with 1.6% repeated text; a full
  day has not been measured yet.
- **Signing.** Apple Development–signed, not notarized; not sandboxed (see
  the Security model for what enforces the network promise instead).

Known limits: binder and phone thresholds are labeled UNCALIBRATED; a night
slice that reaches a model-backed judgment can overrun its budget; macOS
and iOS 18+ only.

## Layout

```
src/tracemem/     the product
tests/            unit, invariants, oracles; tests/test_crown.py is the
                  end-to-end crown test on a synthetic week
phone/            the iOS wearable app (Swift, no dependencies)
packaging/        signed bundle: make_app.py, TraceHelper.swift, entitlements
tools/            cockpit, audits, migrations, eval tools
dev/              maintainer tools (publish_public.py: privacy-gated publishing)
exams/            question sets the honesty checks run (unanswerable, hostile)
```

`./trace install` builds the app from this checkout and keeps its data in
this checkout's `data/`: move or delete the clone and `/Applications/TRACE.app`
loses its memory. Re-run `./trace install` after moving it.

Never committed: `data/` (your encrypted stores), `gold/` (eval frames),
`dist/`, `phone/TracePhone/build/`, and the founder's local workspace
(`campaign/`, `specs/`, `eval_runs/`).

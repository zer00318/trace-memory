# THE NAPKIN — the architecture (verbatim transcription)

Handwritten by the founder; photographed 2026-07-18. This file is the
constitution of this repo: on any conflict it outranks code, tests, campaign
documents, and specs — in that order below it. Nothing here may be "improved"
in place; a change to the architecture is a founder decision, recorded on the
board.

## Standard Model

                     ┌──────── steers ────────┐
                     ▼                        │
    [World] --Senses--> Eyes / Ears --------> [Brain]
       ▲              (the organs)              │
       └──────────────── acts on ───────────────┘

    (because we can't manufacture other senses
     or most of the information is obtained through this)

> **TRANSCRIPTION CORRECTION (2026-07-29).** Until today this block showed
> ONE return arrow (Brain → World). The photograph shows **two** arcs: an
> upper arc from Brain back to **Eyes**, and a lower arc from Brain back to
> **World**. The upper arc — the brain steering its own senses — was lost in
> the original transcription, and its absence is load-bearing: it is why
> `prediction/perception_feedback.py` was written inert and why nothing in
> the tree has ever posted a top-down bid to the gaze auction. Restoring an
> arrow the founder drew is a correction, not an amendment. Also corrected:
> "Organs" labels Eyes/Ears in the drawing; it is not a third sense.

## EXECUTION

    [World] -> Video (Eyes), Audio (Ears)
        -> [Extraction Black Box]
        -> Coordinates + time stamps
        -> Blurry frames discarded · Signal–Noise decipherer + Context
        -> A macro picture of what's happening => VLM
        -> A specialized eye or helper for various tasks are called   (Helper)
        -> Best understanding possible of the world
        -> [TEXT]

    Text from various helpers marked by coordinates
        -> [Binder] => binding the coordinates, physicality & temporal, together
        -> First thought or raw material that can be marinated on
           Note: This is not a complete Node, it's raw material
        -> [Node Creation Black Box]
             · Encryption, i.e. people using our app can decipher
             · A physical living breathing cell that can combine with
               fellow nodes to derive new things — 1+1 = 4, ≠ 2
             · Always running in the background but better nightly
             · Discarder of meta, i.e. the marinated content that's not
               useful is discarded, NOT the raw material

    [Information Supplier Black Box]
        -> Just Enough Context for local LLM
        -> LLM does what LLM does

## Box → code map (subordinate to the drawing above)

| Napkin box | Code |
|---|---|
| Extraction Black Box (coords+timestamps, blur discard, VLM macro, helper eyes) | `src/tracemem/extraction/` |
| TEXT (raw, immutable, coordinate-marked) | `src/tracemem/text/` |
| Binder (physicality & temporal together) | `src/tracemem/binder/` |
| Node Creation Black Box (cell, marination, encryption, discard meta not raw) | `src/tracemem/nodes/`, `src/tracemem/container/` |
| Information Supplier Black Box (just enough context) | `src/tracemem/supplier/` |
| LLM does what LLM does | `src/tracemem/llm/` |

Declared **beyond-napkin** (kept only until founder ruling F8, see board):
`src/tracemem/prediction/` (surprise spine), `src/tracemem/evalx/`
(falsification harness). The napkin names neither.

## AMENDMENTS (founder decisions — the constitution grows only here)

**F-OWNED (founder-ratified 2026-07-24).** The World the senses draw from is
BOTH the lived world and the user's own digital corpus. Memory therefore has
TWO sense-worlds, and every raw fact carries which one it came from:

- **LIVED** — observed happening (screen/camera/mic), anchored to a capture
  frame. The original napkin's `[World] -> Video/Audio` path.
- **OWNED** — read from the user's own files/mail/history, anchored to a
  re-readable source, not a frame. Read broadly first (an index), then mined
  deep where a node is hungry. This is the answer to cold-start: a past to
  derive from on day one.
- **BELIEF** — the derived tier (nodes), never a raw fact.

Consequences now in the tree (all tested, both backends): the Extraction/TEXT
atom carries a provenance CLASS (`domain/provenance.ProvenanceClass`) and a
two-world coordinate (`domain/coordinate.CaptureCoord` = frame OR source);
the OWNED hemisphere lives in `src/tracemem/owned/`. Rationale and the gap
analysis that drove it: `specs/ONTRACK_ANALYSIS_20260724.md` (maintainer's
local workspace; not published). The screen
(LIVED) world is built first; the physical-LIVED and full-OWNED corpus reach
is deliberately staged. Precedence unchanged: this amendment is the napkin,
so it outranks code, tests, campaign, and specs.

---

**Amendments below ratified in the founder interview of 2026-07-29. The full
record, with the rationale and the code delta each implies, is kept in the
maintainer's local workspace (`campaign/`, not published).**

**F-PILLARS — two bodies, one brain.** TRACE is not a screen recorder. The
World the senses draw from includes the PHYSICAL world, captured by a
wearable. **For now the wearable is the phone** (no new hardware). The Mac
is the BRAIN and also a sense: it captures the digital world, receives the
wearable's claims, owns the graph, runs the night, and answers. The wearable
extracts to TEXT on-device and ships claims only — pixels and waveforms die
on the device that captured them, exactly as they already do on the Mac.
*Already anticipated in code:* `WorldCoord.gps/pose`, `Modality.CAMERA`,
`binder/cross_pillar.place_key` (SSID+GPS), `container/sync` (HAVE/WANT).

**F-EAR — the ear is ambient and always on.** Audio has co-equal billing
with video, as drawn. It records everything it hears, including other
people. **The waveform is destroyed on-device and only text persists** —
the same trust boundary the eye already keeps. Exclusion policy (secure
input, blocklists, private places) is DEFERRED by founder decision while
this is a personal project; the mechanism (`app/matcher.py`, the `EXCLUDED`
witness) stays built and unwired so it can be turned on without redesign.

**F-LOOP — the brain steers the eyes.** The restored upper arc is real
architecture, not decoration. A node with unmet hunger bids into the SAME
auction that bottom-up change bids into: one hunger, one currency, for the
eye's next gaze and for the night's tokens. **Staged with caution:** hunger
aims the NIGHT first (re-reading retained raw); live gaze-steering is tried
in branches and adopted only if it measurably converges better.

**F-HELPERS — helpers are sandboxed plugins, and they run in parallel.**
A helper is a composite package, not a model call: trigger + extractors +
optionally a bundled binary (the worked example is a chess helper = board→PGN
reader + Stockfish + username capture). Third-party developers write them.
Many run at once — reading a chess position while another transcribes voice
and another flags a siren. Reconciled with the one-model-on-GPU law by:
ONE heavy (GPU) helper resident at a time, chosen by the primary activity;
unlimited cheap (CPU/streaming) helpers in parallel; anything that could not
get a turn is DEFERRED and re-run by the night against retained raw. Nothing
is dropped — the hardware ceiling becomes a latency cost, which is what
raw-forever buys.

**F-RAW — raw is kept forever, and this deliberately departs from biology.**
A brain discards episodic detail and keeps the gist; TRACE does the opposite,
because re-marination (a node re-reading its own old claims with grammars it
did not have at the time) is only possible while raw survives. Measured
2026-07-29 on the live store: 1,563 bytes/claim, ~1.6 MB/day observed,
≈0.57 GB/year — cheap enough that the biological argument for forgetting
does not apply here. (Superseded as a volume figure: once the eye read every
visible window the store grew on the order of 100 MB/day; F-NEW below is the
answer, and the README carries the current measurement.)

**F-MEASURE — how we know the brain is alive.** Three instruments, and they
**resolve founder question F8: `evalx/` and `prediction/` are NOT
beyond-napkin scaffolding — they are the measurement spine and they stay.**
  1. *Sealed question sets* + gap-to-ceiling: our stored context (pixels
     already deleted) versus the maximum a frontier model extracts from the
     same material. Extraction tier, already instrumented.
  2. *The graded digest*: TRACE shows what it did and what it now believes,
     cumulatively; the founder grades it. This is the MEMORY tier, which a
     single-frame ceiling structurally cannot measure.
  3. *The live map*: the node graph, browsable, including retired beliefs.

**F-GRADE — a founder answer is evidence, never a tuning knob.** A grade
enters the graph as a claim at VERBATIM grade with perceiver `founder`
(the slot `domain/grading.py` already reserves) and everything downstream
re-derives from it. Grades MUST NOT move combination priors, the resolve
bar, or the prune floor. **The law: a grade changes what is TRUE in the
graph, never how the graph DECIDES** — otherwise the brain learns to earn
approval and can no longer surprise you, which is the entire product.

**F-KEYS — what "only our app can decipher" actually means.** Not
cryptographic secrecy: the architecture may be published. The per-user key
is derived on-device and lives in the user's Keychain, and macOS releases it
only to a binary signed with our team identifier. (As built, this is the
keychain item's access-control list naming the signed TRACE.app helper —
`signedHelperAccess()` in `packaging/TraceHelper.swift` — not a
`keychain-access-groups` entitlement.) A stranger who
rebuilds the open-source code cannot read the key; the USER always can,
because it is their memory. Open-source boundary: decided at launch, not now.

**F-COLDSTART — ingest the past before watching the present.** A brain with
no memory is useless for weeks. On install, broad-index the user's own
corpus so day one starts with a populated graph. `src/tracemem/owned/` is
built, tested, and unwired; wiring it is the cold-start answer.

**F-EGRESS — deferred.** Context flowing to Gemini / Apple Intelligence /
calendar apps is a later-day product. `network.client=false` and
`network.server=false` stand unchanged.

**F-LAN — the phone reaches the Mac over the local network (founder ruling,
2026-10-14).** Asked how the wearable's text claims should travel to the
brain — local Wi-Fi, cable/file drop, or keep the phone parked — the founder
chose **"Local Wi-Fi, same network."** This amends the F-EGRESS line for one
direction only: the Mac may LISTEN (`network.server = true`) for the user's
own paired phone on the local network. `network.client` stays **false**: the
Mac still dials nobody, and nothing about this lets memory leave the user's
network. The listener accepts only private / link-local peers, proves key
possession both ways (a purpose key derived from the per-user key; the phone
never holds the master), and seals every batch. Pixels still die on the
phone (F-PILLARS); only TEXT claims cross.

**F-EAR-PHONE — the ear lives on the phone (founder ruling, 2026-10-20).**
Asked whether the always-on ear (F-EAR) or the Mac's opt-in setting should
win, the founder answered: **"It is ON for the phone, not the mac."** The
ambient ear is the wearable's: the iPhone transcribes on the device, destroys
the audio, and sends TEXT claims over the F-LAN link like its camera does.
The Mac's microphone stays off by default (`"ear": true` remains an explicit
opt-in). The phone's ear is built (`phone/TracePhone/TracePhone/Ear.swift`);
until it is installed and paired, the ear is honestly red.

**F-NEW — no re-reads; new detail instead (founder ruling, 2026-10-20).**
"There shouldn't be a re-read; new details must be captured rather than the
same thing." The eye stores text it has not already captured from the same
window, says only that a window is still in front when nothing changed, and
spends the reads it saves on parts of the screen it has not read. This
amends F-RAW's arithmetic, not its law: everything stored is still kept.

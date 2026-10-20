# F2 — golden voice clips (WO-005) — ~10 min of founder time

10 clips of YOUR voice. 7 are read from the scripts below (so the gold
transcript is exact by construction); 3 are spontaneous (you type what you
said right after). All files go in `gold/audio/` (gitignored — audio never
leaves this Mac).

## Recording

ffmpeg is installed (`/opt/homebrew/bin/ffmpeg`). From the repo root, per clip:

    ffmpeg -f avfoundation -i ":0" -t 30 gold/audio/clip01.wav

- `":0"` = default microphone. If it errors, list devices with
  `ffmpeg -f avfoundation -list_devices true -i ""` and use `":N"`.
- First run may need Microphone permission for the terminal.
- Press `q` to stop early. One command per clip: clip01.wav … clip10.wav.
- Prefer QuickTime? File → New Audio Recording works too; save as
  clipNN.m4a in gold/audio/. Either format is fine.

## Read clips (clip01–clip07) — the script IS the gold transcript

Speak naturally, normal pace, don't over-articulate. One paragraph per clip.

**clip01 (numbers & dates):** "The meeting moved from March third to March
thirteenth at four forty-five. That's a difference of ten days, and the
budget went from twelve thousand five hundred to nineteen thousand."

**clip02 (names):** "I talked to Maya and Sam about it, then forwarded the
thread to Priya. Sam thinks Anthropic ships faster than Google, but Maya
disagrees completely."

**clip03 (technical):** "The daemon writes claims to SQLite through the
binder, and the nightly job marinates the graph. If the GPU is contended,
capture goes dark silently — that's the worst failure mode."

**clip04 (casual):** "Honestly I don't know, it was kind of a weird day. I
had coffee way too late and then just doom-scrolled instead of sleeping,
which, yeah, predictable outcome."

**clip05 (product words):** "TRACE should notice what I didn't. It saw what
I saw, it remembers exactly, and it never lies to me. The napkin is the
whole architecture."

**clip06 (fast + fillers):** "So um, basically the thing is — wait, let me
start over. The thing is, we need the extraction black box done before the
binder even matters, right? Like, order matters here."

**clip07 (quiet/far — record this one from across the room):** "This is the
far-field test. I'm standing away from the laptop near the window, speaking
at normal volume, maybe a bit softer than usual."

## Spontaneous clips (clip08–clip10)

Talk for ~20 seconds about anything real (what you did today, what's
annoying you, what you'll do tomorrow). IMMEDIATELY after each, type exactly
what you said into `gold/audio/clip08.txt` (etc.) — word for word, including
fillers like "um". Don't clean it up; the transcript must match the audio,
not improve it.

## Done when

    ls gold/audio/   →  10 audio files + 3 .txt transcripts

Then any worker session can run the WO-005 measurement (whisper vs Apple
Speech, WER + timestamp check) without you.

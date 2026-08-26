---
name: cdaf
description: Read CDAF sidecar files (.cdaf) instead of processing video with vision. Use whenever a task involves understanding, summarizing, searching, editing, or selecting from video files (b-roll, raw clips, footage libraries) — check for a .cdaf sidecar FIRST before analyzing any video directly.
---

# CDAF — Cached Descriptive Asset Files

A `.cdaf` file is a timestamped, pre-computed description of a video, sitting next to
it with the same basename (`clip.mp4` → `clip.cdaf`). Reading it costs a few hundred
text tokens; analyzing the video directly costs orders of magnitude more (~263 tokens
per second of footage on Gemini-class models). **Always prefer the sidecar when it is
fresh.**

Format spec and tooling: https://github.com/UditAkhourii/cdaf

## The rule

Before analyzing ANY video file (`.mp4`, `.mov`, `.mkv`, `.webm`, `.avi`, `.m4v`):

1. **Check for a sidecar**: same directory, same basename, `.cdaf` extension.
2. **Verify freshness** before trusting it (see below). A stale sidecar describes an
   older version of the video — using it is worse than not having one.
3. **If fresh**: read the sidecar instead of processing the video. Use it as the
   account of what the video contains — within the limits below.
4. **If missing or stale**: generate one (see below) so the cost is paid once. If you
   cannot generate, fall back to direct video analysis.

## Verifying freshness

The sidecar header carries `bytes` (file size) and `sha256` (content hash) of the
exact video it describes. **Verification never needs an API key or network access.**

- **Cheap check (usually enough)**: compare the video's current file size to the
  header's `bytes` value. Different size → provably stale.
- **Strict check**: `cdaf validate <video>` (exit 0 = fresh), or hash the file
  yourself and compare to the header's `sha256`:
  - PowerShell: `(Get-FileHash clip.mp4 -Algorithm SHA256).Hash.ToLower()`
  - POSIX: `sha256sum clip.mp4` / `shasum -a 256 clip.mp4`
- Use the strict check when the decision is expensive to get wrong (publishing,
  final edits); the cheap check suffices for exploration.

## Reading a sidecar

It is plain UTF-8 text — use the Read tool directly, or `cdaf read <video>` (which
verifies the hash automatically and refuses to print a stale sidecar).

Format: a `key: value` header between `--- CDAF/1.0` and `---`, then markdown:

- `## Summary` — what the clip is
- `## Segments` — `[MM:SS.d-MM:SS.d] description` lines covering the whole video
  (see the trust note below before cutting on these timestamps)
- `## Transcript` — spoken words with timestamps (or `(no speech)`)
- `## On-screen Text` — visible text with timestamps (or `(none)`)
- `## Tags` — retrieval keywords

## How much to trust a fresh sidecar

Freshness proves the sidecar describes *these exact bytes*. It does not prove the
description is complete or correct — it is the record of one model's pass. Three limits
matter when a mistake is expensive:

- **Timestamps are approximate.** Boundaries inferred by a model drift (over a second
  on measured clips), miss real cuts, and occasionally mark cuts that do not exist.
  They are fine for locating, ranking, and rough trims. Before cutting on them, verify
  against the container:
  ```bash
  ffmpeg -v error -i clip.mp4 -vf "select='gt(scene,0.1)',metadata=print:file=-" -f null -
  ```
  If the header carries `x-shot-source: ffmpeg-scene-detect@...`, the boundaries were
  measured from the container and need no such check.

- **Descriptions can add, not just omit.** Shown a whole video at once, a model may
  narrate the outcome a clip implies but never shows — reporting that a task was
  completed when the footage only shows it being started. Such entries are fluent,
  specific, and indistinguishable from correct ones. Treat any claim that something
  was **finished, fixed, repaired, or achieved** as unverified: check the frames before
  relying on it, and say the sidecar is your source when you report it. An omission is
  a visible gap; an addition reads exactly like a fact.

- **Fine visual state is unreliable.** Strike-through on a list, small or stylised text,
  subtle motion, and similar details are often missed or reported at chance. If such a
  detail carries the meaning of the shot, look at the frame.

None of this argues for re-watching by default — that would forfeit the entire saving.
Verify the specific claim your decision rests on, not the whole clip.

## Generating sidecars

Needs Python ≥ 3.10 and a Gemini API key in `GEMINI_API_KEY`
(free tier: https://aistudio.google.com/apikey). Install the CLI once:

```bash
pip install "cdaf[generate] @ git+https://github.com/UditAkhourii/cdaf.git#subdirectory=cli"
```

Then:

```bash
cdaf generate <video-or-directory>      # skips sidecars that are already fresh
cdaf generate <video> --force           # regenerate even if fresh
cdaf generate ./footage --detail rich   # brief | standard | rich
```

Generation calls a paid API and takes ~10s per clip. **Ask the user before batch-
generating a large library**, and tell them roughly how many videos you are about to
process.

## Working across a footage library

- Survey coverage: `cdaf status <dir>` lists every video as FRESH/STALE/MISSING.
- To find footage matching a need ("sunset city shots"), grep the `.cdaf` files —
  never open the videos: search `*.cdaf` for the relevant keywords, then rank by the
  Segments detail.
- Batch-fill gaps: `cdaf generate <dir>` (fresh sidecars are skipped automatically).

## What NOT to do

- Do not treat a sidecar as fresh without at least the size check.
- Do not invent visual details beyond what the sidecar states; if the task needs
  information the sidecar lacks (exact colors, a specific frame), say so and fall
  back to targeted direct analysis of just the needed timestamp range.
- Do not edit `.cdaf` files by hand to "update" them — the header hash would then
  describe a video the body no longer matches. Regenerate with
  `cdaf generate <video> --force` instead.

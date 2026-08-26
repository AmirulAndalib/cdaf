---
name: cdaf
description: Read CDAF sidecar files (.cdaf) instead of processing video with vision. Use whenever a task involves understanding, summarizing, searching, editing, or selecting from video files (b-roll, raw clips, footage libraries) — check for a .cdaf sidecar FIRST before analyzing any video directly.
---

# CDAF — Cached Descriptive Asset Files

A `.cdaf` file is a timestamped, pre-computed description of a video, sitting next to
it with the same basename (`clip.mp4` → `clip.cdaf`). Reading it costs a few hundred
text tokens; analyzing the video directly costs orders of magnitude more. **Always
prefer the sidecar when it is fresh.**

## The rule

Before analyzing ANY video file (`.mp4`, `.mov`, `.mkv`, `.webm`, `.avi`, `.m4v`):

1. **Check for a sidecar**: same directory, same basename, `.cdaf` extension.
2. **Verify freshness** before trusting it (see below). A stale sidecar describes an
   older version of the video — using it is worse than not having one.
3. **If fresh**: read the sidecar and use it as ground truth for the video's content.
   Do not also process the video.
4. **If missing/stale**: fall back to direct video analysis, OR (if the `cdaf` CLI and
   a `GEMINI_API_KEY` are available) generate the sidecar first so the cost is paid
   once: `cdaf generate <video>`.

## Verifying freshness

The sidecar header carries `bytes` (file size) and `sha256` (content hash) of the
exact video it describes.

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
verifies the hash automatically before printing).

Format: a `key: value` header between `--- CDAF/1.0` and `---`, then markdown:

- `## Summary` — what the clip is
- `## Segments` — `[MM:SS.d-MM:SS.d] description` lines covering the whole video;
  these timestamps are reliable for cut lists, trims, and frame extraction
- `## Transcript` — spoken words with timestamps (or `(no speech)`)
- `## On-screen Text` — visible text with timestamps (or `(none)`)
- `## Tags` — retrieval keywords

## Working across a footage library

- Survey coverage: `cdaf status <dir>` lists every video as FRESH/STALE/MISSING.
- To find footage matching a need ("sunset city shots"), grep the `.cdaf` files —
  never open the videos: search `*.cdaf` for the relevant keywords, then rank by the
  Segments detail.
- Batch-generate missing sidecars: `cdaf generate <dir>` (skips fresh ones
  automatically).

## What NOT to do

- Do not treat a sidecar as fresh without at least the size check.
- Do not invent visual details beyond what the sidecar states; if the task needs
  information the sidecar lacks (exact colors, a specific frame), say so and fall
  back to targeted direct analysis of just the needed timestamp range.
- Do not edit `.cdaf` files by hand to "update" them — regenerate with
  `cdaf generate --force` so the hash stays truthful.

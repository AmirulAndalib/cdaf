# CDAF Specification — Version 1.0

**CDAF** (Cached Descriptive Asset File) is a sidecar file convention that pairs a video
file with a timestamped, human- and LLM-readable description of its contents, so that
AI agents can reuse a single expensive video-understanding pass instead of re-analyzing
the same footage on every task.

Status: **v1.0** · License: MIT · This document is normative. The key words MUST,
MUST NOT, SHOULD, and MAY are to be interpreted as described in RFC 2119.

---

## 1. The asset pair

A CDAF asset consists of exactly two files in the **same directory** with the
**same basename**:

```
sunset-drone.mp4    ← the video (any common container: mp4, mov, mkv, webm, avi, m4v)
sunset-drone.cdaf   ← the sidecar (UTF-8 plain text)
```

- The sidecar file extension MUST be `.cdaf`.
- The video file is untouched by this specification. It MUST remain a standard,
  independently playable video file.
- Tools locate the sidecar by replacing the video's extension with `.cdaf`, and locate
  the video via the `video` header key (falling back to a same-basename search).

## 2. Sidecar file structure

A sidecar is a UTF-8 text file (LF or CRLF line endings both valid) with two parts:
a **header block** followed by a **markdown body**.

```
--- CDAF/1.0
video: sunset-drone.mp4
sha256: 9f2c1a…64 hex chars…e0b7
bytes: 48211394
duration: 00:01:23.456
resolution: 3840x2160
fps: 29.97
generator: gemini-2.5-flash
created: 2026-08-26T14:03:12Z
detail: standard
lang: en
---

## Summary
…

## Segments
[00:00.0-00:04.2] …
```

### 2.1 Header block

- The first line MUST be `--- CDAF/<major>.<minor>` (this document defines `1.0`).
- Subsequent lines are `key: value` pairs, one per line. Keys are lowercase ASCII;
  values are free text up to end of line.
- The header ends with a line containing exactly `---`.
- Unknown keys MUST be ignored by consumers (forward compatibility). Producers MAY add
  experimental keys prefixed `x-`.

**Required keys**

| Key         | Meaning |
|-------------|---------|
| `video`     | Filename (not path) of the paired video file. |
| `sha256`    | Lowercase hex SHA-256 of the video file's bytes. The freshness anchor. |
| `bytes`     | Size of the video file in bytes. Cheap freshness pre-check. |
| `generator` | The model/tool that produced the body (e.g. `gemini-2.5-flash`). |
| `created`   | UTC timestamp, ISO 8601 (e.g. `2026-08-26T14:03:12Z`). |

**Optional keys**

| Key          | Meaning |
|--------------|---------|
| `duration`   | `HH:MM:SS.mmm` duration of the video. |
| `resolution` | `WIDTHxHEIGHT` in pixels. |
| `fps`        | Frames per second (decimal allowed). |
| `detail`     | Description depth: `brief`, `standard`, or `rich`. |
| `lang`       | BCP-47 tag of the body language (default `en`). |

### 2.2 Body

The body is GitHub-flavored markdown organized into `## `-level sections.
Consumers MUST ignore unknown sections.

**Required section**

- `## Segments` — a chronological list of timestamped descriptions covering the full
  video. One segment per line:

  ```
  [START-END] Description of what happens in this span.
  ```

  Timestamps are `MM:SS.d` or `HH:MM:SS.d` (tenths of a second suffice; higher
  precision MAY be used). The separator is a hyphen. A well-formed segment list is
  contiguous and covers `00:00.0` through the video's duration.

**Recommended sections** (include when applicable, omit when empty)

- `## Summary` — one short paragraph: what this clip is and what it's useful for.
- `## Transcript` — spoken words with timestamps and speaker labels when identifiable:
  `[MM:SS.d] Speaker: words…`. Omit for videos with no speech.
- `## On-screen Text` — visible text (titles, captions, signs, UI) with timestamps.
- `## Tags` — a single comma-separated line of retrieval keywords
  (subjects, actions, setting, mood, camera work, lighting).

### 2.3 Segment description quality

Segment descriptions exist so an agent can make editorial and analytical decisions
**without watching the video**. Producers SHOULD describe, per segment: subjects and
their actions, setting, camera framing and movement, lighting/mood, and any notable
moment an editor would cut on. Producers SHOULD state what is objectively visible
rather than speculate.

## 3. Freshness and trust

The sidecar is a **cache**, and every cache needs invalidation:

- A sidecar is **fresh** if and only if the SHA-256 of the video file's current bytes
  equals the header's `sha256` value.
- Consumers SHOULD first compare file size against `bytes` (an O(1) check); on
  mismatch the sidecar is proven stale without hashing.
- Consumers MUST NOT use a stale sidecar as a description of the video. On staleness,
  fall back to direct video analysis or regenerate the sidecar.
- Producers MUST compute `sha256` from the exact file the description was generated
  from.

## 4. Versioning

- The header's `CDAF/<version>` declares the spec version.
- Minor versions are backward compatible: a `1.x` consumer can read any `1.y` file
  (unknown keys/sections ignored).
- A major version bump signals a breaking structural change.

## 5. Rationale (informative)

- **Why a sidecar, not a container?** A bundled format would break playback,
  previews, and every existing editing tool. Sidecars have prior art that won:
  `.srt`, `.xmp`, `.sha256`.
- **Why markdown, not JSON?** The primary consumer is a language model. Markdown with
  timestamps carries the same information in materially fewer tokens than
  quoted-and-braced JSON, and stays human-readable and diffable. Structure-hungry
  tools can parse the format trivially (the header is `key: value`; segments match
  one regex).
- **Why SHA-256 in the header?** Without a cryptographic link to the video's bytes,
  edits silently poison the cache and agents act on wrong descriptions. The hash makes
  the sidecar a verifiable claim about one exact file.

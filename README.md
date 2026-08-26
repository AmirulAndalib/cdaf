# CDAF — Cached Descriptive Asset Files

**Stop making AI agents watch the same video twice.**

CDAF is an open sidecar format for video: a plain-text, timestamped description that
lives next to the video file with the same basename. Generate it once, and every AI
agent that touches that footage afterward reads a few hundred text tokens instead of
running a full video-understanding pass.

```
footage/
├── sunset-drone.mp4     ← plays everywhere, untouched
└── sunset-drone.cdaf    ← what agents read instead of watching
```

**Measured** (reproducible benchmark, `gemini-2.5-flash`, 20 questions): answering
from the sidecar matched direct video analysis on accuracy — **20/20 vs 19/20** — at
**10.1× fewer prompt tokens per question** (303 vs 3,066) and ~35% lower latency. The
ratio grows linearly with clip length (~50× for 60-second footage). In production use
at scale, this pattern cut video-workflow AI costs to roughly **1/25th**.

---

## Components

| Component | Where | What it does |
|---|---|---|
| **[Spec](#-spec)** | [SPEC.md](SPEC.md) | The normative format definition (v1.0) |
| **[Engine](#-engine-core-library)** | [cli/cdaf/](cli/cdaf/) | Python library: parse, validate, hash, generate |
| **[CLI](#-cli)** | [cli/](cli/) | `cdaf` command: generate / validate / read / status |
| **[Agent Skill](#-agent-skill)** | [skills/](skills/claude-code/cdaf/SKILL.md) | Teaches agents the sidecar-first protocol |
| **[Benchmarks](#-benchmarks)** | [benchmarks/](benchmarks/) | Reproducible eval: sidecar vs direct video |
| **[Paper](#-paper)** | [paper/PREPRINT.md](paper/PREPRINT.md) | arXiv preprint draft built on the benchmark |

---

## 📄 Spec

A CDAF asset is `clip.mp4` + `clip.cdaf`: a UTF-8 sidecar with a minimal `key: value`
header and a markdown body. The header carries the **SHA-256 and byte size of the
exact video described** — edit the video and every conforming tool detects the sidecar
as stale and refuses to use it. The body is optimized for the actual reader (an LLM):

```
--- CDAF/1.0
video: sunset-drone.mp4
sha256: 4a7d1ed4…
bytes: 48211394
duration: 00:00:31.500
generator: gemini-2.5-flash
created: 2026-08-26T14:03:12Z
---

## Summary
A 31-second aerial drone clip of a coastal highway at golden hour…

## Segments
[00:00.0-00:05.2] Wide aerial establishing shot: a two-lane coastal highway…
[00:05.2-00:12.8] The drone pushes in and descends toward the highway…

## Transcript
(no speech)

## On-screen Text
(none)

## Tags
drone, aerial, coastal highway, golden hour, sunset, b-roll, …
```

Full normative rules (freshness semantics, versioning, section grammar):
**[SPEC.md](SPEC.md)**. A complete example:
[examples/sunset-drone.cdaf](examples/sunset-drone.cdaf).

## ⚙️ Engine (core library)

The `cdaf` Python package is split by dependency weight:

- **Zero-dependency core** ([sidecar.py](cli/cdaf/sidecar.py)) — parse, serialize,
  chunked SHA-256, freshness checking, segment extraction. Agents and CI can validate
  sidecars with nothing but the standard library.
- **Generator** ([generate.py](cli/cdaf/generate.py)) — Gemini Files API,
  bring-your-own-key, `brief`/`standard`/`rich` detail profiles, token-usage capture.
  Only imported when you generate.
- **Probe** ([probe.py](cli/cdaf/probe.py)) — optional ffprobe metadata
  (duration/resolution/fps), degrades gracefully when ffprobe is absent.

```python
from cdaf import load, check_freshness, sidecar_path_for

sc = load(sidecar_path_for("footage/sunset-drone.mp4"))
if check_freshness("footage/sunset-drone.mp4", sc) == "fresh":
    context_for_llm = sc.body
```

The format is model-agnostic: the engine's Gemini backend is one implementation, and
Claude/GPT/local-VLM backends slot in behind the same `Sidecar` type.

## 💻 CLI

Requires Python ≥ 3.10. Bring your own Gemini API key
([free tier available](https://aistudio.google.com/apikey)) — your footage goes to
your key, not to us.

```bash
pip install ./cli[generate]
export GEMINI_API_KEY=your-key          # PowerShell: $env:GEMINI_API_KEY="your-key"

cdaf generate ./footage                 # describe every video, skip fresh sidecars
cdaf status ./footage                   # FRESH / STALE / MISSING report
cdaf read ./footage/sunset-drone.mp4    # print the description (verifies hash first)
cdaf validate ./footage/clip.mp4        # exit 0 iff sidecar is well-formed and fresh
```

Flags: `--detail brief|standard|rich`, `--model <gemini-model>`, `--force`.
`validate`/`read`/`status` need **no dependencies and no API key**. Safety lives in
the tool, not just in guidance: `cdaf read` refuses to print a stale sidecar.

## 🤖 Agent Skill

The skill turns the format into behavior: **before analyzing any video, check for the
sidecar; verify freshness (size check for exploration, full hash for consequential
decisions); read it instead of watching; grep `.cdaf` files to search whole libraries;
regenerate when stale.**

Claude Code:

```bash
cp -r skills/claude-code/cdaf ~/.claude/skills/cdaf
```

(Windows: copy `skills\claude-code\cdaf` to `%USERPROFILE%\.claude\skills\cdaf`.)

Any other agent framework — the whole contract is one system-prompt paragraph:

> Before analyzing a video file, look for a `.cdaf` file with the same basename. If
> its `bytes`/`sha256` header matches the video file, read it instead of processing
> the video.

### Why agentic video editors care

Programmatic and agent-native editors (Remotion, HyperFrames, prompt-to-edit tools)
already do everything in text — compositions are code, cut decisions are data. Video
understanding is the one step that forces them out of the text domain, priced per
exposure (~263 tokens per second of footage on Gemini-class models). With CDAF:

- **Selection becomes retrieval** — "sunset coastal shots, no people" is a grep over
  `.cdaf` files, not a multimodal sweep of the library.
- **Cut lists come from segment timestamps** — `[00:05.2-00:12.8]` lines map straight
  to Remotion `<Sequence>`s or HyperFrames clips.
- **Captions and audio sync for free** — the Transcript section aligns speech to the
  timeline.
- **The library appreciates** — 40 candidate clips ≈ 473k tokens to watch, ~24k to
  read — every session after the first.

## 📊 Benchmarks

Fully reproducible: [bench.py](benchmarks/bench.py) synthesizes test videos with
ffmpeg from scripted recipes (known colors, hard cuts, timed word overlays), so ground
truth is exact and grading is objective — no LLM judge, no dataset license.

```bash
python benchmarks/bench.py make     # synthesize testset (needs ffmpeg)
python benchmarks/bench.py run     # both conditions via Gemini (needs GEMINI_API_KEY)
python benchmarks/bench.py report  # writes benchmarks/RESULTS.md
```

| Condition | Accuracy | Mean prompt tokens/question | Mean latency |
|---|---|---|---|
| Direct video | 19/20 (95%) | 3,066 | 3.46 s |
| **CDAF sidecar** | **20/20 (100%)** | **303** | **2.24 s** |

One-time generation amortizes after ~1.2 direct questions per video. Full per-question
detail: [benchmarks/RESULTS.md](benchmarks/RESULTS.md).

## 📝 Paper

**[paper/PREPRINT.md](paper/PREPRINT.md)** — *Cached Descriptive Asset Files (CDAF):
A Sidecar Format for Token-Efficient Video Understanding in Agentic Pipelines* —
arXiv draft (cs.MM / cs.AI): format rationale, benchmark methodology and results,
production case study, integration analysis for agentic editors, limitations.

## Roadmap

- MCP server exposing `cdaf_read` / `cdaf_status` / `cdaf_generate` to any MCP client
- Hosted free converter (no local install; bring-your-own-key or free quota)
- Additional generator backends (Claude, GPT, local VLMs)
- Node/TypeScript port of the engine
- Natural-footage benchmark extension with human-verified QA
- Signed sidecars (`signature` header) for cross-organization trust

## License

MIT — see [LICENSE](LICENSE).

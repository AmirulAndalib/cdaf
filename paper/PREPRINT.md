# Cached Descriptive Asset Files (CDAF): A Sidecar Format for Token-Efficient Video Understanding in Agentic Pipelines

**Author:** Udit \<full name\>
**Affiliations:** Indian Institute of Technology Patna · Emora Health
**Contact:** udit@sevaai.co
*Independent research; production deployment data from Emora Health.*
*Draft for arXiv (cs.MM, cross-list cs.AI). Status: working draft — results section is
populated by `benchmarks/RESULTS.md` from the reproducible harness in this repository.*

---

## Abstract

Multimodal language models can understand video, but at a steep and *recurring* cost:
in agentic workflows, the same assets — b-roll, product footage, raw clips — are
re-analyzed on every task that touches them, because the model's understanding is
discarded when the task ends. We present **CDAF** (Cached Descriptive Asset Files), an
open sidecar format that persists one video-understanding pass as a plain-text,
timestamped description stored next to the video file and cryptographically bound to
its exact bytes via SHA-256. Agents read the sidecar — a few hundred text tokens —
instead of re-processing the video — tens of thousands of multimodal tokens.
On a fully reproducible synthetic benchmark with objective ground truth, sidecar-mediated
question answering matches direct video analysis on accuracy (100% vs 95% in our run)
while using **10.1× fewer prompt tokens per question** and ~35% less latency; the
one-time generation cost amortizes after roughly one direct question per video, and
the token ratio grows linearly with clip duration (~50× for 60-second footage). In production use at Emora Health, the same pattern reduced
AI costs of video-heavy creative workflows to approximately **1/25th**. We release the
format specification, a reference implementation, an agent skill, and the benchmark
harness under the MIT license.

## 1. Introduction

Agentic AI systems increasingly operate on video: selecting b-roll for an edit,
generating cut lists, writing captions, answering questions about footage. Modern
multimodal models make this possible but price it per exposure — for example, Gemini
models tokenize video at roughly 263 tokens per second of footage at default sampling
[10], so a single 60-second clip costs ~16,000 prompt tokens *every time any agent
looks at it*. Video assets, unlike one-off inputs, are **reused**: a stock library
clip may be considered by hundreds of editing sessions. The industry-standard remedy
for repeated computation — caching — has no standard carrier for video understanding.

Our position is that the cache should be (1) a *file next to the asset*, not a row in
a proprietary database, so it travels with the footage through file systems, object
stores, git-LFS repositories, and NLEs; (2) *plain text optimized for LLM consumption*,
because the primary reader is a language model; and (3) *verifiable*, because a cache
that can silently go stale is worse than no cache: an agent acting on a description of
a previous version of a video produces confidently wrong output.

CDAF operationalizes this: `clip.mp4` is paired with `clip.cdaf`, a UTF-8 file with a
minimal `key: value` header — including the SHA-256 and byte size of the exact video
described — followed by a markdown body containing a summary, contiguous timestamped
segment descriptions, a transcript, on-screen text, and retrieval tags.

**Contributions.**
1. The CDAF format specification (v1.0): a minimal, versioned, model-agnostic sidecar
   convention with hash-anchored freshness semantics (§3).
2. A reference implementation: a zero-dependency validator/reader and a
   bring-your-own-key generator, plus an agent skill that changes agent behavior from
   "watch the video" to "read the sidecar, verified" (§4).
3. A reproducible benchmark with objective ground truth measuring the
   accuracy/cost/latency trade-off of sidecar-mediated video understanding, and a
   production case study (§5).
4. An analysis of CDAF's role in agentic video editing pipelines (Remotion-class
   programmatic editors, HyperFrames-class agentic editors), where footage selection
   and cut-list generation become text retrieval problems (§6).

## 2. Related Work

**Dense video captioning.** Producing timestamped natural-language descriptions of
video is a long-studied task; Vid2Seq [1] and successors generate temporally grounded
captions end-to-end, and modern multimodal LLMs [2, 10] perform it zero-shot. CDAF is
complementary: it standardizes *where the output lives, how it is formatted for LLM
consumption, and how it is kept truthful* — not the captioning model itself.

**Video LLM benchmarks.** Video-MME [3], MVBench [4], and related suites evaluate
direct video understanding. Our benchmark instead evaluates a *systems* question: how
much task performance survives when the video is replaced by its cached description,
at what token cost. Our synthetic-ground-truth design trades visual realism for exact,
license-free reproducibility.

**Semantic and prompt caching.** Response caches for LLM serving (e.g., GPTCache [5])
and provider-side prompt caching [6] reduce repeated computation within a serving
stack. CDAF is an *asset-side* cache: it persists understanding across sessions,
tools, machines, and organizations, because it is a file, not server state.

**Sidecar metadata.** Sidecars are proven practice: SubRip subtitles, Adobe XMP [7]
for RAW imagery, checksum files. MPEG-7 [8] attempted rich standardized video
description but targeted machine parsers, predating LLM consumers; its complexity
limited adoption. CDAF's bet is the opposite: the schema is *prose*, because the
consumer can read.

**Retrieval-augmented pipelines.** Once footage libraries carry CDAF sidecars, clip
retrieval reduces to text search over `.cdaf` files — inheriting the entire text
RAG toolchain (grep, BM25, embeddings) for video, without a video-specific index.

## 3. The CDAF Format

*(Normative details in SPEC.md; summarized here.)*

A CDAF asset is a video file plus a `.cdaf` sidecar sharing its basename. The sidecar
has a header block and a markdown body:

```
--- CDAF/1.0
video: sunset-drone.mp4
sha256: 4a7d1ed4…            ← freshness anchor: hash of the exact video bytes
bytes: 48211394               ← O(1) staleness pre-check
duration: 00:00:31.500
generator: gemini-2.5-flash
created: 2026-08-26T14:03:12Z
---

## Summary
## Segments        ← required: contiguous [MM:SS.d-MM:SS.d] descriptions
## Transcript
## On-screen Text
## Tags
```

**Freshness semantics.** A sidecar is fresh iff SHA-256(video bytes) equals the
header value. Consumers MUST NOT use a stale sidecar; the byte-size field lets them
prove staleness in O(1) before paying for a hash. This converts the sidecar from an
advisory annotation into a verifiable claim about one exact file — the property that
makes it safe for autonomous agents to trust without human review.

**Design rationale.** *Sidecar over container:* embedding descriptions inside the
media file (MP4 metadata tracks) breaks tool compatibility and makes text opaque to
grep/git; a sidecar keeps the video playable everywhere. *Markdown over JSON:* the
primary consumer is an LLM; structural punctuation is wasted tokens, and prose
descriptions of prose-shaped content are more token-dense and human-auditable.
*Model-agnostic:* the `generator` field records provenance, but nothing in the format
assumes a particular captioning model — quality floors can rise with each model
generation by regenerating sidecars, without format changes.

## 4. Reference Implementation

The `cdaf` Python package separates concerns by dependency weight: **validation,
reading, and library-status reporting are dependency-free** (agents and CI can verify
freshness without any AI SDK), while **generation** uses the Gemini Files API under a
user-supplied key. `cdaf generate` is idempotent — it skips fresh sidecars, making
"describe my whole library" a safely re-runnable batch operation. `cdaf read` refuses
to print a stale sidecar, placing the safety check inside the tool an agent calls
rather than in guidance it might ignore.

An accompanying agent skill (Claude Code format, trivially portable as a system-prompt
paragraph) instructs agents to: check for the sidecar before any video analysis;
verify freshness (size check for exploration, full hash for consequential decisions);
answer from the sidecar when fresh; and fall back to targeted direct analysis only for
information the sidecar lacks.

## 5. Evaluation

### 5.1 Reproducible synthetic benchmark

Public video-QA benchmarks have licensing constraints and subjective answers. We
instead *synthesize* the testset: ffmpeg-scripted videos composed of solid-color
scenes with hard cuts and timed word overlays. Because every video is generated from a
declarative recipe, ground truth (scene colors, cut counts, word identities and onset
times) is exact, and grading is string/number matching with stated tolerances — no LLM
judge, no human annotation, no dataset license. The harness (`benchmarks/bench.py`)
regenerates everything from scratch on any machine with ffmpeg.

Each question is answered under two conditions by the same model: **direct** (video +
question) and **cdaf** (sidecar body + question). We record correctness, prompt
tokens, and latency, plus the one-time sidecar generation cost.

### 5.2 Results

**Table 1** — 4 synthetic videos (10–12 s each), 20 questions, both conditions
answered by `gemini-2.5-flash` (run of 2026-08-26; regenerate with
`python bench.py run && python bench.py report`):

| Condition | Accuracy | Mean prompt tokens / question | Mean latency (s) |
|---|---|---|---|
| Direct video | 19/20 (95%) | 3,066 | 3.46 |
| CDAF sidecar | **20/20 (100%)** | **303** | **2.24** |

The sidecar condition matched — in this run, slightly exceeded — direct-video accuracy
at **10.1× fewer prompt tokens per question** and ~35% lower latency. The single
direct-video error (miscounting hard cuts in a video with repeated scenes) is
anecdotal but illustrative: the sidecar is produced by *one careful, dedicated
description pass*, whereas direct answering re-derives structure from pixels on every
query; the cache can be more reliable than the thing it caches. One-time generation
cost was 14,405 tokens across all four videos (~3,600/video, roughly the cost of a
single direct question) — the sidecar pays for itself after **~1.2 direct questions
per video**, and everything afterward is the 10× saving.

Two effects compound beyond the headline ratio. First, the token economics scale with
duration: direct-video cost grows linearly with length (~256 tokens/s observed,
consistent with Gemini's documented ~263 tokens/s [10]), while sidecar size grows with
*content complexity* and stays near-constant per question (303 tokens here). On these
deliberately short 10–12 s clips the ratio is 10×; on a typical 60 s b-roll clip the
same arithmetic yields ~50×, and a 10-minute locked-off shot costs 150k+ tokens to
look at and a few hundred to read about. Second, latency drops because video
upload/processing disappears from the critical path of every downstream task.

### 5.3 Production case study

Before formalization as CDAF, this pipeline (Gemini-generated timestamped descriptions
stored alongside footage) ran in production in Emora Health's video-creation
workflows. Pre-describing reusable b-roll and raw clips reduced the AI cost of video
production to approximately **1/25th** of the direct-analysis baseline. We report this
as an observational data point rather than a controlled result; the benchmark of §5.1
exists precisely to make the effect independently verifiable.

*(TODO before submission: add one paragraph of workload detail — clips per project,
average clip length, tasks per clip — to make the 1/25 figure interpretable.)*

## 6. CDAF in Agentic Video Editing

Programmatic and agentic editors — Remotion (React-defined video), HyperFrames
(HTML/agent-native compositions), and the emerging class of "prompt-to-edit" tools —
are the highest-leverage consumers of CDAF, because their editing loop is *already
text*: compositions are code, cut decisions are data structures, and an LLM sits in
the loop. Video understanding is the only step that forces them out of the text
domain. CDAF removes that step for every asset already described:

1. **Footage selection becomes retrieval.** "Find sunset coastal shots with no people"
   is a grep/embedding search over `.cdaf` files across the whole library — feasible
   per keystroke — instead of a multimodal sweep costing ~16k tokens per minute of
   candidate footage.
2. **Cut lists come from segment timestamps.** CDAF's contiguous `[start-end]`
   segments give the agent editorial units: it can propose in/out points, match
   b-roll beats to narration, and emit a Remotion `<Sequence>`/HyperFrames clip list
   directly from sidecar text — no frame extraction until final verification.
3. **Caption and audio sync for free.** The Transcript and On-screen Text sections
   align spoken content to the timeline, letting the agent place captions, avoid
   burning titles over existing text, and duck music under speech.
4. **The library appreciates.** Each described asset is described forever (until its
   bytes change). An organization's footage library becomes a text corpus that any
   agent, model, or tool can query at text prices — including models that cannot
   process video at all.

Concretely, an agent building a 60-second edit that considers 40 candidate clips
(mean 45s) pays ~473k prompt tokens to view them directly, once, for that session.
With sidecars (~600 tokens each) the same consideration costs ~24k tokens — and the
next session, the next agent, and the next model pay the same 24k, not another 473k.

## 7. Limitations

- **The cache inherits its generator's ceiling.** A sidecar cannot answer questions
  its generation pass didn't anticipate (exact pixel colors, fine motion). The format
  mitigates via `detail` profiles and graceful fallback to targeted direct analysis,
  but does not eliminate the gap. Our benchmark's questions are answerable from a
  competent description; adversarially fine-grained questions would favor direct
  analysis.
- **Descriptions are not embeddings.** Semantic visual similarity search may still
  want vector indexes; CDAF's Tags/Segments support lexical and text-embedding
  retrieval only. (A future optional section could carry embedding pointers.)
- **Synthetic benchmark realism.** Our testset isolates the caching question with
  objective ground truth but does not measure description quality on natural footage;
  the production case study is evidence, not proof. A natural-footage evaluation with
  human-verified QA is the clear next step.
- **Trust at the ecosystem level.** The hash binds a sidecar to bytes, not to *truth*:
  a maliciously authored sidecar describes the video however its author likes.
  Signed sidecars (a `signature` header key) are a natural v1.x extension.

## 8. Conclusion

CDAF turns video understanding from a per-task expense into a per-asset investment,
using nothing more exotic than a text file, a hash, and a convention — deliberately
boring machinery, chosen because boring machinery is what gets adopted. The
specification, tooling, agent skill, and benchmark are open source (MIT) at
**https://github.com/UditAkhourii/cdaf**.

## References

[1] A. Yang et al., "Vid2Seq: Large-Scale Pretraining of a Visual Language Model for
Dense Video Captioning," CVPR 2023.
[2] Gemini Team, Google, "Gemini 1.5: Unlocking multimodal understanding across
millions of tokens of context," arXiv:2403.05530, 2024.
[3] C. Fu et al., "Video-MME: The First-Ever Comprehensive Evaluation Benchmark of
Multi-modal LLMs in Video Analysis," arXiv:2405.21075, 2024.
[4] K. Li et al., "MVBench: A Comprehensive Multi-modal Video Understanding
Benchmark," CVPR 2024.
[5] F. Bang, "GPTCache: An Open-Source Semantic Cache for LLM Applications," NLP-OSS
2023.
[6] Anthropic, "Prompt caching with Claude," 2024. https://www.anthropic.com/news/prompt-caching
[7] Adobe Systems, "XMP Specification Part 1: Data Model, Serialization, and Core
Properties," ISO 16684-1.
[8] ISO/IEC 15938, "Multimedia Content Description Interface (MPEG-7)," 2002.
[9] Remotion, "Make videos programmatically." https://remotion.dev
[10] Google, "Gemini API: Video understanding — tokenization of video input."
https://ai.google.dev/gemini-api/docs/video-understanding

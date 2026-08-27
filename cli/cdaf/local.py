"""Generate a CDAF body with a local multimodal model (OpenAI-compatible endpoint).

An alternative to the Gemini provider in generate.py. No API key, no per-clip cost,
and the footage never leaves the machine. Requires ffmpeg and a served model with a
vision encoder; an audio encoder is used for the transcript when present.

The Files API path (upload whole video, ask once) is not available locally, so this
provider samples frames itself. That turns out to be an advantage rather than a
workaround:

  * Segment boundaries come from ffmpeg scene detection, so the boundaries it finds
    are frame-exact rather than inferred. Recall depends on --scene-threshold: a
    low-contrast transition can score below it and be missed, merging two shots.
  * Each shot is described on its own. A describer that cannot see the next shot
    cannot invent a causal chain across shots.
  * Transcript timing is measured from a speech-band energy envelope. Models
    transcribe accurately but are poor at timestamps.

No third-party dependencies: stdlib urllib only.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from .generate import GenerationError

DEFAULT_BASE_URL = os.environ.get("CDAF_BASE_URL", "http://127.0.0.1:8090/v1")
DEFAULT_LOCAL_MODEL = os.environ.get("CDAF_LOCAL_MODEL", "local")
HTTP_TIMEOUT = 900
FRAME_WIDTH = 896

# Frames sampled per shot, and sentences requested, by --detail level.
_DETAIL_FRAMES = {"brief": 1, "standard": 2, "rich": 3}
_DETAIL_SENTENCES = {"brief": "one short sentence", "standard": "1-2 sentences",
                     "rich": "2-4 sentences covering subjects, actions, setting, "
                             "camera framing and movement, lighting, colour and mood"}

_SHOT_PROMPT = """You are describing ONE shot from a video. You are shown {n} frame(s) \
sampled from inside this single shot, in order.

Describe what is visible in these frames, in {sentences}.

- Do NOT say a task was completed, fixed, or finished unless the finished result is \
plainly visible. Someone holding a tool or a detached part is intent, not completion.
- Do NOT invent motion you cannot see across the given frames, and do not guess at what \
happened before or after this shot.
- Name what you can identify. If an object is clearly a screwdriver or a light bulb, say \
so; fall back on shape and colour only when you genuinely cannot tell.
- Transcribe visible text EXACTLY as written, including any list numbering. Report only \
deliberate, legible text: burned-in titles and graphics, signage, screens, handwriting \
the shot is showing you. Ignore incidental text on background packaging or labels, and \
never guess a brand from a partly visible logo.

Reply with ONLY a JSON object, no prose or code fences:
{{"description": "...", "visible_text": ["exact text"], "setting": "short location phrase"}}

Use an empty list for visible_text if there is no legible text."""

_CONTINUITY_PROMPT = """These descriptions of one video's shots were written \
independently -- each describer saw only its own shot. So the same person and place are \
reintroduced from scratch every time, and objects are described by shape when the \
surrounding shots make their identity obvious.

Rewrite the list so it reads as one document about one video.

You MAY: introduce the subject once and then use a pronoun; name a place once and then \
refer back to it; name an object when neighbouring shots or the on-screen text make its \
identity unambiguous; label a shot as a POV, insert or close-up when the neighbours make \
that clear; quote on-screen text where it carries meaning.

You MUST NOT: add any action or event that is not in the input; say a task was completed \
unless an input says the finished result is visible; add a colour or material that is not \
in the input for that shot; drop or merge shots. Return exactly {n} descriptions in the \
same order, {sentences} each.

Then a Summary paragraph: what the clip is, its arc, anything it names or advertises, and \
what it would be useful for. Then 12-20 retrieval keywords.

Reply with ONLY a JSON object, no prose or code fences:
{{"segments": ["..."], "summary": "...", "tags": ["keyword"]}}

Shots:
{shots}

Narration:
{narration}

On-screen text:
{screen_text}"""

_ASR_PROMPT = """Transcribe the spoken narration in this audio verbatim.

This audio contains exactly {n} spoken segments, separated by pauses. Output exactly {n} \
lines, one per segment, in order. No timestamps, no numbering, no blank lines -- just the \
words. If you hear fewer distinct segments, still split the narration into {n} lines at \
the most natural pauses."""

# Replies that are the assistant talking rather than transcribing. Asked to transcribe
# silence, a model answers as a chatbot instead of declining, and that lands in the
# sidecar looking like narration.
#
# Two tiers, because the obvious keyword list also matches things real people say on
# camera -- "Sorry, I'm late", "I cannot believe this", "There is no better way":
#
#   ANYWHERE: assistant self-reference and explicit meta-refusals about transcribing.
#             Not plausible as narration, so a match anywhere in the opening is enough.
#   OPENING:  refusal phrasings that ARE plausible narration. A speaker can say them
#             mid-sentence; an assistant's refusal *opens* with them. Anchored to the
#             start of the reply, which costs us only a transcript whose very first
#             words are a refusal phrase.
_NON_TRANSCRIPT_ANYWHERE = (
    "large language model", "i am an ai", "i'm an ai", "as an ai",
    "cannot transcribe", "can't transcribe", "unable to transcribe",
    "no speech is", "no audible speech", "no discernible speech",
)

# Phrasings a refusal *opens* with. Each is also something a person might say on
# camera, so an opener alone is not enough -- see _is_not_a_transcript.
_REFUSAL_OPENERS = (
    "sorry", "unfortunately", "i cannot", "i can't", "i'm unable", "i am unable",
    "there is no", "there's no", "no speech", "no audible", "no discernible",
    "it appears", "it seems", "please provide",
    "the audio is", "the audio contains", "the audio appears", "the audio does",
    "this audio is", "this audio contains", "this audio appears",
)

# A refusal is *about* the recording. Ordinary narration that merely opens with
# "Sorry" or "I can't" is not.
_AUDIO_NOUNS = (
    "audio", "speech", "sound", "silen", "narration", "track", "recording",
    "transcri", "spoken", "voice", "dialogue", "words",
)


def _is_not_a_transcript(raw: str) -> bool:
    """True when a reply reads as the assistant talking rather than transcribing.

    Erring toward discarding is deliberate: a dropped transcript is a visible gap,
    while a confabulated one reads exactly like narration and cannot be caught
    downstream. But the error is not free, so a generic opener must be paired with
    a reference to the recording before the reply is thrown away.
    """
    head = raw.strip().lower()
    if not head:
        return True
    if any(marker in head[:200] for marker in _NON_TRANSCRIPT_ANYWHERE):
        return True
    opening = head.lstrip("\"'“‘([-— ")
    if not opening.startswith(_REFUSAL_OPENERS):
        return False
    first_sentence = re.split(r"[.!?\n]", opening, maxsplit=1)[0]
    return any(noun in first_sentence for noun in _AUDIO_NOUNS)


# --------------------------------------------------------------------- ffmpeg

def _run(cmd: list[str], timeout: int = 300) -> str:
    return subprocess.run(cmd, capture_output=True, text=True,
                          timeout=timeout, check=True).stdout


def _require_ffmpeg() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise GenerationError(
                f"{tool} is required by the local provider but was not found on PATH"
            )


def _duration(video: Path) -> float:
    try:
        return float(_run(["ffprobe", "-v", "error", "-show_entries",
                           "format=duration", "-of", "csv=p=0", str(video)]).strip())
    except (subprocess.SubprocessError, ValueError, OSError) as e:
        raise GenerationError(f"could not read duration of {video.name}: {e}") from e


def detect_cuts(video: Path, threshold: float = 0.15) -> list[float]:
    """Scene-change timestamps in seconds, ascending."""
    # metadata=print writes to stdout so it survives `-v error`; showinfo logs at info
    # level and would be swallowed by the same flag.
    try:
        proc = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(video),
             "-vf", f"select='gt(scene,{threshold})',metadata=print:file=-",
             "-f", "null", "-"],
            capture_output=True, text=True, timeout=900)
    except (subprocess.SubprocessError, OSError):
        return []
    return sorted({round(float(m), 2)
                   for m in re.findall(r"pts_time:([0-9.]+)", proc.stdout)})


def build_shots(duration: float, cuts: list[float],
                min_len: float = 0.35) -> list[tuple[float, float]]:
    """Cut points to [start, end) spans, absorbing sub-min_len slivers."""
    bounds = [0.0] + [c for c in cuts if min_len < c < duration - min_len] + [duration]
    shots: list[tuple[float, float]] = []
    for a, b in zip(bounds, bounds[1:]):
        if b - a >= min_len:
            shots.append((a, b))
        elif shots:
            shots[-1] = (shots[-1][0], b)
    return shots or [(0.0, duration)]


def coarsen(shots: list[tuple[float, float]],
            target: float = 8.0) -> list[tuple[float, float]]:
    """Merge adjacent shots toward `target` seconds, for --detail brief."""
    out: list[tuple[float, float]] = []
    for start, end in shots:
        if out and out[-1][1] - out[-1][0] < target:
            out[-1] = (out[-1][0], end)
        else:
            out.append((start, end))
    return out


def _sample_times(start: float, end: float, n: int) -> list[float]:
    """n frames strictly inside the shot, away from transition frames at the edges."""
    span = end - start
    if n <= 1 or span < 1.2:
        return [start + span * 0.5]
    step = 0.6 / max(n - 1, 1)
    return [start + span * (0.2 + step * i) for i in range(n)]


def _frame(video: Path, t: float, dest: Path) -> Path | None:
    """Frame-accurate grab. -ss must follow -i, or ffmpeg snaps to a keyframe."""
    try:
        _run(["ffmpeg", "-v", "error", "-i", str(video), "-ss", f"{t:.3f}",
              "-frames:v", "1", "-vf", f"scale={FRAME_WIDTH}:-1",
              "-q:v", "3", str(dest), "-y"], timeout=120)
    except (subprocess.SubprocessError, OSError):
        return None
    return dest if dest.exists() and dest.stat().st_size else None


def speech_spans(video: Path, min_gap: float = 0.45,
                 min_len: float = 0.50) -> list[tuple[float, float]]:
    """Measured speech regions from a 300-3400 Hz RMS envelope.

    Band-limiting keeps scored music from dominating. A window counts as speech when
    it rises well above the track's own noise floor. Spans under min_len are dropped:
    on scored footage those are percussion accents, not narration.
    """
    try:
        proc = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000",
             "-af", "highpass=f=300,lowpass=f=3400,asetnsamples=n=1600,"
                    "astats=metadata=1:reset=1,"
                    "ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-",
             "-f", "null", "-"],
            capture_output=True, text=True, timeout=900)
    except (subprocess.SubprocessError, OSError):
        return []

    frames: list[tuple[float, float]] = []
    t: float | None = None
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line.startswith("frame:"):
            m = re.search(r"pts_time:([0-9.]+)", line)
            t = float(m.group(1)) if m else None
        elif "RMS_level=" in line and t is not None:
            try:
                frames.append((t, float(line.split("=", 1)[1])))
            except ValueError:
                pass
            t = None
    if len(frames) < 5:
        return []

    levels = sorted(v for _, v in frames if v > -120)
    if not levels:
        return []
    floor = levels[len(levels) // 5]
    ceil_ = levels[int(len(levels) * 0.98)]
    thresh = floor + max(6.0, (ceil_ - floor) * 0.45)

    spans: list[list[float]] = []
    for ts_, lvl in frames:
        if lvl < thresh:
            continue
        if spans and ts_ - spans[-1][1] <= min_gap:
            spans[-1][1] = ts_ + 0.1
        else:
            spans.append([ts_, ts_ + 0.1])
    return [(a, b) for a, b in spans if b - a >= min_len]


# ---------------------------------------------------------------------- model

class _Endpoint:
    def __init__(self, base_url: str, model: str):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model = model

    def _post(self, payload: dict) -> str:
        req = urllib.request.Request(
            self.url, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
                body = json.load(r)
        except urllib.error.HTTPError as e:
            detail = e.read()[:300].decode(errors="replace")
            raise GenerationError(f"{self.url} returned {e.code}: {detail}") from e
        except urllib.error.URLError as e:
            raise GenerationError(
                f"cannot reach {self.url} ({e.reason}). Is the local server running?"
            ) from e
        try:
            return body["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as e:
            raise GenerationError(f"unexpected response from {self.url}") from e

    def _base(self, max_tokens: int) -> dict:
        return {
            "model": self.model,
            "max_tokens": max_tokens,
            # Gemma 4's recommended sampling. Near-greedy sampling degrades these
            # models and can make them skip parts of a prompt outright.
            "temperature": 1.0, "top_p": 0.95, "top_k": 64,
            # Some servers default reasoning ON, which spends the whole budget in
            # reasoning_content and returns empty content. Ignored where unsupported.
            "chat_template_kwargs": {"enable_thinking": False},
            "reasoning_budget": 0,
        }

    def ask(self, prompt: str, images: list[Path] | None = None,
            max_tokens: int = 1200) -> str:
        content: list[dict] = [{"type": "text", "text": prompt}]
        for p in images or []:
            b64 = base64.b64encode(p.read_bytes()).decode()
            content.append({"type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
        return self._post({**self._base(max_tokens),
                           "messages": [{"role": "user", "content": content}]})

    def ask_audio(self, prompt: str, wav: Path, max_tokens: int = 900) -> str:
        b64 = base64.b64encode(wav.read_bytes()).decode()
        return self._post({**self._base(max_tokens), "messages": [{
            "role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "input_audio", "input_audio": {"data": b64, "format": "wav"}},
            ]}]})


def _parse_json(raw: str) -> dict:
    """Lenient JSON extraction: strips fences, tolerates trailing prose."""
    s = re.sub(r"^\s*```(?:json)?|```\s*$", "", raw.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    depth, start = 0, None
    for i, ch in enumerate(s):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    return json.loads(s[start:i + 1])
                except json.JSONDecodeError:
                    start = None
    raise ValueError("no JSON object in model output")


def _salvage(raw: str) -> dict:
    """Recover a description from JSON cut off mid-object by a token limit.

    A description that ran past max_tokens is still a good description; only the
    closing brace is missing.
    """
    out: dict = {}
    m = re.search(r'"description"\s*:\s*"((?:[^"\\]|\\.)*)"', raw)
    partial = False
    if not m:
        m = re.search(r'"description"\s*:\s*"((?:[^"\\]|\\.)*)$', raw)
        partial = bool(m)
    if not m:
        return out
    text = m.group(1)
    try:
        text = json.loads(f'"{text}"')
    except json.JSONDecodeError:
        pass
    if partial:
        cut = max(text.rfind("."), text.rfind("!"), text.rfind("?"))
        text = text[:cut + 1] if cut > 20 else text.rsplit(" ", 1)[0]
    if text.strip():
        out["description"] = text.strip()
    return out


# ------------------------------------------------------------------ transcript

def _transcribe(video: Path, endpoint: _Endpoint) -> list[tuple[float, str]]:
    """Words from the model's audio encoder, timestamps from measurement.

    Returns [] when no speech is measured -- without asking the model, because a
    model handed a silent track answers conversationally rather than declining.
    """
    spans = speech_spans(video)
    if not spans:
        return []
    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "audio.wav"
        try:
            _run(["ffmpeg", "-v", "error", "-i", str(video), "-vn",
                  "-ac", "1", "-ar", "16000", str(wav), "-y"], timeout=300)
        except (subprocess.SubprocessError, OSError):
            return []
        if not wav.exists():
            return []
        try:
            raw = endpoint.ask_audio(_ASR_PROMPT.format(n=len(spans)), wav)
        except GenerationError:
            return []          # no audio encoder, or the endpoint refused the payload

    if _is_not_a_transcript(raw):
        return []
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    if len(lines) != len(spans):
        joined = re.sub(r"\s+", " ", " ".join(lines)).strip()
        lines = [p.strip() for p in
                 re.split(r'(?<=[.!?])\s+(?=[A-Z"\'“])', joined) if p.strip()]
    if not lines:
        return []
    if len(lines) == len(spans):
        return [(spans[i][0], lines[i]) for i in range(len(lines))]
    # Counts diverged: keep order, place proportionally inside the measured window.
    start, end = spans[0][0], max(spans[-1][1], spans[0][0] + 0.5)
    total = sum(len(x) for x in lines) or 1
    out, acc = [], 0
    for line in lines:
        out.append((round(start + (end - start) * acc / total, 1), line))
        acc += len(line)
    return out


# -------------------------------------------------------------------- assembly

def _ts(seconds: float) -> str:
    m, s = divmod(seconds, 60)
    if m >= 60:
        h, m = divmod(int(m), 60)
        return f"{h:02d}:{int(m):02d}:{s:04.1f}"
    return f"{int(m):02d}:{s:04.1f}"


DEFAULT_SCENE_THRESHOLD = 0.15


def describe_video_local(
    video: str | Path,
    *,
    model: str = DEFAULT_LOCAL_MODEL,
    detail: str = "standard",
    base_url: str = DEFAULT_BASE_URL,
    continuity: bool = True,
    scene_threshold: float = DEFAULT_SCENE_THRESHOLD,
    usage_out: dict | None = None,
) -> str:
    """Return the CDAF body markdown for `video`, using a local model.

    Same contract as generate.describe_video: the five required sections in order.
    """
    if detail not in _DETAIL_FRAMES:
        raise ValueError(f"detail must be one of {sorted(_DETAIL_FRAMES)}")
    _require_ffmpeg()
    video = Path(video)
    endpoint = _Endpoint(base_url, model)

    duration = _duration(video)
    shots = build_shots(duration, detect_cuts(video, scene_threshold))
    if detail == "brief":
        shots = coarsen(shots)

    per_shot = _DETAIL_FRAMES[detail]
    sentences = _DETAIL_SENTENCES[detail]
    described: list[dict] = []

    with tempfile.TemporaryDirectory() as td:
        for i, (start, end) in enumerate(shots, 1):
            frames = [f for f in (
                _frame(video, min(t, duration - 0.05), Path(td) / f"s{i:03d}_{j}.jpg")
                for j, t in enumerate(_sample_times(start, end, per_shot))) if f]
            if not frames:
                described.append({"start": start, "end": end, "description": "",
                                  "text": [], "failed": True})
                continue

            parsed = None
            for budget in (1200, 1800):
                try:
                    raw = endpoint.ask(
                        _SHOT_PROMPT.format(n=len(frames), sentences=sentences),
                        frames, max_tokens=budget)
                except GenerationError:
                    continue
                try:
                    parsed = _parse_json(raw)
                    break
                except ValueError:
                    parsed = _salvage(raw) or None
                    if parsed:
                        break

            if not parsed:
                described.append({"start": start, "end": end, "description": "",
                                  "text": [], "failed": True})
                continue

            desc = str(parsed.get("description") or "").strip()
            setting = str(parsed.get("setting") or "").strip()
            if setting and setting.lower() not in desc.lower():
                desc = f"{desc} Setting: {setting}."
            texts = [str(t).strip() for t in (parsed.get("visible_text") or [])
                     if str(t).strip()]
            described.append({"start": start, "end": end, "description": desc,
                              "text": texts, "failed": not desc})

    transcript = _transcribe(video, endpoint)
    narration = "\n".join(f"[{_ts(t)}] {x}" for t, x in transcript) or "(none)"
    screen = [(sh["start"], t) for sh in described for t in sh["text"]]
    screen_text = "\n".join(f"[{_ts(t)}] {x}" for t, x in screen) or "(none)"

    summary, tags = "", []
    if continuity:
        shots_in = "\n".join(
            f"{i + 1}. {s['description'] if not s['failed'] else '(NOT DESCRIBED)'}"
            for i, s in enumerate(described))
        try:
            parsed = _parse_json(endpoint.ask(_CONTINUITY_PROMPT.format(
                n=len(described), sentences=sentences, shots=shots_in,
                narration=narration, screen_text=screen_text), max_tokens=3000))
            segs = parsed.get("segments")
            if isinstance(segs, list) and len(segs) == len(described):
                for original, new in zip(described, segs):
                    if not original["failed"] and str(new).strip():
                        original["description"] = str(new).strip()
            summary = str(parsed.get("summary") or "").strip()
            tags = [str(t).strip() for t in (parsed.get("tags") or []) if str(t).strip()]
        except (GenerationError, ValueError) as e:
            print(f"cdaf: continuity pass skipped ({e})", file=sys.stderr)

    if not any(not s["failed"] for s in described):
        raise GenerationError(
            f"the local model described none of {video.name}'s {len(shots)} shots"
        )

    lines = ["## Summary", summary or "(unavailable)", "", "## Segments"]
    for sh in described:
        lines.append(f"[{_ts(sh['start'])}-{_ts(sh['end'])}] "
                     f"{sh['description'] or '(not described)'}")
    lines += ["", "## Transcript"]
    lines += [f"[{_ts(t)}] {x}" for t, x in transcript] or ["(no speech)"]
    lines += ["", "## On-screen Text"]
    lines += [f'[{_ts(t)}] "{x}"' for t, x in screen] or ["(none)"]
    lines += ["", "## Tags", ", ".join(tags) if tags else "(none)"]

    if usage_out is not None:
        usage_out.update({"shots": len(shots), "model_calls":
                          len(shots) + (1 if continuity else 0) + (1 if transcript else 0)})
    return "\n".join(lines)


def local_header_extras(continuity: bool, transcribed: bool,
                        threshold: float = DEFAULT_SCENE_THRESHOLD) -> dict[str, str]:
    """`x-` keys recording how this body was produced.

    SPEC.md reserves the `x-` prefix for producers. A consumer deciding whether to
    trust a timestamp benefits from knowing whether it was measured or inferred.
    """
    return {
        "x-shot-source": f"ffmpeg-scene-detect@{threshold:g}",
        "x-shot-isolation": "per-shot",
        "x-continuity-pass": "yes" if continuity else "no",
        "x-transcript-timing": "measured-rms" if transcribed else "none",
    }

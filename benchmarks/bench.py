"""CDAF benchmark: sidecar-mediated vs direct video understanding.

Fully reproducible: videos are synthesized with ffmpeg from scripted recipes, so
ground truth is known exactly and grading is objective (no LLM judge).

Usage (needs ffmpeg on PATH; `run` also needs GEMINI_API_KEY):
    python bench.py make                 # synthesize testset/ videos + ground truth
    python bench.py run                  # generate sidecars, ask questions both ways
    python bench.py report               # write RESULTS.md from results.json

Conditions per question:
  A. direct : [video file, question]        -> Gemini answers from pixels
  B. cdaf   : [sidecar body text, question] -> Gemini answers from the .cdaf alone
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cli"))

from cdaf import load, save, sidecar_path_for  # noqa: E402
from cdaf.generate import DEFAULT_MODEL, _client, generate_sidecar  # noqa: E402

HERE = Path(__file__).resolve().parent
TESTSET = HERE / "testset"
FONT = "C\\:/Windows/Fonts/arial.ttf" if sys.platform == "win32" else \
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

# ---------------------------------------------------------------- recipes ----
# Each video is a sequence of scenes: (background color, seconds, word or None).
# Everything asked in the questions is derivable from these tuples.

RECIPES: dict[str, list[tuple[str, int, str | None]]] = {
    "clip-a": [("red", 4, None), ("blue", 4, "OCEAN"), ("green", 4, None)],
    "clip-b": [("black", 3, "LAUNCH"), ("white", 3, None), ("purple", 3, "ORBIT"),
               ("orange", 3, None)],
    "clip-c": [("yellow", 5, None), ("cyan", 5, "SUMMER")],
    "clip-d": [("blue", 3, None), ("red", 3, "ALERT"), ("blue", 3, None),
               ("red", 3, "ALERT")],
}


def _questions(name: str, scenes: list[tuple[str, int, str | None]]) -> list[dict]:
    """Objective questions + answers derived from a recipe."""
    qs: list[dict] = []
    colors = [c for c, _, _ in scenes]
    words: list[tuple[str, float]] = []
    t = 0.0
    for color, dur, word in scenes:
        if word:
            words.append((word, t))
        t += dur

    qs.append({
        "q": "What is the background color of the FIRST scene? Answer with one word.",
        "type": "text", "answer": colors[0],
    })
    qs.append({
        "q": "What is the background color of the LAST scene? Answer with one word.",
        "type": "text", "answer": colors[-1],
    })
    qs.append({
        "q": "How many scene changes (hard cuts) are in the video? Answer with a number.",
        "type": "number", "answer": len(scenes) - 1, "tolerance": 0,
    })
    if words:
        word, start = words[0]
        qs.append({
            "q": "What is the first word shown on screen? Answer with one word.",
            "type": "text", "answer": word,
        })
        qs.append({
            "q": f"At what time in seconds does the word '{word}' FIRST appear? "
                 "Answer with a number.",
            "type": "number", "answer": start, "tolerance": 1.5,
        })
    else:
        qs.append({
            "q": "Does any text/word appear on screen at any point? Answer yes or no.",
            "type": "text", "answer": "no",
        })
    return qs


def cmd_make(_args) -> int:
    TESTSET.mkdir(parents=True, exist_ok=True)
    truth = {}
    for name, scenes in RECIPES.items():
        out = TESTSET / f"{name}.mp4"
        inputs, filters, labels = [], [], []
        for i, (color, dur, word) in enumerate(scenes):
            inputs += ["-f", "lavfi", "-i",
                       f"color=c={color}:duration={dur}:size=1280x720:rate=30"]
            if word:
                fc = "black" if color in ("white", "yellow", "cyan") else "white"
                filters.append(
                    f"[{i}:v]drawtext=fontfile='{FONT}':text='{word}':fontsize=96:"
                    f"fontcolor={fc}:x=(w-text_w)/2:y=(h-text_h)/2[v{i}]"
                )
            else:
                filters.append(f"[{i}:v]null[v{i}]")
            labels.append(f"[v{i}]")
        graph = ";".join(filters) + ";" + "".join(labels) + \
            f"concat=n={len(scenes)}:v=1:a=0[out]"
        cmd = ["ffmpeg", "-y", "-v", "error", *inputs,
               "-filter_complex", graph, "-map", "[out]",
               "-pix_fmt", "yuv420p", str(out)]
        subprocess.run(cmd, check=True)
        truth[name] = {
            "video": out.name,
            "scenes": [{"color": c, "seconds": d, "word": w} for c, d, w in scenes],
            "questions": _questions(name, scenes),
        }
        print(f"  made {out.name}  ({sum(d for _, d, _ in scenes)}s, "
              f"{len(truth[name]['questions'])} questions)")
    (TESTSET / "ground_truth.json").write_text(
        json.dumps(truth, indent=2), encoding="utf-8")
    print(f"\ntestset ready: {TESTSET}")
    return 0


# ------------------------------------------------------------------- run ----

ANSWER_SUFFIX = (
    "\n\nAnswer the question as briefly as possible — a single word or number when "
    "the question asks for one. No explanation."
)


def _ask(client, model: str, contents, retries: int = 5) -> tuple[str, dict]:
    """generate_content with 429 backoff; returns (text, usage dict)."""
    delay = 10.0
    for attempt in range(retries):
        try:
            t0 = time.monotonic()
            resp = client.models.generate_content(model=model, contents=contents)
            usage = getattr(resp, "usage_metadata", None)
            return (resp.text or "").strip(), {
                "prompt_tokens": getattr(usage, "prompt_token_count", None),
                "output_tokens": getattr(usage, "candidates_token_count", None),
                "seconds": round(time.monotonic() - t0, 2),
            }
        except Exception as e:  # noqa: BLE001 — SDK raises many transient types
            if attempt == retries - 1:
                raise
            wait = delay if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e) else 5.0
            print(f"    transient error ({type(e).__name__}), retrying in {wait:.0f}s",
                  file=sys.stderr)
            time.sleep(wait)
            delay *= 1.7
    raise RuntimeError("unreachable")


def _upload(client, video: Path):
    f = client.files.upload(file=str(video))
    while f.state and f.state.name == "PROCESSING":
        time.sleep(2)
        f = client.files.get(name=f.name)
    if f.state and f.state.name == "FAILED":
        raise RuntimeError(f"upload failed for {video.name}")
    return f


def cmd_run(args) -> int:
    truth = json.loads((TESTSET / "ground_truth.json").read_text(encoding="utf-8"))
    client = _client(None)
    model = args.model
    results = {"model": model, "videos": {}}

    for name, spec in truth.items():
        video = TESTSET / spec["video"]
        print(f"\n=== {name} ===")

        # 1. sidecar generation (the one-time cost CDAF amortizes)
        gen_usage: dict = {}
        sc = generate_sidecar(video, model=model, usage_out=gen_usage)
        save(sc, sidecar_path_for(video))
        print(f"  sidecar generated: {gen_usage}")

        body = load(sidecar_path_for(video)).body
        uploaded = _upload(client, video)
        entry = {"generation": gen_usage, "questions": []}
        try:
            for qspec in spec["questions"]:
                q = qspec["q"] + ANSWER_SUFFIX
                direct_answer, direct_usage = _ask(client, model, [uploaded, q])
                time.sleep(args.pause)
                cdaf_answer, cdaf_usage = _ask(
                    client, model,
                    f"Here is a CDAF descriptive sidecar for a video:\n\n{body}\n\n"
                    f"Based only on this description: {q}",
                )
                time.sleep(args.pause)
                entry["questions"].append({
                    **qspec,
                    "direct": {"answer": direct_answer, **direct_usage},
                    "cdaf": {"answer": cdaf_answer, **cdaf_usage},
                })
                print(f"  Q: {qspec['q'][:60]:<60} "
                      f"direct={direct_answer[:20]!r} cdaf={cdaf_answer[:20]!r}")
        finally:
            try:
                client.files.delete(name=uploaded.name)
            except Exception:
                pass
        results["videos"][name] = entry

    out = HERE / "results.json"
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nwrote {out}")
    return 0


# ---------------------------------------------------------------- report ----

def _correct(qspec: dict, answer: str) -> bool:
    a = answer.strip().lower()
    if qspec["type"] == "number":
        nums = re.findall(r"-?\d+(?:\.\d+)?", a)
        if not nums:
            return False
        return abs(float(nums[0]) - float(qspec["answer"])) <= qspec.get("tolerance", 0)
    return str(qspec["answer"]).lower() in a


def cmd_report(args) -> int:
    results = json.loads((HERE / "results.json").read_text(encoding="utf-8"))
    rows, gen_tokens_total = [], 0
    tally = {"direct": [0, 0, 0.0, 0.0], "cdaf": [0, 0, 0.0, 0.0]}  # correct, n, tok, sec
    for name, entry in results["videos"].items():
        gen_tokens_total += (entry["generation"].get("prompt_tokens") or 0) + \
                            (entry["generation"].get("output_tokens") or 0)
        for q in entry["questions"]:
            for cond in ("direct", "cdaf"):
                ok = _correct(q, q[cond]["answer"])
                tally[cond][0] += int(ok)
                tally[cond][1] += 1
                tally[cond][2] += q[cond].get("prompt_tokens") or 0
                tally[cond][3] += q[cond].get("seconds") or 0
                rows.append((name, q["q"], cond, q[cond]["answer"], ok,
                             q[cond].get("prompt_tokens")))

    n = tally["direct"][1]
    n_videos = len(results["videos"])
    lines = [
        "# CDAF Benchmark Results", "",
        f"Model: `{results['model']}` · Videos: {n_videos} · "
        f"Questions: {n} per condition", "",
        "| Condition | Accuracy | Mean prompt tokens / question | Mean latency (s) |",
        "|---|---|---|---|",
    ]
    for cond, label in (("direct", "Direct video"), ("cdaf", "CDAF sidecar")):
        c, total, tok, sec = tally[cond]
        lines.append(f"| {label} | {c}/{total} ({100*c/total:.0f}%) "
                     f"| {tok/total:,.0f} | {sec/total:.2f} |")

    direct_mean = tally["direct"][2] / n
    cdaf_mean = tally["cdaf"][2] / n
    ratio = direct_mean / max(cdaf_mean, 1)
    gen_per_video = gen_tokens_total / n_videos
    # Break-even: generating one sidecar costs `gen_per_video` tokens and saves
    # (direct_mean - cdaf_mean) tokens per subsequent question on that video.
    saving = direct_mean - cdaf_mean
    breakeven = gen_per_video / saving if saving > 0 else float("inf")
    lines += [
        "",
        f"- **Per-question prompt-token ratio (direct / cdaf): {ratio:.2f}x**",
        f"- Sidecar generation: {gen_tokens_total:,} tokens total, "
        f"{gen_per_video:,.0f} per video (prompt + output).",
        f"- Each question answered from the sidecar saves {saving:,.0f} prompt tokens, "
        f"so generation **breaks even after ~{breakeven:.2f} questions per video**.",
        "", "## Per-clip detail", "",
        "| Clip | Generation tokens | Direct tokens/q | CDAF tokens/q | D/C | "
        "Direct latency | CDAF latency |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, entry in results["videos"].items():
        qs = entry["questions"]
        gen = (entry["generation"].get("prompt_tokens") or 0) + \
              (entry["generation"].get("output_tokens") or 0)
        dm = sum(q["direct"].get("prompt_tokens") or 0 for q in qs) / len(qs)
        cm = sum(q["cdaf"].get("prompt_tokens") or 0 for q in qs) / len(qs)
        dl = sum(q["direct"].get("seconds") or 0 for q in qs) / len(qs)
        cl = sum(q["cdaf"].get("seconds") or 0 for q in qs) / len(qs)
        lines.append(f"| {name} | {gen:,} | {dm:,.1f} | {cm:,.1f} | "
                     f"{dm/max(cm,1):.2f}x | {dl:.2f} s | {cl:.2f} s |")
    lines += [
        "", "## Per-question detail", "",
        "| Video | Question | Condition | Answer | Correct | Prompt tokens |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append("| " + " | ".join(
            [r[0], r[1][:60], r[2], str(r[3])[:40].replace("|", "/"),
             "yes" if r[4] else "NO", str(r[5])]) + " |")
    out = HERE / "RESULTS.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:14]))
    print(f"\nwrote {out}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("make").set_defaults(func=cmd_make)
    r = sub.add_parser("run")
    r.add_argument("--model", default=DEFAULT_MODEL)
    r.add_argument("--pause", type=float, default=5.0,
                   help="seconds between API calls (free-tier rate limits)")
    r.set_defaults(func=cmd_run)
    rep = sub.add_parser("report")
    rep.set_defaults(func=cmd_report)
    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

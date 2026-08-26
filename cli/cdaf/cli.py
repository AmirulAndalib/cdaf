"""cdaf — command-line tool for CDAF sidecars.

Commands:
  generate   Create/refresh .cdaf sidecars for video files (needs Gemini key)
  validate   Verify a sidecar is well-formed and fresh (hash matches video)
  read       Print a sidecar's body (what an agent should consume)
  status     Report fresh/stale/missing across a directory tree
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .sidecar import (
    VIDEO_EXTENSIONS,
    SidecarError,
    check_freshness,
    load,
    save,
    sidecar_path_for,
    video_path_for,
)


def _iter_videos(paths: list[str]) -> list[Path]:
    videos: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            videos.extend(
                f for f in sorted(p.rglob("*"))
                if f.is_file() and f.suffix.lower() in VIDEO_EXTENSIONS
            )
        elif p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS:
            videos.append(p)
        elif p.is_file() and p.suffix.lower() == ".cdaf":
            video = video_path_for(p)
            if video:
                videos.append(video)
            else:
                print(f"warning: no video found for sidecar {p}", file=sys.stderr)
        else:
            print(f"warning: skipping {p} (not a video file or directory)", file=sys.stderr)
    return videos


def _sidecar_state(video: Path) -> str:
    """'fresh' | 'stale' | 'missing' | 'invalid' for a video's sidecar."""
    sidecar = sidecar_path_for(video)
    if not sidecar.is_file():
        return "missing"
    try:
        sc = load(sidecar)
    except SidecarError:
        return "invalid"
    return check_freshness(video, sc)


def cmd_generate(args: argparse.Namespace) -> int:
    from .generate import GenerationError, generate_sidecar  # lazy: needs google-genai

    videos = _iter_videos(args.paths)
    if not videos:
        print("no video files found", file=sys.stderr)
        return 1

    failures = 0
    for video in videos:
        sidecar = sidecar_path_for(video)
        if not args.force and _sidecar_state(video) == "fresh":
            print(f"  fresh   {video}  (skipped; use --force to regenerate)")
            continue
        print(f"  generating  {video} ...", flush=True)
        try:
            sc = generate_sidecar(
                video, model=args.model, detail=args.detail, api_key=args.api_key
            )
            save(sc, sidecar)
            print(f"  wrote   {sidecar}")
        except (GenerationError, OSError) as e:
            failures += 1
            print(f"  FAILED  {video}: {e}", file=sys.stderr)
    return 1 if failures else 0


def cmd_validate(args: argparse.Namespace) -> int:
    path = Path(args.path)
    sidecar_file = path if path.suffix.lower() == ".cdaf" else sidecar_path_for(path)
    if not sidecar_file.is_file():
        print(f"MISSING  no sidecar at {sidecar_file}")
        return 2
    try:
        sc = load(sidecar_file)
    except SidecarError as e:
        print(f"INVALID  {sidecar_file}: {e}")
        return 3
    video = video_path_for(sidecar_file, sc.video)
    if not video:
        print(f"ORPHAN   {sidecar_file}: paired video '{sc.video}' not found")
        return 4
    state = check_freshness(video, sc, fast=args.fast)
    print(f"{state.upper():<8} {sidecar_file}  <->  {video.name}")
    return 0 if state == "fresh" else 5


def cmd_read(args: argparse.Namespace) -> int:
    path = Path(args.path)
    sidecar_file = path if path.suffix.lower() == ".cdaf" else sidecar_path_for(path)
    if not sidecar_file.is_file():
        print(f"error: no sidecar at {sidecar_file}", file=sys.stderr)
        return 2
    try:
        sc = load(sidecar_file)
    except SidecarError as e:
        print(f"error: invalid sidecar: {e}", file=sys.stderr)
        return 3
    if not args.no_verify:
        video = video_path_for(sidecar_file, sc.video)
        if video and check_freshness(video, sc) == "stale":
            print(
                f"error: sidecar is STALE — {video.name} changed since it was written. "
                "Regenerate with: cdaf generate --force",
                file=sys.stderr,
            )
            return 5
    print(sc.body)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    videos = _iter_videos([args.path])
    if not videos:
        print("no video files found", file=sys.stderr)
        return 1
    counts = {"fresh": 0, "stale": 0, "missing": 0, "invalid": 0}
    for video in videos:
        state = _sidecar_state(video)
        counts[state] += 1
        print(f"  {state.upper():<8} {video}")
    total = len(videos)
    print(
        f"\n{total} video(s): {counts['fresh']} fresh, {counts['stale']} stale, "
        f"{counts['missing']} missing, {counts['invalid']} invalid"
    )
    return 0 if counts["fresh"] == total else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="cdaf",
        description="CDAF — Cached Descriptive Asset Files: timestamped descriptive "
        "sidecars for video, so AI agents stop re-analyzing the same footage.",
    )
    parser.add_argument("--version", action="version", version=f"cdaf {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    g = sub.add_parser("generate", help="create/refresh sidecars for videos (Gemini BYOK)")
    g.add_argument("paths", nargs="+", help="video files, sidecars, or directories (recursive)")
    g.add_argument("--model", default=None, help="Gemini model id (default: env CDAF_MODEL or gemini-2.5-flash)")
    g.add_argument("--detail", choices=["brief", "standard", "rich"], default="standard")
    g.add_argument("--force", action="store_true", help="regenerate even if sidecar is fresh")
    g.add_argument("--api-key", default=None, help="Gemini API key (default: env GEMINI_API_KEY)")
    g.set_defaults(func=cmd_generate)

    v = sub.add_parser("validate", help="check one sidecar is well-formed and fresh")
    v.add_argument("path", help="a video file or a .cdaf file")
    v.add_argument("--fast", action="store_true", help="size check only, skip hashing")
    v.set_defaults(func=cmd_validate)

    r = sub.add_parser("read", help="print a sidecar's body (verifies freshness first)")
    r.add_argument("path", help="a video file or a .cdaf file")
    r.add_argument("--no-verify", action="store_true", help="print without hashing the video")
    r.set_defaults(func=cmd_read)

    s = sub.add_parser("status", help="fresh/stale/missing report for a directory tree")
    s.add_argument("path", nargs="?", default=".", help="directory to scan (default: .)")
    s.set_defaults(func=cmd_status)

    args = parser.parse_args(argv)
    if getattr(args, "model", None) is None and args.command == "generate":
        from .generate import DEFAULT_MODEL
        args.model = DEFAULT_MODEL
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

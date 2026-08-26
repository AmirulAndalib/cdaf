"""Core library tests: parse/serialize round-trip, freshness, malformed rejection.

Run from the cli/ directory: python -m pytest
"""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from cdaf import Sidecar, check_freshness, hash_file, load, parse, save, sidecar_path_for
from cdaf.sidecar import SidecarError, dumps, segment_lines

BODY = """## Summary
A synthetic test clip.

## Segments
[00:00.0-00:01.0] First half of the test pattern.
[00:01.0-00:02.0] Second half; no scene change.

## Transcript
(no speech)

## On-screen Text
(none)

## Tags
test pattern, synthetic
"""


@pytest.fixture
def pair(tmp_path: Path) -> tuple[Path, Path]:
    """A fake 'video' (arbitrary bytes) and its fresh sidecar."""
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"\x00\x01\x02fake-video-bytes" * 100)
    sc = Sidecar(header={
        "video": video.name,
        "sha256": hash_file(video),
        "bytes": str(video.stat().st_size),
        "generator": "test",
        "created": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }, body=BODY)
    sidecar = sidecar_path_for(video)
    save(sc, sidecar)
    return video, sidecar


def test_round_trip(pair):
    _, sidecar = pair
    sc = load(sidecar)
    assert parse(dumps(sc)).header == sc.header
    assert parse(dumps(sc)).body.strip() == sc.body.strip()
    assert len(segment_lines(sc)) == 2


def test_fresh(pair):
    video, sidecar = pair
    sc = load(sidecar)
    assert check_freshness(video, sc) == "fresh"
    assert check_freshness(video, sc, fast=True) == "fresh"


def test_stale_on_size_change(pair):
    video, sidecar = pair
    video.write_bytes(video.read_bytes() + b"more")
    sc = load(sidecar)
    assert check_freshness(video, sc, fast=True) == "stale"
    assert check_freshness(video, sc) == "stale"


def test_same_size_corruption_needs_full_hash(pair):
    video, sidecar = pair
    data = bytearray(video.read_bytes())
    data[10] ^= 0xFF
    video.write_bytes(bytes(data))
    sc = load(sidecar)
    assert check_freshness(video, sc, fast=True) == "fresh"  # fast check is fooled
    assert check_freshness(video, sc) == "stale"             # hash is not


def test_crlf_tolerated(pair):
    _, sidecar = pair
    crlf = sidecar.read_text(encoding="utf-8").replace("\n", "\r\n")
    assert parse(crlf).video == "clip.mp4"


@pytest.mark.parametrize("mutate, why", [
    (lambda t: "not a cdaf file", "bad first line"),
    (lambda t: t.replace("CDAF/1.0", "CDAF/2.0"), "unsupported major version"),
    (lambda t: t.replace("sha256: ", "sha256: zz"), "invalid sha256"),
    (lambda t: t.replace("## Segments", "## Nope"), "missing Segments section"),
    (lambda t: "\n".join(l for l in t.split("\n") if not l.startswith("generator")),
     "missing required key"),
])
def test_malformed_rejected(pair, mutate, why):
    _, sidecar = pair
    text = sidecar.read_text(encoding="utf-8")
    with pytest.raises(SidecarError):
        parse(mutate(text))

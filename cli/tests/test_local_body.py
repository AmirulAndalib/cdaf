"""The local provider's assembled body must satisfy the CDAF spec.

The unit tests in test_local.py cover the pieces; these drive describe_video_local
end to end with ffmpeg and the model mocked, and assert the result parses as a
valid sidecar. Offline: no ffmpeg, no endpoint, no network.
"""

import json
from pathlib import Path
from unittest import mock

import pytest

from cdaf import Sidecar, local, parse
from cdaf.sidecar import dumps, segment_lines

SHOT = json.dumps({"description": "A person walks across a bright kitchen.",
                   "visible_text": ["OPEN 9-5"], "setting": "kitchen"})
CONTINUITY = json.dumps({"segments": ["A person walks across a bright kitchen."] * 8,
                         "summary": "A short kitchen clip.",
                         "tags": ["kitchen", "person"]})


def run(*, duration, cuts, speech, asr="", detail="standard"):
    """describe_video_local with the container and the model stubbed out."""
    def fake_frame(_video, _t, dest):
        dest.write_bytes(b"jpeg")
        return dest

    def fake_ask(prompt, images=None, max_tokens=0):
        return CONTINUITY if "Rewrite" in prompt else SHOT

    def fake_run(cmd, timeout=None):
        # The audio extraction must actually produce a file, or _transcribe bails
        # before the reply ever reaches the guard.
        for arg in cmd:
            if isinstance(arg, str) and arg.endswith(".wav"):
                Path(arg).write_bytes(b"RIFF")
        return ""

    with mock.patch.object(local, "_require_ffmpeg"), \
         mock.patch.object(local, "_duration", return_value=duration), \
         mock.patch.object(local, "detect_cuts", return_value=cuts), \
         mock.patch.object(local, "_frame", side_effect=fake_frame), \
         mock.patch.object(local, "speech_spans", return_value=speech), \
         mock.patch.object(local, "_run", side_effect=fake_run), \
         mock.patch.object(local._Endpoint, "ask", side_effect=fake_ask), \
         mock.patch.object(local._Endpoint, "ask_audio", return_value=asr):
        return local.describe_video_local(Path("clip.mp4"), detail=detail)


def as_sidecar(body: str) -> Sidecar:
    """Wrap a body in a valid header and round-trip it through the spec parser."""
    sc = Sidecar(header={"video": "clip.mp4", "sha256": "a" * 64, "bytes": "1",
                         "generator": "local", "created": "2026-08-27T00:00:00Z"},
                 body=body)
    return parse(dumps(sc))


@pytest.mark.parametrize("duration, cuts, want_segments", [
    (12.0, [3.0, 6.0, 9.0], 4),
    (6.0, [], 1),
    (3700.0, [1800.0], 2),          # over an hour -> HH:MM:SS.d timestamps
])
def test_body_parses_as_a_valid_sidecar(duration, cuts, want_segments):
    body = run(duration=duration, cuts=cuts, speech=[], asr="")
    parsed = as_sidecar(body)
    assert len(segment_lines(parsed)) == want_segments
    for section in ("## Summary", "## Segments", "## Transcript",
                    "## On-screen Text", "## Tags"):
        assert section in parsed.body


def test_measured_silence_never_reaches_the_model():
    """No measured speech -> the ASR call is skipped, not merely filtered.

    A model handed a silent track answers conversationally rather than declining.
    """
    body = run(duration=12.0, cuts=[3.0], speech=[],
               asr="I am a large language model, trained by Google.")
    assert "(no speech)" in body
    assert "large language model" not in body


def test_a_chatbot_reply_is_kept_out_of_the_transcript():
    body = run(duration=6.0, cuts=[], speech=[(1.0, 2.0)],
               asr="Sorry, the audio appears to be silent.")
    assert "(no speech)" in body
    assert "Sorry" not in body


def test_real_narration_survives_the_guard():
    """Regression: the guard once discarded any reply containing 'sorry'."""
    body = run(duration=6.0, cuts=[], speech=[(1.0, 2.0)],
               asr="Sorry I'm late, traffic was awful.")
    assert "traffic was awful" in body
    assert "(no speech)" not in body


def test_brief_detail_coarsens_shots():
    body = run(duration=30.0, cuts=[3.0, 6.0, 9.0, 12.0, 15.0], speech=[],
               detail="brief")
    assert len(segment_lines(as_sidecar(body))) < 6

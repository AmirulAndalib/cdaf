# cdaf (CLI)

Generate and validate CDAF sidecars — timestamped descriptive text files that let AI
agents reuse one video-understanding pass instead of re-analyzing footage.

Not on PyPI yet, so install from the repository:

```bash
pip install "cdaf[generate] @ git+https://github.com/UditAkhourii/cdaf.git#subdirectory=cli"
```

Drop the `[generate]` extra for `validate` / `read` / `status` only -- those need no
dependencies and no key.

Two generation providers:

```bash
export GEMINI_API_KEY=your-key
cdaf generate ./footage                  # Gemini Files API; handles directories

cdaf generate ./clip.mp4 --local         # a local OpenAI-compatible endpoint
```

`--local` needs `ffmpeg` and a served multimodal model instead of a key, and never
sends the footage anywhere. Point it with `--base-url` / `--model` (or `CDAF_BASE_URL`
/ `CDAF_LOCAL_MODEL`); set `CDAF_PROVIDER=local` to make it the default. One clip at a
time, and slower per clip -- but free, private, and priced per shot rather than per
second of footage. See `cdaf/local.py` for what it does differently.

Commands: `generate`, `validate`, `read`, `status`. Full docs and the format
specification live in the repository root: see `README.md` and `SPEC.md`.

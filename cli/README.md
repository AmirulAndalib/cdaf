# cdaf (CLI)

Generate and validate CDAF sidecars — timestamped descriptive text files that let AI
agents reuse one video-understanding pass instead of re-analyzing footage.

```bash
pip install cdaf[generate]      # or plain `pip install cdaf` for validate/read/status only
export GEMINI_API_KEY=your-key
cdaf generate ./footage
```

Commands: `generate`, `validate`, `read`, `status`. Full docs and the format
specification live in the repository root: see `README.md` and `SPEC.md`.

# CDAF Benchmark Results

Model: `gemini-2.5-flash` · Videos: 4 · Questions: 20 per condition

| Condition | Accuracy | Mean prompt tokens / question | Mean latency (s) |
|---|---|---|---|
| Direct video | 19/20 (95%) | 3,066 | 3.46 |
| CDAF sidecar | 20/20 (100%) | 303 | 2.24 |

- **Per-question prompt-token ratio (direct / cdaf): 10.12x**
- Sidecar generation: 14,405 tokens total, 3,601 per video (prompt + output).
- Each question answered from the sidecar saves 2,763 prompt tokens, so generation **breaks even after ~1.30 questions per video**.

## Per-clip detail

| Clip | Generation tokens | Direct tokens/q | CDAF tokens/q | D/C | Direct latency | CDAF latency |
|---|---|---|---|---|---|---|
| clip-a | 3,716 | 3,197.4 | 286.4 | 11.16x | 3.21 s | 2.63 s |
| clip-b | 3,790 | 3,197.2 | 360.2 | 8.88x | 3.18 s | 1.95 s |
| clip-c | 3,137 | 2,671.4 | 233.4 | 11.45x | 3.17 s | 2.24 s |
| clip-d | 3,762 | 3,197.2 | 332.2 | 9.62x | 4.27 s | 2.13 s |

## Per-question detail

| Video | Question | Condition | Answer | Correct | Prompt tokens |
|---|---|---|---|---|---|
| clip-a | What is the background color of the FIRST scene? Answer with | direct | Red | yes | 3196 |
| clip-a | What is the background color of the FIRST scene? Answer with | cdaf | red | yes | 285 |
| clip-a | What is the background color of the LAST scene? Answer with  | direct | Green | yes | 3196 |
| clip-a | What is the background color of the LAST scene? Answer with  | cdaf | Green | yes | 285 |
| clip-a | How many scene changes (hard cuts) are in the video? Answer  | direct | 2 | yes | 3199 |
| clip-a | How many scene changes (hard cuts) are in the video? Answer  | cdaf | 2 | yes | 288 |
| clip-a | What is the first word shown on screen? Answer with one word | direct | OCEAN | yes | 3195 |
| clip-a | What is the first word shown on screen? Answer with one word | cdaf | OCEAN | yes | 284 |
| clip-a | At what time in seconds does the word 'OCEAN' FIRST appear?  | direct | 4 | yes | 3201 |
| clip-a | At what time in seconds does the word 'OCEAN' FIRST appear?  | cdaf | 4 | yes | 290 |
| clip-b | What is the background color of the FIRST scene? Answer with | direct | Black | yes | 3196 |
| clip-b | What is the background color of the FIRST scene? Answer with | cdaf | Black | yes | 359 |
| clip-b | What is the background color of the LAST scene? Answer with  | direct | Orange | yes | 3196 |
| clip-b | What is the background color of the LAST scene? Answer with  | cdaf | Orange | yes | 359 |
| clip-b | How many scene changes (hard cuts) are in the video? Answer  | direct | 3 | yes | 3199 |
| clip-b | How many scene changes (hard cuts) are in the video? Answer  | cdaf | 3 | yes | 362 |
| clip-b | What is the first word shown on screen? Answer with one word | direct | LAUNCH | yes | 3195 |
| clip-b | What is the first word shown on screen? Answer with one word | cdaf | LAUNCH | yes | 358 |
| clip-b | At what time in seconds does the word 'LAUNCH' FIRST appear? | direct | 0 | yes | 3200 |
| clip-b | At what time in seconds does the word 'LAUNCH' FIRST appear? | cdaf | 0.0 | yes | 363 |
| clip-c | What is the background color of the FIRST scene? Answer with | direct | Yellow | yes | 2670 |
| clip-c | What is the background color of the FIRST scene? Answer with | cdaf | Yellow | yes | 232 |
| clip-c | What is the background color of the LAST scene? Answer with  | direct | Cyan | yes | 2670 |
| clip-c | What is the background color of the LAST scene? Answer with  | cdaf | cyan | yes | 232 |
| clip-c | How many scene changes (hard cuts) are in the video? Answer  | direct | 1 | yes | 2673 |
| clip-c | How many scene changes (hard cuts) are in the video? Answer  | cdaf | 1 | yes | 235 |
| clip-c | What is the first word shown on screen? Answer with one word | direct | SUMMER | yes | 2669 |
| clip-c | What is the first word shown on screen? Answer with one word | cdaf | SUMMER | yes | 231 |
| clip-c | At what time in seconds does the word 'SUMMER' FIRST appear? | direct | 5 | yes | 2675 |
| clip-c | At what time in seconds does the word 'SUMMER' FIRST appear? | cdaf | 5.1 | yes | 237 |
| clip-d | What is the background color of the FIRST scene? Answer with | direct | Blue | yes | 3196 |
| clip-d | What is the background color of the FIRST scene? Answer with | cdaf | blue | yes | 331 |
| clip-d | What is the background color of the LAST scene? Answer with  | direct | Red | yes | 3196 |
| clip-d | What is the background color of the LAST scene? Answer with  | cdaf | Red | yes | 331 |
| clip-d | How many scene changes (hard cuts) are in the video? Answer  | direct | 4 | NO | 3199 |
| clip-d | How many scene changes (hard cuts) are in the video? Answer  | cdaf | 3 | yes | 334 |
| clip-d | What is the first word shown on screen? Answer with one word | direct | ALERT | yes | 3195 |
| clip-d | What is the first word shown on screen? Answer with one word | cdaf | ALERT | yes | 330 |
| clip-d | At what time in seconds does the word 'ALERT' FIRST appear?  | direct | 3 | yes | 3200 |
| clip-d | At what time in seconds does the word 'ALERT' FIRST appear?  | cdaf | 2.9 | yes | 335 |

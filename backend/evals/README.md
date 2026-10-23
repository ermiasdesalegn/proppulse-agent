# Evaluations

Three questions, answered with numbers instead of impressions:

1. **Does the agent find the right homes?** `retrieval`: about 50 buyer messages in English, Amharic and mixed, each labelled with the
   units a sensible broker would show. The agent's real front half runs (language model, embedding, hybrid search, the widening loop).
   Reported: hit@3, recall@3, precision@3, MRR, how often widening rescues a too-tight request, whether requests nothing matches come
   back empty, and a sweep of the similarity threshold that justifies the value used.
2. **Does it behave across a whole conversation?** `scenarios`: scripted conversations through the real graph, with a rolled-back
   database and mock calendar, email and Telegram. Each checks the graph path, the final state, the side effects (calendar events,
   emails, ledger rows, Telegram sends) and that nothing was sent twice.
3. **Does it ever invent a number?** Every reply in every scenario is checked: each price, size, percentage or year must appear in the
   unit facts or in the buyer's own words (`app/groundedness.py`).

## Running

```bash
cd backend
.venv/Scripts/python -m evals                 # everything, from the recording: free and instant, writes evals/REPORT.md
.venv/Scripts/python -m evals --check         # the same, and exit 1 if anything is worse than evals/thresholds.json (CI)
.venv/Scripts/python -m evals retrieval       # one suite
.venv/Scripts/python -m evals scenarios --only booking
.venv/Scripts/python -m evals --mode record   # ask the live model for anything not yet recorded (uses Gemini quota; safe to repeat)
```

## How replay works

`evals/recordings/gemini.jsonl` holds every request the evaluations make to the model and the answer it gave, keyed by exactly what was
asked. In replay mode the model is never called, so a run is free, instant and repeatable, and it measures everything around the model.
A request with no recording fails loudly instead of silently reaching the network: if a prompt or a scenario changes, run
`--mode record` once to add the new answers. Record mode only pays for what is missing, so a run stopped by the free tier's daily limit
simply continues the next day.

The clock is fixed (Monday 2 November 2026, 08:00) because prompts contain the date.

## What a failing scenario means

- A scenario marked **known gap** fails today for a stated reason and a stated phase that fixes it. It is reported, not hidden, and does
  not fail the check. When it starts passing the report says so.
- Any other failure is a regression. `--check` fails, and so does `tests/test_evals_replay.py` in the normal test run.

## Honest limits

- The voice scenario stubs the transcription (no real audio is available offline); real voice notes were verified by hand.
- Labels are one person's judgement of what a good answer is, on a 15-unit catalogue. Hit@3 on a catalogue this small is generous; the
  point is to catch regressions and compare changes, and the threshold sweep and the empty-request cases are what test precision.
- The groundedness check covers numbers, not wording: it cannot tell that a sentence is misleading, only that a figure is unsupported.

# Shortcuts. On Windows without make, run the commands directly (they are the same).
PY = backend/.venv/Scripts/python

.PHONY: test eval eval-check eval-record

test:           ## the whole offline test suite (embedded Postgres, no network)
	cd backend && ../$(PY) -m pytest -q

eval:           ## evaluations from the recording; writes backend/evals/REPORT.md
	cd backend && ../$(PY) -m evals

eval-check:     ## the same, and fail if anything is worse than backend/evals/thresholds.json
	cd backend && ../$(PY) -m evals --check

eval-record:    ## ask the live model for anything not yet recorded (uses Gemini quota; safe to repeat)
	cd backend && ../$(PY) -m evals --mode record

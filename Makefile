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

.PHONY: dev-backend dev-frontend

dev-backend:    ## local backend on :8123 (embedded Postgres, everything mocked; add ARGS=--live-model or ARGS=--fresh)
	cd backend && ../$(PY) -m scripts.dev_local $(ARGS)

dev-frontend:   ## the website on :3100 against the local backend (console password: dev-admin-key)
	cd frontend && BACKEND_URL=http://127.0.0.1:8123 ADMIN_API_KEY=dev-admin-key DASHBOARD_PASSWORD=dev-pass SESSION_SECRET=dev-session-secret-0123456789abcdef0123456789 NEXT_PUBLIC_SUPABASE_URL= NEXT_PUBLIC_SUPABASE_ANON_KEY= npm run dev -- -p 3100

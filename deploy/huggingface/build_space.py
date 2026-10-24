"""Assemble a clean folder to upload to a Hugging Face Space.  Usage:  python deploy/huggingface/build_space.py
Writes dist/hf-space/ with the Dockerfile, the Space README and only the backend files the image needs.
Never copies .env, .venv, service-account files, tests, recordings or logs."""
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND, OUT = ROOT / "backend", ROOT / "dist" / "hf-space"

if OUT.exists():
    shutil.rmtree(OUT)
OUT.mkdir(parents=True)
junk = shutil.ignore_patterns("__pycache__", "*.pyc", "*.log", ".env*", "service-account*")
for folder in ("app", "migrations", "scripts"):
    shutil.copytree(BACKEND / folder, OUT / folder, ignore=junk)
shutil.copy(BACKEND / "requirements.txt", OUT)
(OUT / "evals").mkdir()
shutil.copy(BACKEND / "evals" / "summary.json", OUT / "evals")
(OUT / "tests" / "fixtures").mkdir(parents=True)
shutil.copy(BACKEND / "tests" / "fixtures" / "seed_embeddings.json", OUT / "tests" / "fixtures")
shutil.copy(Path(__file__).parent / "Dockerfile", OUT)
shutil.copy(Path(__file__).parent / "README.md", OUT)

files = [p for p in OUT.rglob("*") if p.is_file()]
bad = [p for p in files if p.name.startswith(".env") or "service-account" in p.name or ".venv" in p.parts]
assert not bad, f"secrets copied: {bad}"
print(f"{OUT}: {len(files)} files, {sum(p.stat().st_size for p in files) / 1e6:.1f} MB")

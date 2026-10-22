from fastapi import FastAPI

from app.config import get_settings

app = FastAPI(title="PropPulse AI")


@app.get("/health")
def health() -> dict:
    s = get_settings()
    return {
        "status": "ok",
        "gemini_model": s.gemini_model,
        "gemini_key_configured": bool(s.gemini_api_key),
        "database_configured": bool(s.database_url),
    }

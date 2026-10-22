"""Phase 0 gate: confirm the configured model ID and embedding dimension work."""
import sys

from google import genai
from google.genai import types

from app.config import get_settings

EXPECTED_DIM = 768


def main() -> int:
    s = get_settings()
    if not s.gemini_api_key:
        print("GEMINI_API_KEY is empty in .env")
        return 2

    client = genai.Client(api_key=s.gemini_api_key)

    print(f"text model: {s.gemini_model}")
    try:
        r = client.models.generate_content(model=s.gemini_model, contents="Reply with the single word: ready")
        print("  ok ->", (r.text or "").strip()[:40])
    except Exception as e:
        print("  FAILED:", type(e).__name__, str(e)[:300])
        return 1

    print(f"embedding model: {s.gemini_embed_model}")
    try:
        e = client.models.embed_content(
            model=s.gemini_embed_model,
            contents="3 bedroom apartment in Bole with a balcony",
            config=types.EmbedContentConfig(output_dimensionality=EXPECTED_DIM),
        )
        dim = len(e.embeddings[0].values)
        print(f"  ok -> {dim} dimensions")
        if dim != EXPECTED_DIM:
            print(f"  MISMATCH: schema expects {EXPECTED_DIM}")
            return 1
    except Exception as e:
        print("  FAILED:", type(e).__name__, str(e)[:300])
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())

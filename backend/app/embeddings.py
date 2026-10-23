from functools import lru_cache

from google import genai
from google.genai import types

from app.config import get_settings

DIM = 768


@lru_cache
def _client() -> genai.Client:
    # One long-lived client: the SDK closes its HTTP session when a client object is garbage collected.
    return genai.Client(api_key=(get_settings().gemini_key_list or [""])[0])


def _embed(texts: list[str], task_type: str) -> list[list[float]]:
    s = get_settings()
    res = _client().models.embed_content(
        model=s.gemini_embed_model,
        contents=texts,
        config=types.EmbedContentConfig(task_type=task_type, output_dimensionality=DIM),
    )
    return [e.values for e in res.embeddings]


def embed_documents(texts: list[str]) -> list[list[float]]:
    return _embed(texts, "RETRIEVAL_DOCUMENT")


def embed_query(text: str) -> list[float]:
    return _embed([text], "RETRIEVAL_QUERY")[0]

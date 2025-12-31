# openrouter_client.py
import os
from openai import OpenAI

def make_openrouter_client() -> OpenAI:
    return OpenAI(
        base_url=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
        api_key=os.getenv("OPENROUTER_API_KEY"),
    )

def chat_json(client: OpenAI, system: str, user: str) -> dict:
    model = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
    extra_headers = {
        "HTTP-Referer": os.getenv("OPENROUTER_SITE_URL", "https://ontorag.github.io"),
        "X-Title": os.getenv("OPENROUTER_APP_NAME", "OntoRAG"),
    }

    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role":"system","content":system},
            {"role":"user","content":user},
        ],
        response_format={"type":"json_object"},
        extra_headers=extra_headers,
        temperature=0.2,
    )
    return resp.choices[0].message.content  # JSON string (poi json.loads)

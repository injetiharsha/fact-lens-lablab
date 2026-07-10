"""Optional LLM provider clients for the real agent workflow.

The workflow must still run without keys, so every function returns an empty
dict/string on provider failure and the orchestrator falls back to deterministic
logic. When keys are present, agents use these clients for actual reasoning.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import re
import urllib.request
from typing import Any, Dict, List

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

try:  # LangChain provider integrations
    from langchain_google_genai import ChatGoogleGenerativeAI  # type: ignore
except Exception:  # pragma: no cover
    ChatGoogleGenerativeAI = None  # type: ignore

try:  # LangChain provider integrations
    from langchain_openai import ChatOpenAI  # type: ignore
except Exception:  # pragma: no cover
    ChatOpenAI = None  # type: ignore


def _prompt_value_to_text(value: Any) -> str:
    if hasattr(value, "to_string"):
        try:
            return str(value.to_string())
        except Exception:
            pass
    if hasattr(value, "to_messages"):
        try:
            messages = value.to_messages()
            return "\n".join(
                f"{getattr(msg, 'type', 'message')}: {getattr(msg, 'content', '')}"
                for msg in messages
            )
        except Exception:
            pass
    return str(value)


def _render_langchain_prompt(prompt: str, system_message: str) -> str:
    template = ChatPromptTemplate.from_messages(
        [
            ("system", system_message),
            ("human", "{prompt}"),
        ]
    )
    return _prompt_value_to_text(template.invoke({"prompt": prompt}))


def _invoke_langchain_json(model: Any, prompt: str, system_message: str, timeout_s: int = 20) -> Dict[str, Any]:
    template = ChatPromptTemplate.from_messages(
        [
            ("system", system_message),
            ("human", "{prompt}"),
        ]
    )
    chain = template | model | StrOutputParser()

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(chain.invoke, {"prompt": prompt})
        text = future.result(timeout=max(3, timeout_s))
    return _extract_json(str(text or ""))


def _build_gemini_model(model_env: str, default_model: str, api_key: str) -> Any:
    model_name = os.getenv(model_env, default_model)
    if ChatGoogleGenerativeAI is None:
        return None
    return ChatGoogleGenerativeAI(
        model=model_name,
        google_api_key=api_key,
        temperature=0.2,
        max_output_tokens=2048,
    )


def _build_featherless_model(model_env: str = "FEATHERLESS_MODEL", default_model: str = "meta-llama/Meta-Llama-3.1-8B-Instruct", api_key: str = "") -> Any:
    if ChatOpenAI is None:
        return None
    model_name = os.getenv(model_env, default_model)
    base_url = os.getenv("FEATHERLESS_API_BASE", "https://api.featherless.ai/v1").strip()
    if base_url.endswith("/chat/completions"):
        base_url = base_url[: -len("/chat/completions")]
    return ChatOpenAI(
        model=model_name,
        api_key=api_key,
        base_url=base_url,
        temperature=0.2,
        timeout=30,
    )


def generate_gemini_json(prompt: str, model_env: str, default_model: str) -> Dict[str, Any]:
    if os.getenv("FACTLENS_FAST_MODE", "1").strip().lower() in {"1", "true", "yes", "on"}:
        return {}
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        return {}
    timeout_s = int(os.getenv("GEMINI_CALL_TIMEOUT_SECONDS", "20"))
    rendered_prompt = _render_langchain_prompt(prompt, "Return only valid JSON. No markdown.")

    model = _build_gemini_model(model_env, default_model, api_key)
    if model is not None:
        try:
            return _invoke_langchain_json(model, rendered_prompt, "Return only valid JSON. No markdown.", timeout_s=timeout_s)
        except Exception:
            pass

    try:
        import google.generativeai as genai  # type: ignore
    except Exception:
        return {}

    try:
        genai.configure(api_key=api_key)
        legacy = genai.GenerativeModel(os.getenv(model_env, default_model))
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(legacy.generate_content, rendered_prompt)
            response = future.result(timeout=max(3, timeout_s))
        return _extract_json(getattr(response, "text", "") or "")
    except Exception:
        return {}


def generate_featherless_json(
    prompt: str,
    model_env: str = "FEATHERLESS_MODEL",
    default_model: str = "meta-llama/Meta-Llama-3.1-8B-Instruct",
) -> Dict[str, Any]:
    if os.getenv("FACTLENS_FAST_MODE", "1").strip().lower() in {"1", "true", "yes", "on"}:
        return {}
    api_key = os.getenv("FEATHERLESS_API_KEY", "").strip()
    if not api_key:
        return {}

    rendered_prompt = _render_langchain_prompt(prompt, "Return only valid JSON. No markdown.")

    model = _build_featherless_model(model_env=model_env, default_model=default_model, api_key=api_key)
    if model is not None:
        timeout_s = int(os.getenv("FEATHERLESS_CALL_TIMEOUT_SECONDS", "30"))
        try:
            return _invoke_langchain_json(model, rendered_prompt, "Return only valid JSON. No markdown.", timeout_s=timeout_s)
        except Exception:
            pass

    base_url = os.getenv("FEATHERLESS_API_BASE", "https://api.featherless.ai/v1/chat/completions").strip()
    model_name = os.getenv(model_env, default_model).strip()
    payload = json.dumps(
        {
            "model": model_name,
            "messages": [
                {
                    "role": "system",
                    "content": "Return only valid JSON. No markdown.",
                },
                {"role": "user", "content": rendered_prompt},
            ],
            "temperature": 0.2,
        }
    ).encode("utf-8")

    try:
        req = urllib.request.Request(
            base_url,
            data=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return {}

    try:
        content = data["choices"][0]["message"]["content"]
    except Exception:
        return {}
    return _extract_json(str(content))


def compact_sources_for_prompt(sources: List[Any], limit: int = 8) -> str:
    rows = []
    for idx, source in enumerate(sources[:limit], start=1):
        rows.append(
            f"{idx}. title={source.title!r}; url={source.url!r}; "
            f"credibility={source.credibility}; snippet={source.snippet!r}"
        )
    return "\n".join(rows)


def _extract_json(text: str) -> Dict[str, Any]:
    text = str(text or "").strip()
    if not text:
        return {}
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S).strip()
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        pass

    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return {}
    try:
        parsed = json.loads(match.group(0))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}

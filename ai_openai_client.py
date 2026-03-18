import base64
import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests

from ai_config import AISettings, is_domestic_api


@dataclass
class AIReply:
    text: str
    tokens: int = 0
    raw: Optional[Dict[str, Any]] = None


def _safe_int(x: Any, default: int = 0) -> int:
    try:
        return int(x)
    except Exception:
        return default


def _normalize_base_url(base_url: str) -> str:
    u = (base_url or "").strip()
    if not u:
        return "https://api.openai.com/v1"
    return u.rstrip("/")


def _is_reasoner_model(model: str) -> bool:
    m = (model or "").lower()
    return bool(re.search(r"reasoner|deepseek-r1|o1-|o3-", m))


def _smart_proxies(base_url: str) -> Optional[dict]:
    if is_domestic_api(base_url):
        return {"http": "", "https": ""}
    return None


def _build_messages(
    system_prompt: Optional[str],
    user_text: str,
    image_b64_png: Optional[str],
    history: Optional[List[Dict[str, str]]] = None,
    *,
    skip_system: bool = False,
) -> list:
    messages: list = []
    if system_prompt and not skip_system:
        messages.append({"role": "system", "content": system_prompt})
    elif system_prompt and skip_system:
        user_text = f"[人设提示] {system_prompt}\n\n{user_text}"
    if history:
        messages.extend(history)
    if image_b64_png:
        messages.append(
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_text or "请根据这张截图给出反馈。"},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64_png}"}},
                ],
            }
        )
    else:
        messages.append({"role": "user", "content": user_text})
    return messages


def chat_completion(
    settings: AISettings,
    user_text: str,
    *,
    image_png_bytes: Optional[bytes] = None,
    system_prompt: Optional[str] = None,
    history: Optional[List[Dict[str, str]]] = None,
    max_tokens: Optional[int] = None,
    timeout_s: int = 30,
) -> AIReply:
    base_url = _normalize_base_url(settings.base_url)
    api_key = (settings.api_key or "").strip()
    if not api_key:
        raise RuntimeError("API Key 为空，请先在设置面板填写 API Key。")

    image_b64 = None
    if image_png_bytes:
        image_b64 = base64.b64encode(image_png_bytes).decode("ascii")

    prompt = system_prompt if system_prompt is not None else (settings.system_prompt or None)
    reasoner = _is_reasoner_model(settings.model)
    url = f"{base_url}/chat/completions"
    payload: Dict[str, Any] = {
        "model": settings.model or "gpt-4o-mini",
        "messages": _build_messages(prompt, user_text, image_b64, history, skip_system=reasoner),
    }
    if not reasoner:
        payload["temperature"] = 0.7
    if max_tokens and max_tokens > 0:
        payload["max_tokens"] = max_tokens
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        resp = requests.post(url, headers=headers, data=json.dumps(payload),
                             timeout=timeout_s, proxies=_smart_proxies(base_url))
    except requests.exceptions.Timeout:
        raise RuntimeError("请求超时，请检查网络或稍后重试。")
    except requests.exceptions.ConnectionError:
        raise RuntimeError("无法连接到 API 服务器，请检查网络和 Base URL。")

    if resp.status_code == 401:
        raise RuntimeError("API Key 无效或已过期，请检查后重新填写。")
    if resp.status_code == 404:
        raise RuntimeError(f"模型 {settings.model} 不存在或不可用。")
    if resp.status_code == 429:
        raise RuntimeError("请求频率过高或余额不足，请稍后再试。")
    if resp.status_code >= 400:
        try:
            j = resp.json()
            msg = j.get("error", {}).get("message") or resp.text[:200]
        except Exception:
            msg = resp.text[:200]
        raise RuntimeError(f"API 请求失败 (HTTP {resp.status_code})：{msg}")

    data = resp.json()
    text = ""
    try:
        msg_obj = data["choices"][0]["message"]
        text = msg_obj.get("content") or ""
    except Exception:
        text = ""
    usage = data.get("usage") if isinstance(data, dict) else {}
    tokens = _safe_int((usage or {}).get("total_tokens", 0), 0)
    return AIReply(text=text or "", tokens=tokens, raw=data if isinstance(data, dict) else None)


def list_models(settings: AISettings, *, timeout_s: int = 15) -> List[str]:
    base_url = _normalize_base_url(settings.base_url)
    api_key = (settings.api_key or "").strip()
    if not api_key:
        raise RuntimeError("API Key 为空，请先填写。")
    url = f"{base_url}/models"
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        resp = requests.get(url, headers=headers, timeout=timeout_s,
                            proxies=_smart_proxies(base_url))
    except requests.exceptions.Timeout:
        raise RuntimeError("拉取模型列表超时。")
    except requests.exceptions.ConnectionError:
        raise RuntimeError("无法连接到 API 服务器。")
    if resp.status_code >= 400:
        try:
            j = resp.json()
            msg = j.get("error", {}).get("message") or resp.text[:200]
        except Exception:
            msg = resp.text[:200]
        raise RuntimeError(f"模型列表拉取失败 (HTTP {resp.status_code})：{msg}")
    data = resp.json()
    items = data.get("data", []) if isinstance(data, dict) else []
    out: List[str] = []
    if isinstance(items, list):
        for it in items:
            if isinstance(it, dict) and it.get("id"):
                out.append(str(it["id"]))
    out = sorted(set(out))
    return out

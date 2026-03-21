import base64
import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests

from ai_config import AISettings, is_domestic_api, is_anthropic_native


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


def _strip_think_tags(text: str) -> str:
    """去除思维链标签 <think>...</think>，不管模型是否是reasoner都执行。"""
    # 处理完整的 <think>...</think> 块
    text = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE)
    # 处理只有开头没有结尾的 <think>（模型输出被截断的情况）
    text = re.sub(r"<think>[\s\S]*$", "", text, flags=re.IGNORECASE)
    return text.strip()


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
    reply_min_length: int = 0,
    reply_max_length: int = 0,
) -> list:
    messages: list = []
    # 自动拼字数要求到 system prompt
    length_hint = ""
    if reply_min_length > 0 and reply_max_length > 0:
        length_hint = f"\n\n【回复长度要求】请将每次回复控制在 {reply_min_length}~{reply_max_length} 字之间。"
    elif reply_max_length > 0:
        length_hint = f"\n\n【回复长度要求】请将每次回复控制在 {reply_max_length} 字以内。"
    elif reply_min_length > 0:
        length_hint = f"\n\n【回复长度要求】请将每次回复至少回复 {reply_min_length} 字。"

    if system_prompt and not skip_system:
        messages.append({"role": "system", "content": system_prompt + length_hint})
    elif system_prompt and skip_system:
        user_text = f"[人设提示] {system_prompt}{length_hint}\n\n{user_text}"
    elif length_hint and not skip_system:
        messages.append({"role": "system", "content": length_hint.strip()})
    elif length_hint and skip_system:
        user_text = f"{length_hint.strip()}\n\n{user_text}"
    if history:
        messages.extend(history)
        if not skip_system and len(history) >= 2:
            last_assistant = ""
            for h in reversed(history):
                if h.get("role") == "assistant":
                    last_assistant = h.get("content", "")
                    break
            if last_assistant:
                user_text = user_text + "\n\n（请用不同的句式和角度回复，不要重复之前说过的内容）"
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
    if is_anthropic_native(settings.base_url):
        return _chat_completion_anthropic(
            settings, user_text,
            image_png_bytes=image_png_bytes,
            system_prompt=system_prompt,
            history=history,
            max_tokens=max_tokens,
            timeout_s=timeout_s,
        )
    base_url = _normalize_base_url(settings.base_url)
    api_key = (settings.api_key or "").strip()
    if not api_key:
        raise RuntimeError("API Key 为空，请先在设置面板填写 API Key。")

    image_b64 = None
    if image_png_bytes:
        image_b64 = base64.b64encode(image_png_bytes).decode("ascii")

    prompt = system_prompt if system_prompt is not None else (settings.system_prompt or None)
    reasoner = _is_reasoner_model(settings.model)
    reply_min = int(getattr(settings, "reply_min_length", 0) or 0)
    reply_max = int(getattr(settings, "reply_max_length", 0) or 0)
    url = f"{base_url}/chat/completions"
    payload: Dict[str, Any] = {
        "model": settings.model or "gpt-4o-mini",
        "messages": _build_messages(prompt, user_text, image_b64, history, skip_system=reasoner,
                                     reply_min_length=reply_min, reply_max_length=reply_max),
    }
    if not reasoner:
        payload["temperature"] = 0.7
    # max_tokens 按回复上限的3倍计算（中文约1.5token/字），未设置则不限制
    effective_max_tokens = max_tokens
    if not effective_max_tokens and reply_max > 0:
        effective_max_tokens = reply_max * 4
    if effective_max_tokens and effective_max_tokens > 0:
        payload["max_tokens"] = effective_max_tokens
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
        # 优先读 content，去除思维链标签后使用
        raw_content = msg_obj.get("content") or ""
        text = _strip_think_tags(raw_content)
        if not text:
            # content 为空或全是思维链：检查 reasoning_content
            # 但 reasoning_content 是思考过程，不应直接显示给用户
            # 此时说明模型没有生成正文，报错提示重试
            reasoning = msg_obj.get("reasoning_content") or ""
            if reasoning:
                raise RuntimeError("模型只返回了思考过程，未生成正文回复，请重试。")
            raise RuntimeError("模型返回了空内容，请检查模型配置或稍后重试。")
    except RuntimeError:
        raise
    except Exception:
        text = ""
    usage = data.get("usage") if isinstance(data, dict) else {}
    tokens = _safe_int((usage or {}).get("total_tokens", 0), 0)
    return AIReply(text=text or "", tokens=tokens, raw=data if isinstance(data, dict) else None)


# ── Anthropic native Messages API ──

_ANTHROPIC_MODELS = [
    "claude-sonnet-4-20250514",
    "claude-opus-4-20250514",
    "claude-3-7-sonnet-20250219",
    "claude-3-5-sonnet-20241022",
    "claude-3-5-haiku-20241022",
    "claude-3-opus-20240229",
    "claude-3-haiku-20240307",
]



def _merge_consecutive_roles(messages: list) -> list:
    """Anthropic requires strict user/assistant alternation.
    Merge consecutive same-role messages into one."""
    if not messages:
        return messages
    merged: list = [messages[0]]
    for msg in messages[1:]:
        if not isinstance(msg, dict):
            continue
        if msg.get("role") == merged[-1].get("role"):
            prev_content = merged[-1].get("content", "")
            cur_content = msg.get("content", "")
            if isinstance(prev_content, str) and isinstance(cur_content, str):
                merged[-1]["content"] = prev_content + "\n" + cur_content
            else:
                merged.append(msg)
        else:
            merged.append(msg)
    return merged


def _build_anthropic_messages(
    user_text: str,
    image_b64_png: Optional[str],
    history: Optional[List[Dict[str, str]]] = None,
) -> list:
    messages: list = []
    if history:
        messages.extend(history)
        if not skip_system and len(history) >= 2:
            last_assistant = ""
            for h in reversed(history):
                if h.get("role") == "assistant":
                    last_assistant = h.get("content", "")
                    break
            if last_assistant:
                user_text = user_text + "\n\n（请用不同的句式和角度回复，不要重复之前说过的内容）"
    if image_b64_png:
        messages.append({
            "role": "user",
            "content": [
                {"type": "text", "text": user_text or "请根据这张截图给出反馈。"},
                {"type": "image", "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": image_b64_png,
                }},
            ],
        })
    else:
        messages.append({"role": "user", "content": user_text})
    return messages


def _chat_completion_anthropic(
    settings: AISettings,
    user_text: str,
    *,
    image_png_bytes: Optional[bytes] = None,
    system_prompt: Optional[str] = None,
    history: Optional[List[Dict[str, str]]] = None,
    max_tokens: Optional[int] = None,
    timeout_s: int = 30,
) -> AIReply:
    base_url = (settings.base_url or "").strip().rstrip("/") or "https://api.anthropic.com"
    api_key = (settings.api_key or "").strip()
    if not api_key:
        raise RuntimeError("API Key 为空，请先在设置面板填写 API Key。")

    image_b64 = None
    if image_png_bytes:
        image_b64 = base64.b64encode(image_png_bytes).decode("ascii")

    prompt = system_prompt if system_prompt is not None else (settings.system_prompt or None)
    reply_min = int(getattr(settings, "reply_min_length", 0) or 0)
    reply_max = int(getattr(settings, "reply_max_length", 0) or 0)

    length_hint = ""
    if reply_min > 0 and reply_max > 0:
        length_hint = f"\n\n【回复长度要求】请将每次回复控制在 {reply_min}~{reply_max} 字之间。"
    elif reply_max > 0:
        length_hint = f"\n\n【回复长度要求】请将每次回复控制在 {reply_max} 字以内。"
    elif reply_min > 0:
        length_hint = f"\n\n【回复长度要求】请将每次回复至少回复 {reply_min} 字。"

    system_text = ((prompt or "") + length_hint).strip() or None

    effective_max_tokens = max_tokens
    if not effective_max_tokens and reply_max > 0:
        effective_max_tokens = reply_max * 4
    if not effective_max_tokens or effective_max_tokens <= 0:
        effective_max_tokens = 4096

    url = f"{base_url}/v1/messages"
    payload: Dict[str, Any] = {
        "model": settings.model or "claude-sonnet-4-20250514",
        "max_tokens": effective_max_tokens,
        "messages": _merge_consecutive_roles(_build_anthropic_messages(user_text, image_b64, history)),
    }
    if system_text:
        payload["system"] = system_text

    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2024-10-22",
        "content-type": "application/json",
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
            err_obj = j.get("error", {})
            msg = err_obj.get("message") if isinstance(err_obj, dict) else resp.text[:200]
        except Exception:
            msg = resp.text[:200]
        raise RuntimeError(f"API 请求失败 (HTTP {resp.status_code})：{msg}")

    data = resp.json()
    text = ""
    try:
        content_blocks = data.get("content", [])
        parts = []
        for block in content_blocks:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        raw_text = "\n".join(parts)
        text = _strip_think_tags(raw_text)
        if not text:
            raise RuntimeError("模型返回了空内容，请检查模型配置或稍后重试。")
    except RuntimeError:
        raise
    except Exception:
        text = ""

    usage = data.get("usage", {})
    tokens = _safe_int(usage.get("input_tokens", 0), 0) + _safe_int(usage.get("output_tokens", 0), 0)
    return AIReply(text=text or "", tokens=tokens, raw=data if isinstance(data, dict) else None)


def list_models(settings: AISettings, *, timeout_s: int = 15) -> List[str]:
    if is_anthropic_native(settings.base_url):
        api_key = (settings.api_key or "").strip()
        if not api_key:
            raise RuntimeError("API Key 为空，请先填写。")
        return list(_ANTHROPIC_MODELS)
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

import json
import os
import re
from dataclasses import dataclass, asdict, field
from typing import Any, Dict, List


def _config_dir() -> str:
    from config_utils import get_config_dir
    return get_config_dir()


def get_ai_settings_path() -> str:
    return os.path.join(_config_dir(), "ai_settings.json")


def get_chat_memory_path() -> str:
    return os.path.join(_config_dir(), "chat_memory.json")


# ── Provider presets (OpenAI-compatible) ──
PROVIDER_PRESETS: List[Dict[str, str]] = [
    {"name": "OpenAI",              "base_url": "https://api.openai.com/v1",                                "default_model": "gpt-4o-mini"},
    {"name": "Anthropic (原生)",    "base_url": "https://api.anthropic.com",                                "default_model": "claude-sonnet-4-20250514"},
    {"name": "Google Gemini",       "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",   "default_model": "gemini-2.0-flash"},
    {"name": "DeepSeek",            "base_url": "https://api.deepseek.com",                                 "default_model": "deepseek-chat"},
    {"name": "通义千问 (Qwen)",       "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",       "default_model": "qwen-turbo"},
    {"name": "智谱 GLM",             "base_url": "https://open.bigmodel.cn/api/paas/v4",                    "default_model": "glm-4-flash"},
    {"name": "月之暗面 (Kimi)",       "base_url": "https://api.moonshot.cn/v1",                              "default_model": "moonshot-v1-8k"},
    {"name": "xAI Grok",            "base_url": "https://api.x.ai/v1",                                     "default_model": "grok-3-mini"},
    {"name": "Groq",                "base_url": "https://api.groq.com/openai/v1",                           "default_model": "llama-3.1-8b-instant"},
    {"name": "OpenRouter",          "base_url": "https://openrouter.ai/api/v1",                             "default_model": "openai/gpt-4o-mini"},
    {"name": "SiliconFlow",         "base_url": "https://api.siliconflow.cn/v1",                            "default_model": "Qwen/Qwen2.5-7B-Instruct"},
    {"name": "Ollama 本地",          "base_url": "http://localhost:11434/v1",                                "default_model": "llama3"},
    {"name": "自定义",                "base_url": "",                                                         "default_model": ""},
]

# ── Vision model heuristic patterns ──
_VISION_PATTERNS = [
    r"gpt-4o", r"gpt-4-turbo", r"gpt-4-vision",
    r"gemini",
    r"claude-3", r"claude-sonnet", r"claude-opus", r"claude-haiku",
    r"qwen-vl", r"qwen2-vl", r"qwen.*vl",
    r"glm-4v",
    r"grok-2.*vision", r"grok-3",
    r"deepseek-vl",
    r"llava", r"llama.*vision", r"pixtral", r"internvl",
]


def guess_supports_vision(model_name: str) -> bool:
    m = (model_name or "").strip().lower()
    if not m:
        return False
    for pat in _VISION_PATTERNS:
        if re.search(pat, m, re.IGNORECASE):
            return True
    return False


# ── Default prompt presets ──
DEFAULT_PROMPT_PRESETS: List[Dict[str, str]] = [
    {
        "name": "傲娇猫娘",
        "prompt": "你是一只傲娇、黏人但很聪明的桌面宠物。\n请根据主人的消息（或屏幕截图）给出符合人设的简短回复。",
    },
    {
        "name": "正经助手",
        "prompt": "你是一只认真负责的桌面宠物助手。\n请用简洁专业的语气回答主人的问题或评价截图内容。",
    },
    {
        "name": "毒舌损友",
        "prompt": "你是一只嘴毒但内心善良的桌面宠物。\n请用毒舌幽默的方式回应主人的消息或截图，但不要真的伤害主人感情。",
    },
]


# ── Domestic (China) API domains → bypass proxy, direct connect ──
_DOMESTIC_DOMAINS = [
    "api.deepseek.com",
    "dashscope.aliyuncs.com",
    "open.bigmodel.cn",
    "api.moonshot.cn",
    "api.siliconflow.cn",
    "localhost",
    "127.0.0.1",
]


def is_domestic_api(base_url: str) -> bool:
    u = (base_url or "").lower()
    for d in _DOMESTIC_DOMAINS:
        if d in u:
            return True
    return False


def is_anthropic_native(base_url: str) -> bool:
    return "api.anthropic.com" in (base_url or "").lower()


@dataclass
class AISettings:
    provider: str = "openai"
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = "gpt-4o-mini"
    system_prompt: str = (
        "你是一只傲娇、黏人但很聪明的桌面宠物。\n"
        "请根据主人的消息（或屏幕截图）给出符合人设的简短回复。"
    )
    reply_min_length: int = 20
    reply_max_length: int = 80
    auto_screenshot_interval_min: int = 0
    max_memory_turns: int = 5
    max_blackbox_logs: int = 150
    supports_vision: bool = True


def _load_json(path: str, default_obj: Dict[str, Any]) -> Dict[str, Any]:
    if not os.path.exists(path):
        return dict(default_obj)
    try:
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        return obj if isinstance(obj, dict) else dict(default_obj)
    except Exception:
        return dict(default_obj)


def _save_json(path: str, data: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def ensure_defaults() -> None:
    p = get_ai_settings_path()
    if not os.path.exists(p):
        _save_json(p, {
            "version": 2,
            "openai": asdict(AISettings()),
            "prompt_presets": [dict(d) for d in DEFAULT_PROMPT_PRESETS],
        })
    m = get_chat_memory_path()
    if not os.path.exists(m):
        _save_json(m, {"version": 1, "logs": []})


def load_ai_settings() -> AISettings:
    ensure_defaults()
    obj = _load_json(get_ai_settings_path(), {"version": 2, "openai": asdict(AISettings())})
    openai_obj = obj.get("openai") if isinstance(obj, dict) else None
    if not isinstance(openai_obj, dict):
        openai_obj = asdict(AISettings())
    defaults = AISettings()
    s = AISettings()
    s.base_url = str(openai_obj.get("base_url", defaults.base_url) or defaults.base_url)
    s.api_key = str(openai_obj.get("api_key", "") or "")
    s.model = str(openai_obj.get("model", defaults.model) or defaults.model)
    s.system_prompt = str(openai_obj.get("system_prompt", defaults.system_prompt) or defaults.system_prompt)
    s.max_bubble_length = 0  # 已废弃，保留兼容旧配置
    # 注意：这里不能用 `or defaults.xxx`，否则用户显式设置 0 会被错误回退成默认值
    s.reply_min_length = int(openai_obj.get("reply_min_length", openai_obj.get("max_bubble_length", defaults.reply_min_length)))
    s.reply_max_length = int(openai_obj.get("reply_max_length", defaults.reply_max_length))
    s.auto_screenshot_interval_min = int(openai_obj.get("auto_screenshot_interval_min", 0))
    s.max_memory_turns = int(openai_obj.get("max_memory_turns", defaults.max_memory_turns))
    s.max_blackbox_logs = int(openai_obj.get("max_blackbox_logs", defaults.max_blackbox_logs))
    s.supports_vision = bool(openai_obj.get("supports_vision", defaults.supports_vision))
    s.provider = str(openai_obj.get("provider", "openai") or "openai")
    return s


def save_ai_settings(settings: AISettings) -> None:
    ensure_defaults()
    path = get_ai_settings_path()
    obj = _load_json(path, {"version": 2, "openai": {}})
    if not isinstance(obj, dict):
        obj = {"version": 2}
    obj["version"] = 2
    obj["openai"] = {
        "provider": settings.provider,
        "base_url": settings.base_url,
        "api_key": settings.api_key,
        "model": settings.model,
        "system_prompt": settings.system_prompt,
        "reply_min_length": settings.reply_min_length,
        "reply_max_length": settings.reply_max_length,
        "auto_screenshot_interval_min": settings.auto_screenshot_interval_min,
        "max_memory_turns": settings.max_memory_turns,
        "max_blackbox_logs": settings.max_blackbox_logs,
        "supports_vision": settings.supports_vision,
    }
    _save_json(path, obj)


def load_prompt_presets() -> List[Dict[str, str]]:
    ensure_defaults()
    obj = _load_json(get_ai_settings_path(), {})
    presets = obj.get("prompt_presets")
    if isinstance(presets, list) and len(presets) > 0:
        return [p for p in presets if isinstance(p, dict) and "name" in p and "prompt" in p]
    return [dict(d) for d in DEFAULT_PROMPT_PRESETS]


def save_prompt_presets(presets: List[Dict[str, str]]) -> None:
    ensure_defaults()
    path = get_ai_settings_path()
    obj = _load_json(path, {})
    obj["prompt_presets"] = presets
    _save_json(path, obj)

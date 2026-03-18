import json
import os
import time
from typing import Any, Dict, List, Optional

from ai_config import get_chat_memory_path, ensure_defaults


def _load() -> Dict[str, Any]:
    ensure_defaults()
    path = get_chat_memory_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        if not isinstance(obj, dict):
            return {"version": 1, "logs": []}
        if not isinstance(obj.get("logs"), list):
            obj["logs"] = []
        obj.setdefault("version", 1)
        return obj
    except Exception:
        return {"version": 1, "logs": []}


def _save(obj: Dict[str, Any]) -> None:
    ensure_defaults()
    path = get_chat_memory_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def append_log(
    prompt: str,
    response: str,
    tokens: int = 0,
    kind: str = "text",
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    obj = _load()
    logs: List[Dict[str, Any]] = obj.get("logs", [])
    now_ms = int(time.time() * 1000)
    item = {
        "id": now_ms,
        "type": kind,
        "prompt": prompt,
        "response": response,
        "tokens": int(tokens or 0),
        "timestamp": time.strftime("%H:%M"),
        "ts_ms": now_ms,
    }
    if isinstance(extra, dict):
        item["extra"] = extra
    logs.insert(0, item)
    # keep last K (configurable)
    max_keep = 200
    try:
        from ai_config import load_ai_settings
        s = load_ai_settings()
        max_keep = int(getattr(s, "max_blackbox_logs", 200) or 200)
    except Exception:
        max_keep = 200
    max_keep = max(20, min(2000, int(max_keep or 200)))
    if len(logs) > max_keep:
        del logs[max_keep:]
    obj["logs"] = logs
    _save(obj)
    return item


def list_logs(limit: int = 100) -> List[Dict[str, Any]]:
    obj = _load()
    logs = obj.get("logs", [])
    if not isinstance(logs, list):
        return []
    return logs[: max(0, int(limit))]


def get_log_by_id(log_id: int) -> Optional[Dict[str, Any]]:
    obj = _load()
    for item in obj.get("logs", []):
        if isinstance(item, dict) and int(item.get("id", -1)) == int(log_id):
            return item
    return None


def delete_log(log_id: int) -> None:
    obj = _load()
    logs = obj.get("logs", [])
    if not isinstance(logs, list):
        return
    obj["logs"] = [l for l in logs if int(l.get("id", -1)) != int(log_id)]
    _save(obj)


def update_log(
    log_id: int,
    *,
    prompt: Optional[str] = None,
    response: Optional[str] = None,
    tokens: Optional[int] = None,
    kind: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
    touch_timestamp: bool = True,
) -> Optional[Dict[str, Any]]:
    """覆盖更新某条记录（不新增），返回更新后的 item。"""
    obj = _load()
    logs = obj.get("logs", [])
    if not isinstance(logs, list):
        return None
    for i, it in enumerate(logs):
        try:
            if int(it.get("id", -1)) != int(log_id):
                continue
        except Exception:
            continue
        if prompt is not None:
            it["prompt"] = str(prompt)
        if response is not None:
            it["response"] = str(response)
        if tokens is not None:
            it["tokens"] = int(tokens or 0)
        if kind is not None:
            it["type"] = str(kind)
        if isinstance(extra, dict):
            it["extra"] = dict(extra)
        if touch_timestamp:
            now_ms = int(time.time() * 1000)
            it["ts_ms"] = now_ms
            it["timestamp"] = time.strftime("%H:%M")
        logs[i] = it
        obj["logs"] = logs
        _save(obj)
        return it
    return None


def clear_logs() -> None:
    obj = _load()
    obj["logs"] = []
    _save(obj)


def get_last_watch_response() -> Optional[str]:
    """Return the response text from the most recent auto_watch log entry."""
    logs = list_logs(30)
    for item in logs:
        extra = item.get("extra", {})
        if isinstance(extra, dict) and extra.get("source") == "auto_watch":
            return item.get("response") or None
    return None


def get_recent_turns(n: int) -> List[Dict[str, str]]:
    """取最近 n 轮对话，返回 OpenAI messages 格式（从旧到新）。
    每轮 = 一条 user + 一条 assistant。"""
    if n <= 0:
        return []
    logs = list_logs(n)
    if not logs:
        return []
    # logs[0] 是最新的，需要反转成时间正序
    turns = list(reversed(logs[:n]))
    messages: List[Dict[str, str]] = []
    for item in turns:
        p = item.get("prompt", "")
        r = item.get("response", "")
        if p:
            messages.append({"role": "user", "content": p})
        if r:
            messages.append({"role": "assistant", "content": r})
    return messages

"""AI auto-watch (timed patrol) — worker thread + timer helpers, extracted from pet_core."""

from __future__ import annotations

from typing import Any, List, Optional

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal

from logger import logger


class AIWatchWorker(QThread):
    finished = pyqtSignal(str, int)
    error = pyqtSignal(str)

    def __init__(
        self,
        *,
        prompt: str,
        image_bytes: Optional[bytes] = None,
        system_prompt_extra: str = "",
        history: List,
        parent=None,
    ):
        super().__init__(parent)
        self._prompt = prompt
        self._image_bytes = image_bytes
        self._system_extra = system_prompt_extra
        self._history = history

    def run(self):
        try:
            from ai_config import load_ai_settings
            from ai_openai_client import chat_completion

            s = load_ai_settings()
            if not (s.api_key or "").strip():
                self.error.emit("未连接：请在设置→AI 填写 Key")
                return
            reply_max = int(getattr(s, "reply_max_length", 0) or 0)
            max_tok = reply_max * 4 if reply_max > 0 else None
            sys_prompt = None
            if self._system_extra:
                base = s.system_prompt or ""
                sys_prompt = base + "\n\n" + self._system_extra
            r = chat_completion(
                s,
                self._prompt,
                image_png_bytes=self._image_bytes,
                system_prompt=sys_prompt,
                history=self._history,
                max_tokens=max_tok,
                timeout_s=30,
            )
            text = (r.text or "").strip() or "(无回复)"
            self.finished.emit(text, int(r.tokens or 0))
        except Exception as e:
            self.error.emit(str(e))


def grab_screen_for_ai_watch() -> Optional[bytes]:
    try:
        from PyQt6.QtCore import QBuffer, QByteArray

        screen = QApplication.primaryScreen()
        if screen is None:
            return None
        pix = screen.grabWindow(0)
        try:
            if pix.width() > 1280:
                pix = pix.scaledToWidth(1280, Qt.TransformationMode.FastTransformation)
        except Exception:
            pass
        arr = QByteArray()
        buf = QBuffer(arr)
        buf.open(QBuffer.OpenModeFlag.WriteOnly)
        ok = pix.save(buf, "PNG")
        if not ok:
            return None
        return bytes(arr)
    except Exception:
        return None


def set_ai_watch_enabled(pet: Any, enabled: bool) -> None:
    pet.ai_watch_enabled = bool(enabled)
    if not pet.ai_watch_enabled:
        try:
            pet._ai_watch_timer.stop()
        except Exception:
            pass
        return
    refresh_ai_watch_timer(pet)


def refresh_ai_watch_timer(pet: Any) -> None:
    try:
        from ai_config import load_ai_settings

        s = load_ai_settings()
        interval = int(getattr(s, "auto_screenshot_interval_min", 0) or 0)
        if (not pet.ai_watch_enabled) or interval <= 0:
            pet._ai_watch_timer.stop()
            return
        ms = max(10_000, interval * 60 * 1000)
        pet._ai_watch_timer.start(ms)
    except Exception:
        try:
            pet._ai_watch_timer.stop()
        except Exception:
            pass


def ai_watch_tick(pet: Any) -> None:
    if not getattr(pet, "ai_watch_enabled", False) or getattr(pet, "_ai_watch_busy", False):
        return
    try:
        if pet._chat_console and getattr(pet._chat_console, "_sending", False):
            return
    except Exception:
        pass
    pet._ai_watch_busy = True

    try:
        from chat_memory import get_recent_turns
        from ai_config import load_ai_settings

        s = load_ai_settings()
        history = (
            get_recent_turns(int(getattr(s, "max_memory_turns", 0) or 0))
            if int(getattr(s, "max_memory_turns", 0) or 0) > 0
            else None
        )
    except Exception:
        s = None
        history = None

    from ai_openai_client import _is_reasoner_model

    is_reasoner = _is_reasoner_model(getattr(s, "model", "") if s else "")
    vision = bool(getattr(s, "supports_vision", True)) if s else True
    if is_reasoner:
        vision = False

    img = None
    if vision:
        img = grab_screen_for_ai_watch()

    if vision and img:
        prompt = "这是定时自动截取的屏幕画面，请根据画面内容给出符合你人设的主动发言。"
        kind = "image"
        log_prompt = "[自动巡视截屏]"
    else:
        prompt = "你正在定时巡视中。请根据对话记忆和你的人设，主动说一些有趣的话。"
        kind = "text"
        log_prompt = "[自动巡视]"
        img = None

    sys_extra = ""
    try:
        from chat_memory import get_last_watch_response

        last_resp = get_last_watch_response()
        if last_resp:
            sys_extra = (
                f"【防重复提示】你上次巡视时说了：「{last_resp[:120]}」。"
                "请换一个角度或话题，不要重复类似的内容。"
            )
    except Exception:
        pass

    worker = AIWatchWorker(
        prompt=prompt,
        image_bytes=img,
        system_prompt_extra=sys_extra,
        history=(history or []),
        parent=pet,
    )
    pet._ai_watch_worker = worker

    def _done(text: str, tokens: int):
        try:
            from chat_memory import append_log

            append_log(
                prompt=log_prompt,
                response=text,
                tokens=tokens,
                kind=kind,
                extra={"source": "auto_watch"},
            )
        except Exception:
            pass
        bubble_text = text
        try:
            bl = int(getattr(s, "reply_max_length", 60) or 60)
            bubble_limit = bl + 20 if bl > 0 else 0
            if bubble_limit > 0 and len(bubble_text) > bubble_limit:
                bubble_text = bubble_text[:bubble_limit] + "\u2026"
        except Exception:
            pass
        try:
            pet._show_activity_bubble("\U0001F50D " + bubble_text)
        except Exception:
            try:
                pet._request_notice(text)
            except Exception:
                pass
        pet._ai_watch_retry_count = 0
        pet._ai_watch_busy = False
        try:
            worker.deleteLater()
        except Exception:
            pass

    def _err(msg: str):
        logger.warning(f"自动巡视失败: {msg}")
        retry_count = getattr(pet, "_ai_watch_retry_count", 0)
        if retry_count < 1:
            pet._ai_watch_retry_count = retry_count + 1
            logger.info("自动巡视：首次失败，自动重试…")
            pet._ai_watch_busy = False
            try:
                worker.deleteLater()
            except Exception:
                pass
            QTimer.singleShot(3000, pet._ai_watch_tick)
            return
        pet._ai_watch_retry_count = 0
        pet._ai_watch_busy = False
        try:
            worker.deleteLater()
        except Exception:
            pass

    worker.finished.connect(_done)
    worker.error.connect(_err)
    worker.start()

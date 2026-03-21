import os
import time
from typing import Optional, List, Dict

from PyQt6.QtCore import Qt, QTimer, QPoint, QThread, pyqtSignal, QSize
from PyQt6.QtGui import QCursor, QGuiApplication, QAction, QIcon
from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QListWidget,
    QListWidgetItem,
    QCheckBox,
    QMessageBox,
    QWidget,
    QMenu,
    QSizePolicy,
    QToolButton,
    QInputDialog,
    QFrame,
    QLineEdit,
    QStyle,
    QStackedWidget,
)

from ai_config import load_ai_settings
from ai_openai_client import chat_completion
from chat_memory import (
    append_log, list_logs, get_log_by_id, delete_log, clear_logs, update_log,
    get_recent_turns,
)

from config_utils import get_config_dir


class _AIWorker(QThread):
    """后台线程：调用 OpenAI，不阻塞 GUI。"""
    finished = pyqtSignal(str, int, str)
    error = pyqtSignal(str)

    def __init__(
        self,
        user_text: str,
        image_bytes: Optional[bytes],
        history: Optional[List[Dict[str, str]]] = None,
        parent=None,
    ):
        super().__init__(parent)
        self._user_text = user_text
        self._image_bytes = image_bytes
        self._history = history

    def run(self):
        try:
            settings = load_ai_settings()
            if not (settings.api_key or "").strip():
                self.error.emit("未连接：请先在设置→AI 填写 API Key")
                return
            reply_max = int(getattr(settings, "reply_max_length", 0) or 0)
            max_tok = reply_max * 4 if reply_max > 0 else None
            reply = chat_completion(
                settings,
                self._user_text or "请根据截图给出反馈。",
                image_png_bytes=self._image_bytes,
                history=self._history,
                max_tokens=max_tok,
                timeout_s=30,
            )
            text = (reply.text or "").strip() or "(无回复)"
            kind = "image" if self._image_bytes else "text"
            self.finished.emit(text, reply.tokens, kind)
        except Exception as e:
            self.error.emit(str(e))


class ChatConsole(QDialog):
    def __init__(self, pet, parent=None):
        super().__init__(parent)
        self.pet = pet
        self._worker: Optional[_AIWorker] = None
        self.setWindowTitle("桌宠控制台")
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.FramelessWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setFixedSize(380, 540)

        self._sending = False
        self._pin = True
        self._show_history = False
        self._dragging = False
        self._drag_offset = QPoint(0, 0)
        self._prompt_cache = ""
        self._attach_cache = False
        self._auto_watch_timer: Optional[QTimer] = None
        self._pending_update_log_id: Optional[int] = None
        self._compact_mode = True
        self._expanded_log_ids = set()
        self._item_by_log_id: Dict[int, QListWidgetItem] = {}
        self._status_reset_timer = QTimer()
        self._status_reset_timer.setSingleShot(True)
        self._status_reset_timer.timeout.connect(lambda: self._set_status("ready", "待命中"))

        root = QVBoxLayout()
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(6)

        # ── top bar (draggable) ──
        top = QHBoxLayout()
        title = QLabel("桌宠控制台")
        title.setObjectName("ConsoleTitle")
        top.addWidget(title)
        top.addStretch(1)

        # 顶部：图标按钮（紧凑/完整都显示）
        self.btn_pin = QToolButton()
        self.btn_pin.setObjectName("PinButton")
        self.btn_pin.setCheckable(True)
        self.btn_pin.setChecked(True)
        self.btn_pin.setToolTip("置顶")
        # 用图钉字符，和“展开/收起箭头”明显区分
        self.btn_pin.setText("📌")
        self.btn_pin.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.btn_pin.clicked.connect(self._toggle_pin)
        top.addWidget(self.btn_pin)

        self.btn_toggle_size = QToolButton()
        self.btn_toggle_size.setObjectName("ToggleSizeButton")
        self.btn_toggle_size.setToolTip("展开/收起")
        self.btn_toggle_size.setIconSize(QSize(14, 14))
        self.btn_toggle_size.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowDown))
        self.btn_toggle_size.clicked.connect(self._toggle_compact_mode)
        top.addWidget(self.btn_toggle_size)

        self.btn_close = QToolButton()
        self.btn_close.setObjectName("CloseButton")
        self.btn_close.setToolTip("退出")
        self.btn_close.setIconSize(QSize(14, 14))
        self.btn_close.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_TitleBarCloseButton))
        self.btn_close.clicked.connect(self.close)
        top.addWidget(self.btn_close)

        topw = QWidget()
        topw.setLayout(top)
        topw.setObjectName("TopBar")
        topw.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        topw.installEventFilter(self)
        root.addWidget(topw)

        # ── status ──
        self.lbl_status = QLabel("")
        self.lbl_status.setStyleSheet("font-size:11px;")
        root.addWidget(self.lbl_status)

        # ── history section (固定区域，避免按钮上下跳) ──
        self.history_container = QWidget()
        hc = QVBoxLayout()
        hc.setContentsMargins(0, 0, 0, 0)
        hc.setSpacing(6)

        bar = QHBoxLayout()
        self.btn_hist = QPushButton("记忆黑匣子")
        self.btn_hist.setCheckable(True)
        self.btn_hist.setChecked(False)
        self.btn_hist.clicked.connect(self._toggle_history)
        bar.addWidget(self.btn_hist)

        self.btn_clear = QPushButton("清空")
        self.btn_clear.clicked.connect(self._clear_history)
        bar.addWidget(self.btn_clear)
        bar.addStretch(1)
        hc.addLayout(bar)

        self.history_stack = QStackedWidget()
        self.history_stack.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)

        self.history_placeholder = QLabel("（黑匣子已收起）")
        self.history_placeholder.setStyleSheet("color: gray;")
        self.history_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.history_stack.addWidget(self.history_placeholder)

        self.list_history = QListWidget()
        self.history_stack.addWidget(self.list_history)

        hc.addWidget(self.history_stack, 1)
        self.history_container.setLayout(hc)
        root.addWidget(self.history_container, 1)

        # ── controls ──
        ctrl = QHBoxLayout()
        self.cb_attach_screen = QCheckBox("附带截图")
        self.cb_attach_screen.setToolTip("发送时附带当前屏幕截图（需要模型支持视觉）")
        ctrl.addWidget(self.cb_attach_screen)

        self.cb_auto_watch = QCheckBox("自动巡视")
        self.cb_auto_watch.setToolTip("按设置的间隔定时截屏，桌宠主动发言")
        self.cb_auto_watch.stateChanged.connect(self._toggle_auto_watch)
        ctrl.addWidget(self.cb_auto_watch)

        self.cb_app_bubbles = QCheckBox("应用检测气泡")
        self.cb_app_bubbles.setToolTip("只控制“检测应用触发的文案气泡”，与 AI 对话无关")
        self.cb_app_bubbles.stateChanged.connect(self._toggle_app_bubbles)
        ctrl.addWidget(self.cb_app_bubbles)
        ctrl.addStretch(1)
        root.addLayout(ctrl)

        # ── input ──
        self.ed_input_line = QLineEdit()
        self.ed_input_line.setPlaceholderText("想说点什么…（Enter 发送）")
        self.ed_input_line.returnPressed.connect(self._send)
        root.addWidget(self.ed_input_line)

        self.ed_input = QTextEdit()
        self.ed_input.setPlaceholderText("想说点什么…（Enter 发送，Shift+Enter 换行）")
        self.ed_input.setFixedHeight(90)
        self.ed_input.installEventFilter(self)
        root.addWidget(self.ed_input)

        # ── send ──
        send_row = QHBoxLayout()
        send_row.addStretch(1)
        self.btn_send = QPushButton("发送")
        self.btn_send.clicked.connect(self._send)
        self.btn_send.setDefault(True)
        send_row.addWidget(self.btn_send)
        root.addLayout(send_row)

        # stretch：历史区可伸缩，输入区固定
        root.setStretchFactor(self.history_container, 1)
        self.setObjectName("ChatConsole")
        self.setLayout(root)
        self._apply_frontend_theme()
        self._reload_history()

        # initial status
        try:
            s = load_ai_settings()
            if (s.api_key or "").strip():
                self._set_status("ready", "待命中")
            else:
                self._set_status("warn", "未连接：请在设置→AI 填写 Key")
        except Exception:
            self._set_status("warn", "未连接：请在设置→AI 填写 Key")

        # disable activity bubbles while console is open
        self._sync_app_bubbles_checkbox_from_config()
        self._sync_auto_watch_from_pet()
        self._sync_vision_state()

        # default: compact mode
        self._apply_compact_mode(True)

    # ═══════ window ═══════
    def _toggle_pin(self):
        self._pin = bool(self.btn_pin.isChecked())
        flags = self.windowFlags()
        if self._pin:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        else:
            flags &= ~Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.show()

    def _toggle_compact_mode(self):
        self._apply_compact_mode(not self._compact_mode)

    def _apply_compact_mode(self, compact: bool):
        self._compact_mode = bool(compact)
        if self._compact_mode:
            self.btn_hist.setChecked(False)
            self._toggle_history()
            # 紧凑模式隐藏整个黑匣子区域
            self.history_container.setVisible(False)
            self.lbl_status.setVisible(True)
            # 紧凑版：三个开关保留文字
            self.cb_attach_screen.setVisible(True)
            self.cb_auto_watch.setVisible(True)
            self.cb_app_bubbles.setVisible(True)
            # one-line input only
            self.ed_input.setVisible(False)
            self.ed_input_line.setVisible(True)
            # 顶部图标：展开=向下
            self.btn_toggle_size.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowDown))
            # Frontend theme uses larger paddings; compact height needs extra room.
            self._set_fixed_height(196)
        else:
            self.history_container.setVisible(True)
            self.lbl_status.setVisible(True)
            self.cb_attach_screen.setVisible(True)
            self.cb_auto_watch.setVisible(True)
            self.cb_app_bubbles.setVisible(True)
            self.ed_input.setVisible(True)
            self.ed_input_line.setVisible(False)
            # 顶部图标：收起=向上
            self.btn_toggle_size.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowUp))
            self._set_fixed_height(540)

    def _set_fixed_height(self, h: int):
        try:
            # 底部对齐：高度变化时保持窗口底部不动
            geo = self.geometry()
            old_h = int(geo.height())
            new_h = int(h)
            if old_h == new_h:
                self.setFixedHeight(new_h)
                return
            bottom_y = geo.y() + old_h
            self.setFixedHeight(new_h)
            new_y = bottom_y - new_h
            self.move(geo.x(), new_y)
        except Exception:
            try:
                self.setFixedHeight(int(h))
            except Exception:
                pass

    def eventFilter(self, obj, event):
        if getattr(obj, "objectName", lambda: "")() == "TopBar":
            if event.type() == event.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self._dragging = True
                self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                return True
            if event.type() == event.Type.MouseMove and self._dragging:
                self.move(event.globalPosition().toPoint() - self._drag_offset)
                return True
            if event.type() == event.Type.MouseButtonRelease:
                self._dragging = False
                return True
        if obj is self.ed_input and event.type() == event.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
                self._send()
                return True
        return super().eventFilter(obj, event)

    # ═══════ status (with color) ═══════
    def _set_status(self, level: str, msg: str):
        self.lbl_status.setProperty("statusLevel", level or "ready")
        self.lbl_status.style().unpolish(self.lbl_status)
        self.lbl_status.style().polish(self.lbl_status)
        self.lbl_status.setText(msg)

    def _apply_frontend_theme(self):
        """Apply a modern frontend-like visual style without changing layout."""
        self.setStyleSheet(
            """
            QDialog#ChatConsole {
                background: #f5f7fb;
                border: 1px solid #dbe4f0;
                border-radius: 12px;
            }
            QWidget#TopBar {
                background: #ffffff;
                border: 1px solid #e8eef7;
                border-radius: 10px;
            }
            QLabel#ConsoleTitle {
                color: #1f2937;
                font-size: 14px;
                font-weight: 700;
            }
            QLabel {
                color: #1f2937;
                font-size: 12px;
            }
            QLabel[statusLevel="ready"] {
                background: #ecfdf5;
                color: #047857;
                border: 1px solid #a7f3d0;
                border-radius: 10px;
                padding: 4px 10px;
                font-weight: 600;
            }
            QLabel[statusLevel="busy"] {
                background: #eff6ff;
                color: #1d4ed8;
                border: 1px solid #bfdbfe;
                border-radius: 10px;
                padding: 4px 10px;
                font-weight: 600;
            }
            QLabel[statusLevel="ok"] {
                background: #ecfdf5;
                color: #047857;
                border: 1px solid #6ee7b7;
                border-radius: 10px;
                padding: 4px 10px;
                font-weight: 600;
            }
            QLabel[statusLevel="warn"] {
                background: #fff7ed;
                color: #c2410c;
                border: 1px solid #fdba74;
                border-radius: 10px;
                padding: 4px 10px;
                font-weight: 600;
            }
            QLabel[statusLevel="error"] {
                background: #fef2f2;
                color: #b91c1c;
                border: 1px solid #fca5a5;
                border-radius: 10px;
                padding: 4px 10px;
                font-weight: 600;
            }
            QPushButton {
                background: #ffffff;
                color: #1f2937;
                border: 1px solid #dbe4f0;
                border-radius: 8px;
                padding: 6px 12px;
                font-size: 12px;
            }
            QPushButton:hover {
                background: #f8fbff;
                border: 1px solid #b8d0f2;
            }
            QPushButton:pressed {
                background: #edf4ff;
            }
            QPushButton#qt_default_push_button, QPushButton:default {
                background: #2f6fed;
                color: white;
                border: 1px solid #2f6fed;
                font-weight: 700;
            }
            QPushButton#qt_default_push_button:hover, QPushButton:default:hover {
                background: #255fce;
            }
            QToolButton {
                background: #f8fbff;
                border: 1px solid #d6e0ef;
                border-radius: 7px;
                padding: 4px;
                color: #334155;
            }
            QToolButton:hover {
                background: #e9f2ff;
                border: 1px solid #b8d0f2;
            }
            QToolButton:pressed {
                background: #dbeafe;
                border: 1px solid #93c5fd;
            }
            QToolButton:disabled {
                background: #f3f4f6;
                border: 1px solid #e5e7eb;
                color: #9ca3af;
            }
            QToolButton#CloseButton:hover {
                background: #fee2e2;
                border: 1px solid #fca5a5;
                color: #b91c1c;
            }
            QToolButton#PinButton:checked {
                background: #e0ecff;
                border: 1px solid #93b7f3;
                color: #1d4ed8;
            }
            QLineEdit, QTextEdit {
                background: #ffffff;
                border: 1px solid #d4deed;
                border-radius: 9px;
                padding: 8px;
                color: #111827;
                selection-background-color: #bfdbfe;
            }
            QLineEdit:focus, QTextEdit:focus {
                border: 1px solid #5b9cff;
            }
            QListWidget {
                background: #ffffff;
                border: 1px solid #dbe4f0;
                border-radius: 10px;
                outline: 0;
            }
            QCheckBox {
                spacing: 6px;
                color: #1f2937;
            }
            QCheckBox:disabled {
                color: #9ca3af;
            }
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
            }
            QCheckBox::indicator:unchecked {
                border: 1px solid #b8c5d9;
                border-radius: 4px;
                background: #ffffff;
            }
            QCheckBox::indicator:checked {
                border: 1px solid #2f6fed;
                border-radius: 4px;
                background: #2f6fed;
            }
            QCheckBox::indicator:unchecked:disabled {
                border: 1px solid #d1d5db;
                border-radius: 4px;
                background: #f3f4f6;
            }
            QCheckBox::indicator:checked:disabled {
                border: 1px solid #93c5fd;
                border-radius: 4px;
                background: #bfdbfe;
            }
            """
        )

    # ═══════ history / blackbox ═══════
    def _toggle_history(self):
        self._show_history = bool(self.btn_hist.isChecked())
        self.history_stack.setCurrentIndex(1 if self._show_history else 0)
        self._reload_history()

    def _reload_history(self, incremental: bool = False):
        if not self._show_history:
            return
        logs = list_logs(50)
        if incremental and logs and self.list_history.count() > 0:
            newest = logs[0]
            nid = int(newest.get("id", 0) or 0)
            if nid and nid not in self._item_by_log_id:
                it = QListWidgetItem()
                it.setData(Qt.ItemDataRole.UserRole, nid)
                self.list_history.insertItem(0, it)
                self._item_by_log_id[nid] = it
                self._render_history_item_widget(it, newest)
                return
        scrollbar = self.list_history.verticalScrollBar()
        old_scroll = scrollbar.value() if scrollbar else 0
        self.list_history.clear()
        self._item_by_log_id = {}
        for item in logs:
            it = QListWidgetItem()
            log_id = int(item.get("id", 0) or 0)
            it.setData(Qt.ItemDataRole.UserRole, log_id)
            self.list_history.addItem(it)
            self._item_by_log_id[log_id] = it
            self._render_history_item_widget(it, item)
        if scrollbar and old_scroll > 0:
            QTimer.singleShot(0, lambda: scrollbar.setValue(old_scroll))

    def _render_history_item_widget(self, it: QListWidgetItem, data: dict):
        log_id = int(data.get("id", 0) or 0)
        ts = str(data.get("timestamp", "") or "")
        tok = int(data.get("tokens", 0) or 0)
        kind = str(data.get("type", "text") or "text")
        q = str(data.get("prompt", "") or "")
        a = str(data.get("response", "") or "")
        expanded = log_id in self._expanded_log_ids

        w = QFrame()
        w.setFrameShape(QFrame.Shape.NoFrame)
        try:
            pal = self.palette()
            win = pal.color(pal.ColorRole.Window)
            lum = 0.2126 * win.red() + 0.7152 * win.green() + 0.0722 * win.blue()
            if lum < 128:
                w.setStyleSheet("QFrame{background:rgba(255,255,255,0.06); border:1px solid rgba(255,255,255,0.10); border-radius:10px;}")
            else:
                w.setStyleSheet("QFrame{background:rgba(0,0,0,0.03); border:1px solid rgba(0,0,0,0.08); border-radius:10px;}")
        except Exception:
            pass
        row = QVBoxLayout()
        row.setContentsMargins(8, 6, 8, 6)
        row.setSpacing(2)

        top_row = QHBoxLayout()
        lbl_meta = QLabel(f"[{ts}]  {kind}  {tok}tok")
        lbl_meta.setStyleSheet("font-size:10px; color: gray;")
        top_row.addWidget(lbl_meta)
        top_row.addStretch(1)
        row.addLayout(top_row)

        full_text = f"Q: {q}\nA: {a}"
        if expanded:
            lbl_text = QLabel(full_text)
            lbl_text.setWordWrap(True)
            lbl_text.setStyleSheet("font-size:11px; font-family: 'Segoe UI Emoji', 'Segoe UI', sans-serif;")
            row.addWidget(lbl_text)
            _f = lbl_text.font(); _f.setFamily("Segoe UI Emoji"); lbl_text.setFont(_f)
        else:
            q_short = q[:80] + ("…" if len(q) > 80 else "")
            a_short = a[:80] + ("…" if len(a) > 80 else "")
            lbl_text = QLabel(f"Q: {q_short}\nA: {a_short}")
            lbl_text.setWordWrap(True)
            lbl_text.setStyleSheet("font-size:11px; font-family: 'Segoe UI Emoji', 'Segoe UI', sans-serif;")
            lbl_text.setMaximumHeight(lbl_text.fontMetrics().lineSpacing() * 3 + 4)
            row.addWidget(lbl_text)
            _f = lbl_text.font(); _f.setFamily("Segoe UI Emoji"); lbl_text.setFont(_f)

        btns = QHBoxLayout()
        b_expand = QToolButton()
        b_expand.setToolTip("展开全文" if not expanded else "收起")
        b_expand.setIcon(self.style().standardIcon(
            QStyle.StandardPixmap.SP_ArrowDown if not expanded else QStyle.StandardPixmap.SP_ArrowUp
        ))
        b_expand.clicked.connect(lambda: self._toggle_expand_log(log_id))
        btns.addWidget(b_expand)
        btns.addStretch(1)

        b_retry = QToolButton()
        b_retry.setText("⟳")
        b_retry.setToolTip("重试")
        b_retry.clicked.connect(lambda: self._history_retry(log_id))
        btns.addWidget(b_retry)

        b_edit = QToolButton()
        b_edit.setText("✎")
        b_edit.setToolTip("修改")
        b_edit.clicked.connect(lambda: self._history_edit(log_id))
        btns.addWidget(b_edit)

        b_del = QToolButton()
        b_del.setText("🗑")
        b_del.setToolTip("删除")
        b_del.clicked.connect(lambda: self._history_delete(log_id))
        btns.addWidget(b_del)
        row.addLayout(btns)

        w.setLayout(row)

        if expanded:
            w.adjustSize()
            need_h = max(100, w.sizeHint().height() + 8)
        else:
            need_h = 92
        it.setSizeHint(QSize(320, need_h))
        self.list_history.setItemWidget(it, w)

    def _toggle_expand_log(self, log_id: int):
        try:
            lid = int(log_id)
        except Exception:
            return
        if lid in self._expanded_log_ids:
            self._expanded_log_ids.remove(lid)
        else:
            self._expanded_log_ids.add(lid)
        entry = get_log_by_id(lid)
        it = self._item_by_log_id.get(lid)
        if it and entry:
            self._render_history_item_widget(it, entry)

    def _history_view(self, log_id: int):
        entry = get_log_by_id(log_id)
        if not entry:
            return
        ts = entry.get("timestamp", "")
        tok = entry.get("tokens", 0)
        kind = entry.get("type", "text")
        prompt = entry.get("prompt", "")
        response = entry.get("response", "")
        text = (
            f"时间: {ts}    类型: {kind}    Token: {tok}\n"
            f"{'─' * 40}\n"
            f"Q: {prompt}\n"
            f"{'─' * 40}\n"
            f"A: {response}"
        )
        QMessageBox.information(self, "对话详情", text)

    def _history_retry(self, log_id: int):
        if self._sending:
            return
        entry = get_log_by_id(log_id)
        if not entry:
            return
        prompt = str(entry.get("prompt", "") or "")
        if not prompt:
            return
        is_image = str(entry.get("type", "") or "") == "image"
        # 截图类型：重试时重新截图（不是复用旧图）
        self._pending_update_log_id = int(log_id)
        if is_image:
            self.cb_attach_screen.setChecked(True)
            self.ed_input.setPlainText("")  # 截图型提示语走 entry.prompt
            self._send_override(prompt_text=prompt, force_screenshot=True)
        else:
            self.cb_attach_screen.setChecked(False)
            if self._compact_mode:
                self.ed_input_line.setText(prompt)
            else:
                self.ed_input.setPlainText(prompt)
            self._send_override(prompt_text=prompt, force_screenshot=False)

    def _history_edit(self, log_id: int):
        if self._sending:
            return
        entry = get_log_by_id(log_id)
        if not entry:
            return
        old_prompt = str(entry.get("prompt", "") or "")
        new_prompt, ok = QInputDialog.getMultiLineText(self, "修改这条记录", "Prompt：", old_prompt)
        if not ok:
            return
        new_prompt = (new_prompt or "").strip()
        if not new_prompt:
            return
        # 覆盖 prompt，然后重新跑一次（截图类型按当前屏幕重新截图）
        is_image = str(entry.get("type", "") or "") == "image"
        self._pending_update_log_id = int(log_id)
        self._send_override(prompt_text=new_prompt, force_screenshot=is_image)

    def _history_delete(self, log_id: int):
        if QMessageBox.question(self, "删除", "确定删除这条记录吗？") != QMessageBox.StandardButton.Yes:
            return
        delete_log(int(log_id))
        self._reload_history()

    def _clear_history(self):
        if QMessageBox.question(self, "确认", "确定要清空记忆黑匣子吗？") != QMessageBox.StandardButton.Yes:
            return
        clear_logs()
        self._reload_history()

    # ═══════ auto-watch (定时截屏巡视) ═══════
    def _toggle_auto_watch(self, state):
        enabled = state == Qt.CheckState.Checked.value
        try:
            if hasattr(self.pet, "set_ai_watch_enabled"):
                self.pet.set_ai_watch_enabled(bool(enabled))
                if hasattr(self.pet, "_refresh_ai_watch_timer"):
                    self.pet._refresh_ai_watch_timer()
        except Exception:
            pass
        self._sync_auto_watch_from_pet()
        self._push_auto_watch_to_settings(enabled)

    def _push_auto_watch_to_settings(self, enabled):
        """同步自动巡视状态到设置面板。"""
        try:
            dlg = getattr(self.pet, "_settings_dialog", None)
            if dlg and hasattr(dlg, "cb_auto_watch_ai"):
                dlg.cb_auto_watch_ai.blockSignals(True)
                dlg.cb_auto_watch_ai.setChecked(bool(enabled))
                dlg.cb_auto_watch_ai.blockSignals(False)
        except Exception:
            pass

    def _sync_auto_watch_from_pet(self):
        try:
            settings = load_ai_settings()
            interval = int(getattr(settings, "auto_screenshot_interval_min", 0) or 0)
        except Exception:
            interval = 0
        try:
            running = bool(getattr(self.pet, "ai_watch_enabled", False))
        except Exception:
            running = bool(self.cb_auto_watch.isChecked())
        # keep checkbox in sync
        try:
            self.cb_auto_watch.blockSignals(True)
            self.cb_auto_watch.setChecked(bool(running))
        finally:
            try:
                self.cb_auto_watch.blockSignals(False)
            except Exception:
                pass
        if running and interval > 0:
            self._set_status("ready", f"自动巡视：后台运行中（每 {interval} 分钟）")
        elif running and interval <= 0:
            self._set_status("warn", "自动巡视：间隔为 0，请先在设置→AI 设置")
        else:
            # don't overwrite other statuses if sending
            if not self._sending:
                self._set_status("ready", "待命中")




    # ═══════ screenshot ═══════
    def _grab_screen_png(self) -> Optional[bytes]:
        try:
            from PyQt6.QtCore import QBuffer, QByteArray
            screen = QGuiApplication.primaryScreen()
            if screen is None:
                return None
            pix = screen.grabWindow(0)
            # 缩放到 1280 宽以内，降低体积
            try:
                if pix.width() > 1280:
                    pix = pix.scaledToWidth(1280, Qt.TransformationMode.SmoothTransformation)
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

    # ═══════ 应用检测气泡（活动 bubbles）开关 ═══════
    def _bubbles_path(self) -> str:
        return os.path.join(get_config_dir(), "bubbles.json")

    def _sync_app_bubbles_checkbox_from_config(self):
        try:
            from config_utils import load_json as _load_json
            obj = _load_json(self._bubbles_path(), {"version": 1, "settings": {}})
            enabled = bool((obj.get("settings") or {}).get("enabled", True))
            self.cb_app_bubbles.setChecked(enabled)
        except Exception:
            # fallback: reflect current pet state
            try:
                self.cb_app_bubbles.setChecked(bool(getattr(self.pet, "activity_bubbles_enabled", True)))
            except Exception:
                pass

    def _toggle_app_bubbles(self, state):
        enabled = state == Qt.CheckState.Checked.value
        # persist to bubbles.json and apply immediately
        try:
            from config_utils import load_json as _load_json, save_json as _save_json
            obj = _load_json(self._bubbles_path(), {"version": 1, "settings": {}})
            if "settings" not in obj or not isinstance(obj.get("settings"), dict):
                obj["settings"] = {}
            obj["settings"]["enabled"] = bool(enabled)
            _save_json(self._bubbles_path(), obj)
        except Exception:
            pass
        try:
            if hasattr(self.pet, "set_activity_bubbles_enabled"):
                self.pet.set_activity_bubbles_enabled(bool(enabled))
        except Exception:
            pass

    # ═══════ vision state ═══════
    def _sync_vision_state(self):
        try:
            s = load_ai_settings()
            vision = bool(s.supports_vision)
        except Exception:
            vision = True
        self.cb_attach_screen.setEnabled(vision)
        if not vision:
            self.cb_attach_screen.setChecked(False)
            self.cb_attach_screen.setToolTip("当前模型不支持视觉，无法附带截图")
        else:
            self.cb_attach_screen.setToolTip("发送时附带当前屏幕截图（需要模型支持视觉）")

    # ═══════ thinking state ═══════
    def _set_pet_thinking(self, thinking: bool):
        try:
            if hasattr(self.pet, "set_thinking"):
                self.pet.set_thinking(bool(thinking))
        except Exception:
            pass

    # ═══════ send (async via QThread) ═══════
    def _send(self):
        if self._sending:
            return
        if self._compact_mode:
            text = (self.ed_input_line.text() or "").strip()
        else:
            text = (self.ed_input.toPlainText() or "").strip()
        if not text and not self.cb_attach_screen.isChecked():
            return

        try:
            if getattr(self.pet, "state", None) == "SLEEP" and hasattr(self.pet, "_trigger_poke"):
                self.pet._trigger_poke()
        except Exception:
            pass

        attach = bool(self.cb_attach_screen.isChecked())
        screenshot_bytes = self._grab_screen_png() if attach else None
        if attach and screenshot_bytes is None:
            self._set_status("warn", "截图失败，仅发送文本")

        self._prompt_cache = text if text else ("[截取了当前屏幕画面]" if attach else "")
        self._attach_cache = attach

        self.ed_input.setPlainText("")
        self.ed_input_line.setText("")
        self.ed_input.setReadOnly(True)
        self.ed_input_line.setReadOnly(True)
        self.ed_input.setPlaceholderText("桌宠正在思考中…")
        self.ed_input_line.setPlaceholderText("桌宠正在思考中…")

        self._start_ai_worker(text or "请根据截图给出反馈。", screenshot_bytes if attach else None)

    def _send_override(self, *, prompt_text: str, force_screenshot: bool):
        if self._sending:
            return
        try:
            if getattr(self.pet, "state", None) == "SLEEP" and hasattr(self.pet, "_trigger_poke"):
                self.pet._trigger_poke()
        except Exception:
            pass

        screenshot_bytes = self._grab_screen_png() if force_screenshot else None
        if force_screenshot and screenshot_bytes is None:
            self._set_status("warn", "截图失败，仅发送文本")
        self._prompt_cache = prompt_text
        self._attach_cache = bool(force_screenshot and screenshot_bytes)

        self._start_ai_worker(
            prompt_text or "请根据截图给出反馈。",
            screenshot_bytes if (force_screenshot and screenshot_bytes) else None,
        )

    def _start_ai_worker(self, user_text: str, screenshot_bytes: Optional[bytes]):
        """Common worker setup shared by _send and _send_override."""
        self._sending = True
        self._status_reset_timer.stop()
        self.btn_send.setEnabled(False)
        self.btn_send.setText("思考中…")
        self._set_status("busy", "正在努力理解中…")
        self._set_pet_thinking(True)

        settings = load_ai_settings()
        history = get_recent_turns(settings.max_memory_turns) if settings.max_memory_turns > 0 else None

        worker = _AIWorker(user_text, screenshot_bytes, history=history, parent=self)
        worker.finished.connect(self._on_ai_done)
        worker.error.connect(self._on_ai_error)
        worker.finished.connect(worker.deleteLater)
        worker.error.connect(worker.deleteLater)
        self._worker = worker
        worker.start()

    def _on_ai_done(self, response_text: str, tokens: int, kind: str):
        self._set_pet_thinking(False)
        settings = load_ai_settings()
        bubble_text = response_text

        extra = {"source": "console"}
        if self._pending_update_log_id:
            update_log(
                int(self._pending_update_log_id),
                prompt=self._prompt_cache,
                response=response_text,
                tokens=tokens,
                kind=kind,
                extra=extra,
            )
        else:
            append_log(
                prompt=self._prompt_cache,
                response=response_text,
                tokens=tokens,
                kind=kind,
                extra=extra,
            )
        self._pending_update_log_id = None

        def _show():
            try:
                if hasattr(self.pet, "_show_activity_bubble"):
                    self.pet._show_activity_bubble(bubble_text)
                elif hasattr(self.pet, "_request_notice"):
                    self.pet._request_notice(bubble_text)
            except Exception:
                pass

        delay_ms = 0
        try:
            if getattr(self.pet, "state", None) == "SLEEP":
                delay_ms = int(getattr(self.pet, "sleep_poke_ms", 800)) + 50
        except Exception:
            pass
        QTimer.singleShot(max(0, delay_ms), _show)

        self._set_status("ok", f"已送达桌面（{tokens}tok）")
        self._status_reset_timer.start(3000)
        self.ed_input.setReadOnly(False)
        self.ed_input_line.setReadOnly(False)
        self.ed_input.setPlaceholderText("想说点什么…（Enter 发送，Shift+Enter 换行）")
        self.ed_input_line.setPlaceholderText("想说点什么…（Enter 发送）")
        self._reload_history(incremental=True)
        self._sending = False
        self.btn_send.setEnabled(True)
        self.btn_send.setText("发送")

    def _on_ai_error(self, msg: str):
        self._set_pet_thinking(False)
        self._set_status("error", f"失败：{msg}")
        self._status_reset_timer.start(5000)
        self.ed_input.setReadOnly(False)
        self.ed_input_line.setReadOnly(False)
        self.ed_input.setPlaceholderText("想说点什么…（Enter 发送，Shift+Enter 换行）")
        self.ed_input_line.setPlaceholderText("想说点什么…（Enter 发送）")
        self._sending = False
        self.btn_send.setEnabled(True)
        self.btn_send.setText("发送")
        self._pending_update_log_id = None

    def showEvent(self, event):
        super().showEvent(event)
        self._sync_vision_state()

    def closeEvent(self, event):
        self._set_pet_thinking(False)
        super().closeEvent(event)

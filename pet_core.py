import sys
import os
import random
import json
import time
import math
from collections import deque
from typing import Optional

import ctypes
from ctypes import wintypes

from PyQt6.QtWidgets import QApplication, QLabel, QMainWindow, QSystemTrayIcon, QMenu, QGraphicsDropShadowEffect, QStyle, QWidget
from PyQt6.QtCore import Qt, QTimer, QTime, QSize, QRectF, QRect, QStandardPaths
from PyQt6.QtGui import QCursor, QPixmap, QPainter, QColor, QFont, QIcon, QShortcut, QAction, QKeySequence

from logger import logger

from pet_animations import (
    PET_SIZE_DEFAULT,
    MIN_SIZE,
    MAX_SIZE,
    SIZE_STEP,
    AnimatedLabel,
    load_movie_for_key,
    rebuild_pet_movies,
)
from pet_bubbles import BubbleWidget, PetActivityBubblesMixin
from pet_ai_watch import (
    ai_watch_tick,
    grab_screen_for_ai_watch,
    refresh_ai_watch_timer,
    set_ai_watch_enabled as _set_ai_watch_enabled_impl,
)

# 点击 vs 拖拽：移动超过这个像素才算拖拽
DRAG_THRESHOLD_PX = 8

# Ctrl+拖边缩放的热区（像图片缩放那样）
RESIZE_HOTZONE_PX = 16



class NoticePopup(QWidget):
    """A lightweight always-on-top popup used for reminders (water/move)."""
    def __init__(self, parent=None):
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.ToolTip
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        self._label = QLabel(self)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setStyleSheet(
            "QLabel{"
            "color: rgba(255,255,255,235);"
            "background: rgba(0,0,0,170);"
            "border-radius: 10px;"
            "padding: 10px 14px;"
            "}"
        )
        _nf = QFont("Microsoft YaHei UI")
        _nf.setPointSize(10)
        self._label.setFont(_nf)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(18)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 160))
        self._label.setGraphicsEffect(shadow)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)

    def show_text(self, text: str, duration_ms: int, anchor_global_xy: tuple[int,int]):
        self._label.setText(text)
        self._label.adjustSize()
        w = self._label.width()
        h = self._label.height()
        self.resize(w, h)
        self._label.setGeometry(0, 0, w, h)

        ax, ay = anchor_global_xy
        # show above the anchor
        self.move(int(ax - w/2), int(ay - h - 18))
        self.show()
        self.raise_()
        self._hide_timer.start(max(400, int(duration_ms)))


class DesktopPet(QMainWindow, PetActivityBubblesMixin):
    def __init__(self):
        super().__init__()

        # ---------- 0) 资源路径（兼容开发环境和打包exe） ----------
        # 导入main.py中的路径函数
        from config_utils import get_resource_path, get_config_dir
        
        # assets目录：从exe内部读取（只读）
        self.assets_dir = get_resource_path("assets")
        
        # 配置文件目录：用户可写目录
        config_dir = get_config_dir()

        # ---------- 1) 窗口基础设置 ----------
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        # ---------- 2) 状态 ----------
        self.state = "FALL"
        self.prev_state_for_notice = None
        self.prev_vx_for_notice = 0.0
        self.prev_vy_for_notice = 0.0

        # 提示延后队列：忙时不打扰，但也不会漏掉提醒（最多欠 1 条）
        self.notice_queue = deque(maxlen=5)  # 提醒队列，最多保留5条，队列满时自动丢弃最旧的
        # Life reminder visual mode (water/move) - shows a static placeholder block (no text bubble)
        self.life_mode = None               # 'water' | 'move' | None
        self.life_mode_until_ms = 0
        self.life_mode_duration_ms = int(getattr(self, "life_mode_duration_ms", 3000))
        self._notice_popup = NoticePopup()
        self._notice_deferred_timer = QTimer(self)
        self._notice_deferred_timer.setSingleShot(True)
        self._notice_deferred_timer.timeout.connect(self._try_show_queued_notice)

        # Headpat（摸头）短动作：仅站立态长按触发（未来可换 headpat.gif）
        self.headpat_active = False
        self.headpat_duration_ms = 700
        self.headpat_hold_ms = 420
        self._headpat_timer = QTimer(self)
        self._headpat_timer.setSingleShot(True)
        self._headpat_timer.timeout.connect(self._maybe_trigger_headpat)

        # Idle 双动画：idle / idle2 自动交替（如果 idle2.gif 存在）
        self.idle_variant = 1
        self._idle_switch_tick = 0
        self._idle_next_switch = random.randint(180, 300)  # 约 6~10 秒（30fps）
        self._last_idle_variant = 1  # 记录上次播放的变体，避免连续重复

        # 安静模式：暂停 AI + 暂停提示（保留拖拽/缩放/戳/摸头）
        self.quiet_mode = False
        # 用户交互防抖：点击/拖拽/缩放后的短时间内，不触发“前台切换”气泡（避免 sleep 被戳一下就误触发）
        self._suppress_activity_until_ms = 0

        # 活动气泡：仅在前台应用切换时触发（v5）
        self.activity_bubbles_enabled = True
        self.activity_pending = None  # (text, app_name, category)
        self._activity_last_fire = {}  # category -> last_ms
        self._activity_last_sig = None
        self._activity_last_app = None
        self._activity_last_title = None
        self._recent_apps = {}  # exe -> {exe,title,last_seen_ms}
        self._activity_trigger_prob = 0.6
        self._activity_cooldown_by_cat = {
            'chat': 480000, 'video': 480000, 'ai': 360000,
            'code': 480000, 'office': 600000, 'browse': 600000, 'gamehub': 600000
        }
        self._activity_show_ms = 2600

        self._activity_deferred_timer = QTimer(self)
        self._activity_deferred_timer.setSingleShot(True)
        self._activity_deferred_timer.timeout.connect(self._try_show_activity_pending)

        # 读取/生成文案与映射（没有也能跑）
        self.app_map_path = os.path.join(config_dir, 'app_map.json')
        self.bubbles_path = os.path.join(config_dir, 'bubbles.json')

        self.pet_settings_path = os.path.join(config_dir, 'pet_settings.json')
        self._load_pet_settings_file()
        self._load_activity_files()

        # ---------- AI 自动巡视（后台能力） ----------
        self.ai_watch_enabled = False
        self._ai_watch_busy = False
        self._ai_watch_timer = QTimer(self)
        self._ai_watch_timer.timeout.connect(self._ai_watch_tick)
        self._ai_watch_worker = None
        self._thinking = False

        # 加载filters.json用于过滤系统进程
        self.filters_path = os.path.join(config_dir, 'filters.json')
        self._load_filters()

        # HUD（调试小字，可开关；默认关）
        self.hud_enabled = False

        # “戳一下”是一个临时的视觉覆盖（不改物理/不改抛物线）
        self.poke_active = False
        self.poke_kind = None  # None | 'ground' | 'wall' | 'ceiling' | 'sleep'
        self.poke_duration_ms = 650
        self.ceiling_poke_ms = 260
        self.sleep_poke_ms = 420
        self.poke_cooldown_ms = 240
        self._last_poke_ms = 0

        # ---------- 3) 物理参数（不改这套） ----------
        self.vx = 0.0
        self.vy = 0.0
        self.gravity = 0.8
        # walk_speed 已在 _load_pet_settings_file 里从配置读取，不再硬编码
        self.slide_speed = 2.0

        self.pos_x = 500.0
        self.pos_y = 300.0
        self.home_x = self.pos_x

        # 朝向：用于翻转 walk
        self.facing_left = False

        # 生物钟 / 睡眠（以 ms 计时，避免 fps 变化带来的“秒睡”玄学）
        self.tick_ms = 16  # game_loop timer interval
        self.idle_elapsed_ms = 0
        self.run_elapsed_ms = 0

        # sleep 相关配置已在 _load_pet_settings_file 里读取，不再硬编码
        # 这些字段必须初始化，防止第一次读配置前就用到
        self.wake_lock_until_ms = 0
        self.stationary_elapsed_ms = 0
        self._last_sleep_pos = (0.0, 0.0)


        self.is_night = False

        # 天花板
        self.ceiling_time = 100
        self.current_ceiling_tick = 0

        # ---------- 4) 屏幕适配 ----------
        screen = QApplication.primaryScreen()
        if screen is None:
            try:
                from PyQt6.QtGui import QGuiApplication

                screen = QGuiApplication.primaryScreen()
            except Exception:
                screen = None
        if screen is None:
            # 无可用显示器（远程/特殊环境）时用合理默认，避免启动崩溃
            self.screen_rect = QRect(0, 0, 1920, 1080)
            logger.warning("未检测到主屏幕，使用默认几何 1920x1080")
        else:
            self.screen_rect = screen.availableGeometry()

        # 尺寸（可缩放）
        self.pet_size = PET_SIZE_DEFAULT
        self.pet_width = self.pet_size
        self.pet_height = self.pet_size
        self.half_w = self.pet_width // 2
        self.half_h = self.pet_height // 2

        # 地面：保持你现在这套最稳定的计算方式
        self.floor_offset = 0
        self.floor_y = (self.screen_rect.bottom() + 1) + self.floor_offset

        # ---------- 5) UI ----------
        self.label = AnimatedLabel(self)
        self.label.setFixedSize(self.pet_width, self.pet_height)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.resize(self.pet_width, self.pet_height)

        # 鼠标悬停检测 + 缩放提示（不需要你改GIF；按住Ctrl并悬停在桌宠上才出现）
        self._hovering = False
        self.setMouseTracking(True)
        self.label.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

        self.scale_hint = QLabel(self)
        self.scale_hint.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.scale_hint.setGeometry(0, 0, self.pet_width, self.pet_height)
        # 虚线边框 + 角标感（轻提示）
        self.scale_hint.setStyleSheet(
            'background: transparent;'
            'border: 1px dashed rgba(255, 255, 255, 160);'
            'border-radius: 6px;'
        )
        self.scale_hint.hide()

        # 缩放提示的淡出计时（避免一直亮着）
        self._scale_hint_timer = QTimer(self)
        self._scale_hint_timer.setSingleShot(True)
        self._scale_hint_timer.timeout.connect(lambda: self.scale_hint.hide())

        # HUD：调试信息（默认隐藏，按需从菜单/快捷键打开）
        self.hud_label = QLabel(self)
        self.hud_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.hud_label.setStyleSheet(
            "QLabel{color:rgba(255,255,255,230); background-color:rgba(0,0,0,120);"
            "padding:4px 6px; border-radius:6px;}"
        )
        _hf = QFont("Microsoft YaHei UI")
        _hf.setPointSize(9)
        self.hud_label.setFont(_hf)
        self.hud_label.move(10, 10)
        self.hud_label.hide()

        
        # 活动气泡（v5）：使用自绘气泡窗，避免圆角四角黑尖尖
        self.activity_bubble = BubbleWidget(None)
        self.activity_bubble.hide()

        self._activity_bubble_timer = QTimer(self)
        self._activity_bubble_timer.setSingleShot(True)
        self._activity_bubble_timer.timeout.connect(self._hide_activity_bubble)

        # 冒泡期间让气泡跟随桌宠（轻量）
        self._activity_bubble_follow = False
        # ---------- 6) GIF资源 ----------
        self.movie_files = {
            "idle": "idle.gif",
            "idle2": "idle2.gif",  # 可选：发呆换个姿势

            "walk": "walk.gif",
            "drag": "drag.gif",
            "fall": "fall.gif",
            "wall_slide": "wall_slide.gif",
            "ceiling_hang": "ceiling_hang.gif",
            
            # 睡眠动画（白天/夜晚）
            "sleep_day": "sleep_day.gif",
            "sleep_night": "sleep_night.gif",
            
            # 可选：戳一下（地面）
            "poke": "poke.gif",
            # 可选：贴墙戳一下
            "poke_wall": "poke_wall.gif",
            # 可选：天花板戳一下
            "poke_ceiling": "poke_ceiling.gif",
            # 可选：睡觉戳醒
            "poke_sleep": "poke_sleep.gif",
            # 可选：摸头
            "headpat": "headpat.gif",

        
            # 生活提示（可选）：喝水/动一动
            "life_water": "life_water.gif",
            "life_move": "life_move.gif",

}
        self.movies = {}
        for k, fn in self.movie_files.items():
            self._load_movie(k, fn)

        # 当前动画 key
        self.current_anim = None

        # ---------- 7) 输入判定（点击 vs 拖拽；Ctrl+拖边缩放） ----------
        self._pending_click = False
        self._press_global = None
        self._drag_started = False

        self._resizing = False
        self._resize_anchor = None
        self._resize_start_global = None
        self._resize_start_size = None
        self._resize_center = None

        self.last_mouse_pos = None

        # ---------- 8) 启动 ----------
        self.check_real_time()
        self.update_appearance(force=True)

        self.timer = QTimer()
        self.timer.timeout.connect(self.game_loop)
        self.timer.start(16)  # 62.5 FPS，保持流畅

        self.ai_timer = QTimer()
        self.ai_timer.timeout.connect(self.ai_think)
        self.ai_timer.start(max(200, int(getattr(self, 'ai_interval_ms', 2000))))

        self.time_checker = QTimer()
        self.time_checker.timeout.connect(self.check_real_time)
        self.time_checker.start(1000)

        # 前台应用监听：间隔可通过 pet_settings.json 的 activity_poll_ms 配置
        self.activity_timer = QTimer()
        self.activity_timer.timeout.connect(self._poll_foreground_app)
        poll_ms = int(self.pet_settings.get('activity_poll_ms', 300))
        self.activity_timer.start(max(100, poll_ms))

        # 待机闲聊定时器（读取配置，默认10分钟）
        self.idle_chat_timer = QTimer()
        self.idle_chat_timer.timeout.connect(self._trigger_idle_chat)
        idle_chat_min = getattr(self, 'idle_chat_interval_min', 10)
        self.idle_chat_timer.start(idle_chat_min * 60_000)

        # HUD更新定时器（100ms一次，不浪费性能）
        self.hud_update_timer = QTimer()
        self.hud_update_timer.timeout.connect(self._update_hud_if_enabled)
        self.hud_update_timer.start(500)  # 2次/秒 (从10次降到2次，调试够用)

        # 应用记录清理定时器（每30分钟清理一次旧记录，防止内存泄漏）
        self.app_cleanup_timer = QTimer()
        self.app_cleanup_timer.timeout.connect(self._cleanup_old_app_records)
        self.app_cleanup_timer.start(1800000)  # 30分钟

        # ---------- 托盘图标（兜底入口：桌宠飞走也能召回） ----------
        self._create_tray()

        # ---------- 安全热键（低误触：Ctrl+Alt+...） ----------
        # 注意：不做系统级全局劫持；只在本应用内生效。真正兜底靠托盘。
        self._bind_shortcuts()



    def _suppress_activity_briefly(self, suppress_ms: int = 800):
        """Temporarily suppress foreground/app-activity bubbles to avoid flicker during state transitions."""
        now_ms = int(time.time() * 1000)
        self._suppress_activity_until_ms = max(self._suppress_activity_until_ms, now_ms + int(suppress_ms))


    # ---------- 托盘 / 快捷键 ----------
    def _create_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = None
            return
        
        # 使用自定义图标，优先png，其次ico，最后用系统默认
        icon_png = os.path.join(self.assets_dir, "icon.png")
        icon_ico = os.path.join(self.assets_dir, "icon.ico")
        
        if os.path.exists(icon_png):
            icon = QIcon(icon_png)
        elif os.path.exists(icon_ico):
            icon = QIcon(icon_ico)
        else:
            icon = QApplication.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
        
        self.tray = QSystemTrayIcon(icon, self)
        self.tray.setToolTip("Desktop Pet")

        menu = QMenu()

        self.act_quiet = QAction("安静模式", self)
        self.act_quiet.setCheckable(True)
        self.act_quiet.setChecked(self.quiet_mode)
        self.act_quiet.triggered.connect(self.toggle_quiet_mode)
        menu.addAction(self.act_quiet)

        self.act_activity = QAction("活动气泡", self)
        self.act_activity.setCheckable(True)
        self.act_activity.setChecked(self.activity_bubbles_enabled)
        self.act_activity.triggered.connect(self.toggle_activity_bubbles)
        menu.addAction(self.act_activity)

        self.act_hud = QAction("HUD(调试)", self)
        self.act_hud.setCheckable(True)
        self.act_hud.setChecked(self.hud_enabled)
        self.act_hud.triggered.connect(self.toggle_hud)
        act_chat = QAction("桌宠控制台", self)
        act_chat.triggered.connect(self.open_chat_console)
        menu.addAction(act_chat)

        act_settings = QAction("设置...  Settings", self)
        act_settings.triggered.connect(self.open_settings)
        menu.addAction(act_settings)
        
        act_export_log = QAction("📋 导出日志", self)
        act_export_log.triggered.connect(self.export_log)
        menu.addAction(act_export_log)

        menu.addSeparator()
        act_exit = QAction("退出", self)
        act_exit.triggered.connect(QApplication.instance().quit)
        menu.addAction(act_exit)

        self.tray.setContextMenu(menu)
        self.tray.show()

    def _bind_shortcuts(self):
        sc_reset = QShortcut(QKeySequence("Ctrl+Alt+R"), self)
        sc_reset.activated.connect(self.reset_to_home)

        sc_hud = QShortcut(QKeySequence("Ctrl+Alt+H"), self)
        sc_hud.activated.connect(self.toggle_hud)

        sc_quiet = QShortcut(QKeySequence("Ctrl+Alt+Q"), self)
        sc_quiet.activated.connect(self.toggle_quiet_mode)
        
        # 缩放快捷键
        sc_zoom_in = QShortcut(QKeySequence("Ctrl+="), self)
        sc_zoom_in.activated.connect(self.zoom_in)
        
        sc_zoom_out = QShortcut(QKeySequence("Ctrl+-"), self)
        sc_zoom_out.activated.connect(self.zoom_out)
        
        sc_zoom_reset = QShortcut(QKeySequence("Ctrl+0"), self)
        sc_zoom_reset.activated.connect(self.zoom_reset)

    def toggle_hud(self):
        self.hud_enabled = not self.hud_enabled
        if self.hud_enabled:
            self.hud_label.show()
            self._update_hud()
        else:
            self.hud_label.hide()
        if hasattr(self, "act_hud"):
            self.act_hud.setChecked(self.hud_enabled)

    def toggle_quiet_mode(self):
        self.quiet_mode = not self.quiet_mode
        if self.quiet_mode:
            # While quiet, do not show queued notices.
            if hasattr(self, 'notice_queue'):
                self.notice_queue.clear()
            if hasattr(self, '_notice_deferred_timer') and self._notice_deferred_timer.isActive():
                self._notice_deferred_timer.stop()
            if hasattr(self, '_notice_popup'):
                self._notice_popup.hide()
        
        # 同步托盘菜单的复选框
        if hasattr(self, 'act_quiet'):
            self.act_quiet.setChecked(self.quiet_mode)
        
        # 同步设置界面的复选框（用 blockSignals 避免回调循环）
        if hasattr(self, '_settings_dialog') and self._settings_dialog is not None:
            try:
                if hasattr(self._settings_dialog, 'cb_quiet'):
                    w = self._settings_dialog.cb_quiet
                    w.blockSignals(True)
                    w.setChecked(self.quiet_mode)
                    w.blockSignals(False)
            except Exception:
                pass

    def zoom_in(self):
        """放大：Ctrl + =（加号）"""
        self._apply_size(self.pet_size + SIZE_STEP, keep_center=True)
        self._maybe_show_scale_hint()
    
    def zoom_out(self):
        """缩小：Ctrl + -（减号）"""
        self._apply_size(self.pet_size - SIZE_STEP, keep_center=True)
        self._maybe_show_scale_hint()
    
    def zoom_reset(self):
        """恢复默认大小：Ctrl + 0"""
        self._apply_size(PET_SIZE_DEFAULT, keep_center=True)
        self._maybe_show_scale_hint()

    def reset_to_home(self):
        fy = int(self.floor_y - self.pet_height)
        cx = int(self.screen_rect.left() + (self.screen_rect.width() - self.pet_width) / 2)
        cx, fy = self._clamp_to_screen(cx, fy)

        self.pos_x = float(cx)
        self.pos_y = float(fy)
        self.vx = 0.0
        self.vy = 0.0
        self.state = "IDLE"
        self.idle_elapsed_ms = 0
        self.move(cx, fy)
        self.update_appearance(force=True)

    def _update_hud(self):
        if not self.hud_enabled:
            return
        snap = 4
        x = int(self.pos_x)
        y = int(self.pos_y)
        left = self.screen_rect.left()
        top = self.screen_rect.top()
        right_edge = self.screen_rect.right() + 1
        floor_y = self.floor_y

        on_top = abs(y - top) <= snap
        on_left = abs(x - left) <= snap
        on_right = abs((x + self.pet_width) - right_edge) <= snap
        on_floor = abs((y + self.pet_height) - floor_y) <= snap

        edge = []
        if on_floor: edge.append("地")
        if on_left: edge.append("左墙")
        if on_right: edge.append("右墙")
        if on_top: edge.append("顶")
        edge_txt = ",".join(edge) if edge else "-"

        txt = (
            f"state={self.state}\n"
            f"vx={self.vx:.2f} vy={self.vy:.2f}\n"
            f"size={self.pet_size} px\n"
            f"edge={edge_txt}\n"
            f"sleep={int(self.stationary_elapsed_ms/1000)}s / {int(self.sleep_idle_ms/1000)}s"
        )
        self.hud_label.setText(txt)
        self.hud_label.adjustSize()

    def _update_hud_if_enabled(self):
        """由定时器调用，100ms更新一次"""
        if self.hud_enabled:
            self._update_hud()

    # ---------- 右键菜单 ----------
    def contextMenuEvent(self, event):
        menu = QMenu(self)

        act_reset = QAction("复位/召回", self)
        act_reset.triggered.connect(self.reset_to_home)
        menu.addAction(act_reset)

        act_quiet = QAction("安静模式", self)
        act_quiet.setCheckable(True)
        act_quiet.setChecked(self.quiet_mode)
        act_quiet.triggered.connect(self.toggle_quiet_mode)
        menu.addAction(act_quiet)

        act_activity = QAction("活动气泡", self)
        act_activity.setCheckable(True)
        act_activity.setChecked(self.activity_bubbles_enabled)
        act_activity.triggered.connect(self.toggle_activity_bubbles)
        menu.addAction(act_activity)

        act_force_sleep = QAction("强制睡眠", self)
        act_force_sleep.triggered.connect(self.force_sleep)
        menu.addAction(act_force_sleep)

        menu.addSeparator()
        act_chat = QAction("桌宠控制台", self)
        act_chat.triggered.connect(self.open_chat_console)
        menu.addAction(act_chat)

        act_settings = QAction("设置...  Settings", self)
        act_settings.triggered.connect(self.open_settings)
        menu.addAction(act_settings)

        menu.addSeparator()
        act_exit = QAction("退出", self)
        act_exit.triggered.connect(QApplication.instance().quit)
        menu.addAction(act_exit)

        menu.exec(event.globalPos())


    # ---------- 设置面板（v5.1） ----------
    def open_settings(self):
        """打开设置面板（可编辑 app_map / bubbles 并一键重载）。"""
        try:
            from settings_ui import SettingsDialog
        except Exception as e:
            try:
                logger.exception("设置面板导入失败")
            except Exception:
                pass
            self._request_notice(f"设置面板加载失败: {e}")
            return
        if getattr(self, "_settings_dialog", None) is None:
            try:
                self._settings_dialog = SettingsDialog(self)
            except Exception as e:
                try:
                    logger.exception("设置面板创建失败")
                except Exception:
                    pass
                self._request_notice(f"设置面板创建失败: {e}")
                return
        self._settings_dialog.show()
        self._settings_dialog.raise_()
        self._settings_dialog.activateWindow()

    def open_chat_console(self):
        """打开桌宠控制台（聊天窗）"""
        try:
            from chat_console import ChatConsole
        except Exception as e:
            try:
                logger.exception("控制台导入失败")
            except Exception:
                pass
            self._request_notice(f"控制台加载失败: {e}")
            return
        if getattr(self, "_chat_console", None) is None:
            try:
                self._chat_console = ChatConsole(self)
            except Exception as e:
                try:
                    logger.exception("控制台创建失败")
                except Exception:
                    pass
                self._request_notice(f"控制台创建失败: {e}")
                return
        self._chat_console.show()
        self._chat_console.raise_()
        self._chat_console.activateWindow()
    
    def export_log(self):
        """导出日志文件到桌面，方便用户发送给开发者"""
        try:
            import shutil
            from PyQt6.QtWidgets import QMessageBox
            from logger import get_log_dir

            log_dir = get_log_dir()
            
            if not os.path.exists(log_dir):
                QMessageBox.information(self, "无日志", "还没有生成日志文件")
                return
            
            # 获取最新的日志文件
            log_files = [f for f in os.listdir(log_dir) if f.endswith('.log')]
            if not log_files:
                QMessageBox.information(self, "无日志", "还没有生成日志文件")
                return
            
            # 排序找最新的
            log_files.sort(reverse=True)
            latest_log = os.path.join(log_dir, log_files[0])
            
            # 复制到桌面（用系统标准路径，兼容中文/OneDrive 等）
            desktop = QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.DesktopLocation
            )
            if not desktop or not os.path.isdir(desktop):
                desktop = os.path.join(os.path.expanduser("~"), "Desktop")
            if not os.path.isdir(desktop):
                desktop = os.path.expanduser("~")
            
            dest_file = os.path.join(desktop, f"DesktopPet_Log_{log_files[0]}")
            shutil.copy(latest_log, dest_file)
            
            QMessageBox.information(self, "导出成功", 
                f"日志已导出到桌面：\n{dest_file}\n\n"
                "请将这个文件发送给开发者以帮助定位问题")
                
        except Exception as e:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "导出失败", f"日志导出失败：{str(e)}")


    # ---------- AI thinking state ----------
    def set_thinking(self, thinking: bool):
        self._thinking = bool(thinking)

    # ---------- AI 自动巡视（后台） ----------
    def set_ai_watch_enabled(self, enabled: bool):
        """由控制台/设置面板调用：开启/关闭自动巡视。"""
        _set_ai_watch_enabled_impl(self, enabled)

    def _refresh_ai_watch_timer(self):
        refresh_ai_watch_timer(self)

    def _grab_screen_for_ai_watch(self) -> Optional[bytes]:
        """截屏并压缩，降低体积。"""
        return grab_screen_for_ai_watch()

    def _ai_watch_tick(self):
        ai_watch_tick(self)





    def _default_pet_settings(self):
        return {
            "version": 1,
            "behavior": {
                "move_speed": 1.5,
                "ai_interval_ms": 2000,
                "auto_walk_enabled": True,
                "roam_radius_px": 0,
                "edge_margin_px": 0,
            },
            "sleep": {
                "enabled": True,
                "idle_minutes": 20,
                "adrenaline_minutes": 10,
            },
            "reminders": {
                "water_enabled": True,
                "water_interval_min": 60,
                "move_enabled": True,
                "move_interval_min": 90,
                "active_start_h": 9,
                "active_start_m": 0,
                "active_end_h": 23,
                "active_end_m": 30,
                "notice_duration_ms": 3000,
            },
        }

    def _load_pet_settings_file(self):
        # 自动生成默认文件（没有也能跑）
        try:
            self._ensure_json(self.pet_settings_path, self._default_pet_settings())
        except Exception:
            pass
        try:
            with open(self.pet_settings_path, "r", encoding="utf-8") as f:
                self.pet_settings = json.load(f)
        except Exception as e:
            print(f"Error loading pet_settings.json: {e}")
            self.pet_settings = self._default_pet_settings()
        self._apply_pet_settings(first_load=True)

    def reload_pet_settings(self):
        """从磁盘重新读取 pet_settings.json 并应用到桌宠行为/提醒。"""
        logger.info("重新加载pet_settings.json配置...")
        try:
            with open(self.pet_settings_path, "r", encoding="utf-8") as f:
                self.pet_settings = json.load(f)
            logger.info(f"配置加载成功: {self.pet_settings_path}")
        except Exception as e:
            logger.error(f"加载pet_settings.json失败: {e}")
            self.pet_settings = self._default_pet_settings()
        self._apply_pet_settings(first_load=False)

    def force_sleep(self):
        """强制进入睡眠状态（调试用）。"""
        self.vx = 0.0
        self.vy = 0.0
        # 判断当前是白天还是夜晚（19:00-7:00为夜晚）
        from PyQt6.QtCore import QTime
        current_hour = QTime.currentTime().hour()
        self.is_night = (current_hour >= 19 or current_hour < 7)
        self.state = "SLEEP"
        self.sleep_started_at_ms = int(time.time() * 1000)
        self.stationary_elapsed_ms = 0
        self._walk_until_ms = 0
        self.update_appearance(force=True)

    def force_wake(self):
        """强制从睡眠醒来（调试用）。"""
        if self.state == "SLEEP":
            self.state = "IDLE"
            self.vx = 0.0
            self.vy = 0.0
            now_ms = int(time.time() * 1000)
            self.wake_lock_until_ms = now_ms + int(getattr(self, "wake_grace_ms", 45_000))
            self.stationary_elapsed_ms = 0
            self.idle_elapsed_ms = 0
            self._walk_until_ms = 0
            # 醒来时清空提示队列，避免睡眠期间累积的多条提示连续弹出
            self.notice_queue.clear()
            self.update_appearance(force=True)

    def _apply_pet_settings(self, first_load: bool = False):
        ps = self.pet_settings if isinstance(self.pet_settings, dict) else {}
        beh = ps.get("behavior", {}) if isinstance(ps.get("behavior", {}), dict) else {}
        rem = ps.get("reminders", {}) if isinstance(ps.get("reminders", {}), dict) else {}

        # --- behavior ---
        try:
            self.walk_speed = float(beh.get("move_speed", getattr(self, "walk_speed", 1.5)))
        except Exception:
            self.walk_speed = getattr(self, "walk_speed", 1.5)

        try:
            self.ai_interval_ms = int(beh.get("ai_interval_ms", 2000))
        except Exception:
            self.ai_interval_ms = 2000

        self.auto_walk_enabled = bool(beh.get("auto_walk_enabled", True))

        try:
            self.roam_radius_px = int(beh.get("roam_radius_px", 0))
        except Exception:
            self.roam_radius_px = 0

        try:
            self.edge_margin_px = int(beh.get("edge_margin_px", 0))
        except Exception:
            self.edge_margin_px = 0

        # apply ai timer if exists
        try:
            if hasattr(self, "ai_timer") and self.ai_timer is not None:
                self.ai_timer.start(max(200, int(self.ai_interval_ms)))
        except Exception:
            pass

        # --- sleep ---
        slp = ps.get("sleep", {}) if isinstance(ps.get("sleep", {}), dict) else {}

        self.sleep_enabled = bool(slp.get("enabled", True))

        # 直接从json读，读不到才用默认值（不依赖self属性）
        try:
            self.sleep_idle_minutes = max(0, int(slp.get("idle_minutes", 20)))
        except Exception:
            self.sleep_idle_minutes = 20

        try:
            self.adrenaline_minutes = max(0, int(slp.get("adrenaline_minutes", 10)))
        except Exception:
            self.adrenaline_minutes = 10

        # new: wake lock (seconds) + motion thresholds (px)
        try:
            wake_grace_sec = float(slp.get("wake_grace_seconds", 45.0))
            self.wake_grace_ms = max(0, int(wake_grace_sec * 1000))
        except Exception:
            self.wake_grace_ms = 45_000

        try:
            self.sleep_motion_epsilon_px = float(slp.get("motion_epsilon_px", 2.0))
        except Exception:
            self.sleep_motion_epsilon_px = 2.0

        try:
            self.sleep_ground_epsilon_px = float(slp.get("ground_epsilon_px", 2.0))
        except Exception:
            self.sleep_ground_epsilon_px = 2.0

        self.sleep_idle_ms = int(self.sleep_idle_minutes * 60_000)
        self.adrenaline_ms = int(self.adrenaline_minutes * 60_000)

# --- reminders ---
        self.water_enabled = bool(rem.get("water_enabled", True))
        try:
            self.water_interval_min = max(1, int(rem.get("water_interval_min", 60)))
        except Exception:
            self.water_interval_min = 60

        self.move_enabled = bool(rem.get("move_enabled", True))
        try:
            self.move_interval_min = max(1, int(rem.get("move_interval_min", 90)))
        except Exception:
            self.move_interval_min = 90

        try:
            self.rem_start_h = int(rem.get("active_start_h", 9))
            self.rem_start_m = int(rem.get("active_start_m", 0))
            self.rem_end_h = int(rem.get("active_end_h", 23))
            self.rem_end_m = int(rem.get("active_end_m", 30))
        except Exception:
            self.rem_start_h, self.rem_start_m, self.rem_end_h, self.rem_end_m = 9, 0, 23, 30

        try:
            self.notice_duration_ms = max(500, int(rem.get("notice_duration_ms", 3000)))
        except Exception:
            self.notice_duration_ms = 3000

        # --- idle chat ---
        try:
            self.idle_chat_interval_min = max(1, int(rem.get("idle_chat_interval_min", 10)))
        except Exception:
            self.idle_chat_interval_min = 10
        
        # apply idle chat timer if exists
        try:
            if hasattr(self, "idle_chat_timer") and self.idle_chat_timer is not None:
                self.idle_chat_timer.start(self.idle_chat_interval_min * 60_000)
        except Exception:
            pass

        # --- auto fall ---
        try:
            self.auto_fall_enabled = bool(beh.get("auto_fall_enabled", True))
        except Exception:
            self.auto_fall_enabled = True

        # initialize last reminder timestamps to "now" on first load, so it doesn't spam immediately
        now_ms = int(time.time() * 1000)
        if first_load or not hasattr(self, "_last_water_notice_ms"):
            self._last_water_notice_ms = now_ms
        if first_load or not hasattr(self, "_last_move_notice_ms"):
            self._last_move_notice_ms = now_ms
        
        # 记录关键配置值
        logger.info("=" * 50)
        logger.info("配置应用成功！详细参数如下：")
        logger.info(f"  [行为] 移动速度: {self.walk_speed}, AI间隔: {self.ai_interval_ms}ms, 自动行走: {self.auto_walk_enabled}")
        logger.info(f"  [睡眠] 启用: {self.sleep_enabled}, 发呆{self.sleep_idle_minutes}分钟后睡, 兴奋期{self.adrenaline_minutes}分钟")
        logger.info(f"  [闲聊] 间隔: {self.idle_chat_interval_min}分钟")
        logger.info(f"  [提醒] 喝水: {self.water_enabled}({self.water_interval_min}分钟), 运动: {self.move_enabled}({self.move_interval_min}分钟)")
        logger.info("=" * 50)

    def get_recent_apps(self, limit: int = 30):
        """给设置面板用：返回最近前台出现过的应用列表。"""
        ra = getattr(self, "_recent_apps", {})
        if not isinstance(ra, dict):
            return []
        items = list(ra.values())
        items.sort(key=lambda x: x.get("last_seen_ms", 0), reverse=True)
        return items[:max(1, int(limit))]


    # ---------- 资源 ----------
    def _load_movie(self, key: str, filename: str):
        self.movies[key] = load_movie_for_key(
            self.assets_dir, key, filename, self.pet_width, self.pet_height
        )

    def _rebuild_movies_for_size(self):
        """缩放后：重建 QMovie，避免“判定框变了但 GIF 视觉没变”的缓存问题。"""
        rebuild_pet_movies(self)

    # ---------- 缩放 ----------

    def _apply_size(self, new_size: int, keep_center: bool = False):
        """缩放（v3）：
        - GIF 视觉尺寸跟随（重建 QMovie）
        - 根据“当前贴边情况”决定锚点：地/墙/顶/空中
        """
        new_size = int(max(MIN_SIZE, min(MAX_SIZE, new_size)))
        if new_size == self.pet_size:
            return

        snap = 4  # 吸附阈值（像素）

        old_w = self.pet_width
        old_h = self.pet_height
        old_x = int(self.pos_x)
        old_y = int(self.pos_y)

        # 当前接触边（用旧尺寸判断）
        left = self.screen_rect.left()
        top = self.screen_rect.top()
        right_edge = self.screen_rect.right() + 1
        floor_y = self.floor_y

        on_left = abs(old_x - left) <= snap
        on_right = abs((old_x + old_w) - right_edge) <= snap
        on_top = abs(old_y - top) <= snap
        on_floor = abs((old_y + old_h) - floor_y) <= snap

        # 默认按中心缩放
        cx = old_x + old_w / 2.0
        cy = old_y + old_h / 2.0

        # 更新尺寸
        self.pet_size = new_size
        self.pet_width = new_size
        self.pet_height = new_size
        self.half_w = new_size // 2
        self.half_h = new_size // 2

        self.label.setFixedSize(self.pet_width, self.pet_height)
        self.resize(self.pet_width, self.pet_height)
        if hasattr(self, 'scale_hint'):
            self.scale_hint.setGeometry(0, 0, self.pet_width, self.pet_height)

        # 关键：重建 movies，确保 GIF 视觉尺寸也同步缩放
        self._rebuild_movies_for_size()

        # 计算新位置：按状态与贴边情况选择锚点
        nx = int(cx - self.pet_width / 2.0)
        ny = int(cy - self.pet_height / 2.0)

        if not keep_center:
            if self.state in ["IDLE", "WALK", "SLEEP", "NOTICE"] and on_floor:
                # 站在地上：脚贴地
                ny = int(floor_y - self.pet_height)
            elif self.state == "WALL_SLIDE" and (on_left or on_right):
                # 扒墙：贴墙侧锁死
                if on_left:
                    nx = int(left)
                else:
                    nx = int(right_edge - self.pet_width)
            elif self.state == "CEILING_HANG" and on_top:
                # 挂顶：顶边锁死
                ny = int(top)

        # 轻微吸附，消灭 1px 白边
        if abs(nx - left) <= snap:
            nx = int(left)
        if abs((nx + self.pet_width) - right_edge) <= snap:
            nx = int(right_edge - self.pet_width)
        if abs(ny - top) <= snap:
            ny = int(top)
        if abs((ny + self.pet_height) - floor_y) <= snap:
            ny = int(floor_y - self.pet_height)

        nx, ny = self._clamp_to_screen(nx, ny)
        self.move(nx, ny)
        self.pos_x = float(nx)
        self.pos_y = float(ny)

        # 强制刷新当前显示
        self.update_appearance(force=True)
    def enterEvent(self, event):
        self._hovering = True
        self._maybe_show_scale_hint()
        self._update_hud()
        return super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovering = False
        if hasattr(self, 'scale_hint'):
            self.scale_hint.hide()
        return super().leaveEvent(event)

    def _ctrl_down(self) -> bool:
        return bool(QApplication.keyboardModifiers() & Qt.KeyboardModifier.ControlModifier)

    def _maybe_show_scale_hint(self):
        # 仅在按住Ctrl且鼠标在桌宠上时给提示；拖拽/戳一下时不提示
        if not hasattr(self, 'scale_hint'):
            return
        if self.state == 'DRAG' or self._resizing or self.poke_active:
            self.scale_hint.hide()
            return
        if self._hovering and self._ctrl_down():
            self.scale_hint.show()
            # 0.9s 自动淡出，避免一直亮
            self._scale_hint_timer.start(900)
        else:
            self.scale_hint.hide()

    def wheelEvent(self, event):
        self._mark_user_interaction()
        # Ctrl + 滚轮缩放（仅鼠标悬停在桌宠上时生效；避免误触桌面图标缩放）
        if (event.modifiers() & Qt.KeyboardModifier.ControlModifier) and self._hovering:
            self._maybe_show_scale_hint()
            delta = event.angleDelta().y()
            if delta > 0:
                self._apply_size(self.pet_size + SIZE_STEP, keep_center=False)
            elif delta < 0:
                self._apply_size(self.pet_size - SIZE_STEP, keep_center=False)
            event.accept()
            return
        super().wheelEvent(event)

    # ---------- 时间 / 提示 ----------

    def check_real_time(self):
        now = QTime.currentTime()
        hour = now.hour()
        minute = now.minute()
        second = now.second()

        # 只用于「睡觉外观」：夜=19:00~次日07:00
        if not hasattr(self, "sleep_theme"):
            self.sleep_theme = ("night" if (hour >= 19 or hour < 7) else "day")

        # 两个时间点触发睡觉外观切换：07:00 与 19:00
        if minute == 0 and second == 0:
            if hour == 7:
                self.sleep_theme = "day"
                self.is_night = False
                if self.state == "SLEEP":
                    self.update_appearance(force=True)
            elif hour == 19:
                self.sleep_theme = "night"
                self.is_night = True
                if self.state == "SLEEP":
                    self.update_appearance(force=True)

        # 喝水/运动提示：按“间隔”触发；稳定态才“显示”，忙时进入延后队列
        if self.state in ["IDLE", "WALK", "SLEEP"]:
            self._check_interval_reminders(hour, minute)

    
    def _is_within_reminder_window(self, hour: int, minute: int) -> bool:
        sh, sm = int(getattr(self, "rem_start_h", 9)), int(getattr(self, "rem_start_m", 0))
        eh, em = int(getattr(self, "rem_end_h", 23)), int(getattr(self, "rem_end_m", 30))
        cur = hour * 60 + minute
        start = sh * 60 + sm
        end = eh * 60 + em
        if start == end:
            return True  # treat as always
        if start < end:
            return start <= cur <= end
        # wrap midnight
        return cur >= start or cur <= end

    def _check_interval_reminders(self, hour: int, minute: int):
        if self.quiet_mode:
            return
        if not self._is_within_reminder_window(hour, minute):
            return

        now_ms = int(time.time() * 1000)
        # water
        if bool(getattr(self, "water_enabled", True)):
            interval_ms = max(1, int(getattr(self, "water_interval_min", 60))) * 60 * 1000
            if now_ms - int(getattr(self, "_last_water_notice_ms", 0)) >= interval_ms:
                self._last_water_notice_ms = now_ms
                self._request_life("water")
        # move
        if bool(getattr(self, "move_enabled", True)):
            interval_ms = max(1, int(getattr(self, "move_interval_min", 90))) * 60 * 1000
            if now_ms - int(getattr(self, "_last_move_notice_ms", 0)) >= interval_ms:
                self._last_move_notice_ms = now_ms
                self._request_life("move")

    def _busy_for_notice(self) -> bool:
        if self._resizing or self.poke_active or self.headpat_active:
            return True
        if self.state in ["DRAG", "NOTICE", "FALL", "WALL_SLIDE", "CEILING_HANG"]:
            return True
        return False

    def _request_life(self, kind: str):
        if self.quiet_mode:
            return

        # Sleep: never interrupt; defer until wake (life reminder is visual)
        if self.state == "SLEEP":
            try:
                self.notice_queue.append(f"__LIFE__:{kind}")
            except Exception:
                pass
            return

        # 如果当前正在显示另一个life提示，排队等结束再显示
        if getattr(self, "life_mode", None):
            try:
                self.notice_queue.append(f"__LIFE__:{kind}")
            except Exception:
                pass
            return

        if self._busy_for_notice():
            try:
                self.notice_queue.append(f"__LIFE__:{kind}")
            except Exception:
                pass
            return

        self._start_life_mode(kind)

    def _request_notice(self, text: str):
        if self.quiet_mode:
            return

        # Deduplicate: if same as last queued, don't spam
        try:
            if len(self.notice_queue) and self.notice_queue[-1] == text:
                return
        except Exception:
            pass

        # Sleep: never interrupt; queue it for later
        if self.state == "SLEEP":
            try:
                self.notice_queue.append(text)
            except Exception:
                pass
            return

        # If we're in a stable state and not busy, show immediately
        if (self.state in ["IDLE", "WALK"]) and (not self._busy_for_notice()):
            self._show_notice_overlay(text, int(getattr(self, 'notice_duration_ms', 3000)))
            return

        # Otherwise, queue and retry later
        try:
            self.notice_queue.append(text)
        except Exception:
            pass
        if not self._notice_deferred_timer.isActive():
            self._notice_deferred_timer.start(random.randint(3000, 5000))


    def _try_show_queued_notice(self):
        if self.quiet_mode:
            try:
                self.notice_queue.clear()
            except Exception:
                pass
            return

        try:
            if not self.notice_queue:
                return
        except Exception:
            return

        if self.state == "SLEEP":
            # keep queued until wake
            return

        if (self.state in ["IDLE", "WALK"]) and (not self._busy_for_notice()):
            try:
                txt = self.notice_queue.popleft()
            except Exception:
                return
            if isinstance(txt, str) and txt.startswith("__LIFE__:"):
                kind = txt.split(":", 1)[1].strip() if ":" in txt else "water"
                self._start_life_mode(kind)
                return
            self._show_notice_overlay(txt, int(getattr(self, 'notice_duration_ms', 3000)))
            return

        self._notice_deferred_timer.start(1000)


    def _show_notice_overlay(self, text: str, duration_ms: int):
        """Show a visible reminder popup near the pet without changing pet physics/state."""
        try:
            # Anchor: above the pet center
            g = self.mapToGlobal(self.rect().center())
            anchor = (int(g.x()), int(g.y()))
        except Exception:
            try:
                anchor = (int(self.x() + self.width() / 2), int(self.y()))
            except Exception:
                anchor = (0, 0)

        try:
            if not hasattr(self, "_notice_popup") or self._notice_popup is None:
                self._notice_popup = NoticePopup()
            self._notice_popup.show_text(text, int(duration_ms), anchor)
        except Exception:
            pass


    # ---------- Life reminder visual mode (no text bubble) ----------
    def _start_life_mode(self, kind: str):
        # kind: 'water' or 'move'
        # 纯视觉覆盖，不干预物理状态
        try:
            now_ms = int(time.time() * 1000)
        except Exception:
            now_ms = 0

        self.life_mode = kind
        self.life_mode_until_ms = now_ms + int(self.life_mode_duration_ms)

        # Suppress activity bubbles for a short moment to avoid flicker / overlap
        self._suppress_activity_briefly(suppress_ms=800)
        self.update_appearance(force=True)

        QTimer.singleShot(int(self.life_mode_duration_ms), self._end_life_mode)

    def _end_life_mode(self):
        self.life_mode = None
        self.life_mode_until_ms = 0
        self.vx = 0.0
        self._walk_until_ms = 0
        self.update_appearance(force=True)
        # 有排队的提醒就让它出来（延迟2秒，避免连续life提醒刷屏）
        if not self._notice_deferred_timer.isActive():
            self._notice_deferred_timer.start(2000)

    
    def _render_life_placeholder(self):
        """Life reminder visual:
        - Prefer GIF slot if assets exist (life_water.gif / life_move.gif)
        - Fallback to a neutral block placeholder (reuses the same safe placeholder logic as other states)
        """
        kind = getattr(self, "life_mode", None)
        key = None
        if kind == "water":
            key = "life_water"
        elif kind == "move":
            key = "life_move"

        # Prefer GIF slot if available
        if key:
            mv = self.movies.get(key)
            if mv is not None:
                # Life mode should not flip
                self.label.play_movie(key, mv, False)
                return

        # Fallback placeholder block (safe PyQt6 enums)
        txt = "喝水" if kind == "water" else ("动一动" if kind == "move" else "提示")
        pm = self._fallback_pixmap(txt)
        self.label.stop_movie()
        self.label.setPixmap(pm)

    # ---------- 摸头（Headpat） ----------
    def _maybe_trigger_headpat(self):
        if not getattr(self, "_pending_click", False):
            return
        if getattr(self, "_drag_started", False):
            return
        if self.state not in ["IDLE", "WALK"]:
            return
        if self.poke_active or self.headpat_active or self._resizing:
            return

        self._pending_click = False
        self.vx = 0.0
        self.vy = 0.0
        self.state = "IDLE"

        self.headpat_active = True
        self.update_appearance(force=True)
        QTimer.singleShot(self.headpat_duration_ms, self._end_headpat)

    def _end_headpat(self):
        self.headpat_active = False
        self.vx = 0.0
        self.state = "IDLE"
        self._walk_until_ms = 0
        self.update_appearance(force=True)

    # ---------- 戳一下 ----------

    def _trigger_poke(self):
        """按状态分支触发 poke（v3 规则）：
        - FALL：忽略
        - SLEEP：sleep_poke -> IDLE 连锁
        - CEILING_HANG：ceiling_poke -> FALL 连锁
        - WALL_SLIDE：不停位移，只显示 wall_poke
        - IDLE/WALK：停住 + ground_poke -> IDLE
        """
        # 下落：完全不触发
        if self.state == "FALL":
            return

        # 冷却（防连点）
        now_ms = int(time.time() * 1000)
        if now_ms - getattr(self, "_last_poke_ms", 0) < getattr(self, "poke_cooldown_ms", 200):
            return

        # 已经在 poke：不重复
        if self.poke_active:
            return

        self._last_poke_ms = now_ms

        # 1) 睡觉：poke -> 起床
        if self.state == "SLEEP":
            self.poke_active = True
            self.poke_kind = "sleep"
            self.update_appearance(force=True)

            def _wake():
                self.poke_active = False
                self.poke_kind = None
                self.state = "IDLE"
                self.vx = 0.0
                self.vy = 0.0
                # prevent immediate re-sleep after wake (poke itself has no displacement)
                now2 = int(time.time() * 1000)
                self.wake_lock_until_ms = now2 + int(getattr(self, "wake_grace_ms", 45_000))
                self.stationary_elapsed_ms = 0
                self.idle_elapsed_ms = 0
                # 醒来时清空提示队列，避免睡眠期间累积的多条提示连续弹出
                self.notice_queue.clear()
                self.update_appearance(force=True)

            QTimer.singleShot(self.sleep_poke_ms, _wake)
            return

        # 2) 天花板：poke -> 掉落
        if self.state == "CEILING_HANG":
            self.poke_active = True
            self.poke_kind = "ceiling"
            self.update_appearance(force=True)

            def _drop():
                self.poke_active = False
                self.poke_kind = None
                self.state = "FALL"
                if self.vy <= 0:
                    self.vy = 2.0
                self.update_appearance(force=True)

            QTimer.singleShot(self.ceiling_poke_ms, _drop)
            return

        # 3) 扒墙：不停位移，只显示
        if self.state == "WALL_SLIDE":
            self.poke_active = True
            self.poke_kind = "wall"
            self.update_appearance(force=True)
            QTimer.singleShot(self.poke_duration_ms, self._end_poke)
            return

        # 4) 地面（含 WALK）：停住并回到 IDLE
        if self.state in ["IDLE", "WALK", "NOTICE"]:
            self.vx = 0.0
            self.state = "IDLE"
            self.poke_active = True
            self.poke_kind = "ground"
            self.update_appearance(force=True)
            QTimer.singleShot(self.poke_duration_ms, self._end_poke)
            return

        # 其它状态：默认也当作地面 poke（不改物理）
        self.poke_active = True
        self.poke_kind = "ground"
        self.update_appearance(force=True)
        QTimer.singleShot(self.poke_duration_ms, self._end_poke)
    def _end_poke(self):
        self.poke_active = False
        self.poke_kind = None
        self.update_appearance(force=True)

    # ---------- 外观 ----------
    def _fallback_pixmap(self, text: str):
        pm = QPixmap(self.pet_width, self.pet_height)
        pm.fill(Qt.GlobalColor.transparent)

        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        p.setBrush(QColor(80, 80, 80, 200))
        p.setPen(Qt.PenStyle.NoPen)
        radius = max(14, int(self.pet_width * 0.12))
        p.drawRoundedRect(0, 0, self.pet_width, self.pet_height, radius, radius)

        p.setPen(QColor(255, 255, 255, 240))
        _psz = max(10, int(self.pet_height * 0.08))
        _ff = QFont("Microsoft YaHei")
        _ff.setPointSize(_psz)
        _ff.setWeight(QFont.Weight.Bold)
        p.setFont(_ff)
        p.drawText(0, 0, self.pet_width, self.pet_height, Qt.AlignmentFlag.AlignCenter, text)

        p.end()
        return pm

    def update_appearance(self, force: bool = False):
        """
        - 只有 IDLE/WALK/DRAG 用 GIF（保持你的稳定策略）
        - POKE 作为“短时视觉覆盖”，优先显示（不改物理）
        - 其他状态全部用“方框+文字”
        """
        # walk 朝向
        if self.state == "WALK":
            self.facing_left = (self.vx < 0)

        # 0) HEADPAT 覆盖（短动作；不改物理）
        if self.headpat_active and self.state != "DRAG":
            mv = self.movies.get("headpat")
            if mv is not None:
                if force or self.current_anim != "headpat":
                    self.label.play_movie("headpat", mv, False)
                    self.current_anim = "headpat"
                return
            self.label.stop_movie()
            self.current_anim = None
            self.label.setPixmap(self._fallback_pixmap("摸摸"))
            return

        # 0) POKE 覆盖（但拖拽时不打断 drag.gif）
        if self.poke_active and self.state != "DRAG":
            # 根据 poke_kind 选择不同资源（可选）
            poke_key = {
                "ground": "poke",
                "wall": "poke_wall",
                "ceiling": "poke_ceiling",
                "sleep": "poke_sleep",
            }.get(self.poke_kind or "ground", "poke")

            # poke_wall 和 poke_ceiling 需要根据位置翻转
            poke_flip = False
            if poke_key == "poke_wall":
                left = self.screen_rect.left()
                right_edge = self.screen_rect.right() + 1
                x = int(self.pos_x)
                on_left = abs(x - left) <= 4
                on_right = abs((x + self.pet_width) - right_edge) <= 4
                # 左墙面向右（不翻转），右墙面向左（翻转）
                poke_flip = on_right
            elif poke_key == "poke_ceiling":
                # poke_ceiling直接使用ceiling_hang保存的朝向，确保一致
                poke_flip = getattr(self, 'ceiling_flip', False)

            mv = self.movies.get(poke_key)
            if mv is not None:
                if force or self.current_anim != poke_key:
                    self.label.play_movie(poke_key, mv, poke_flip)
                    self.current_anim = poke_key
                return

            # 没有对应 poke.gif 就用方块（区分一下更直观）
            self.label.stop_movie()
            self.current_anim = None
            fallback_text = {
                "ground": "叮",
                "wall": "别戳",
                "ceiling": "啊!",
                "sleep": "……",
            }.get(self.poke_kind or "ground", "叮")
            self.label.setPixmap(self._fallback_pixmap(fallback_text))
            return

        # Life reminder visual placeholder (no bubble)
        # 优先级低于 headpat/poke，且不打断 DRAG 的物理感
        if getattr(self, "life_mode", None) and self.state != "DRAG":
            try:
                now_ms = int(time.time() * 1000)
            except Exception:
                now_ms = 0
            if now_ms < getattr(self, "life_mode_until_ms", 0):
                self._render_life_placeholder()
                self.current_anim = None
                return
            else:
                self.life_mode = None
                self.life_mode_until_ms = 0


        # 1) GIF状态（扩展支持：IDLE/WALK/DRAG/FALL/WALL_SLIDE/CEILING_HANG/SLEEP）
        gif_states = ["IDLE", "WALK", "DRAG", "FALL", "WALL_SLIDE", "CEILING_HANG", "SLEEP"]
        
        if self.state in gif_states:
            # 确定GIF key
            key = None
            flip = False
            
            if self.state == "IDLE":
                if self.idle_variant == 2 and self.movies.get("idle2") is not None:
                    key = "idle2"
                else:
                    key = "idle"
            elif self.state == "WALK":
                key = "walk"
                flip = self.facing_left
            elif self.state == "DRAG":
                key = "drag"
                flip = False
            elif self.state == "FALL":
                key = "fall"
                # 下落时保持最后的朝向
                flip = self.facing_left
            elif self.state == "WALL_SLIDE":
                key = "wall_slide"
                # 根据位置判断是左墙还是右墙
                left = self.screen_rect.left()
                right_edge = self.screen_rect.right() + 1
                x = int(self.pos_x)
                on_left = abs(x - left) <= 4
                on_right = abs((x + self.pet_width) - right_edge) <= 4
                # 左墙面向右，右墙面向左
                flip = on_right
            elif self.state == "CEILING_HANG":
                key = "ceiling_hang"
                # 根据位置判断是左边还是右边挂着
                left = self.screen_rect.left()
                right_edge = self.screen_rect.right() + 1
                x = int(self.pos_x)
                center_x = x + self.pet_width / 2
                screen_center_x = (left + right_edge) / 2
                # 左半边挂着面向右，右半边挂着面向左
                flip = (center_x > screen_center_x)
                # 保存状态供poke_ceiling使用
                self.ceiling_flip = flip
            elif self.state == "SLEEP":
                # 根据is_night选择白天/夜晚睡眠动画
                if self.is_night and self.movies.get("sleep_night") is not None:
                    key = "sleep_night"
                elif self.movies.get("sleep_day") is not None:
                    key = "sleep_day"
                else:
                    # 兼容只有一个sleep.gif的情况
                    key = "sleep"
                flip = False

            mv = self.movies.get(key)

            if mv is None:
                # GIF缺失，显示占位符
                self.label.stop_movie()
                self.label.setPixmap(self._fallback_pixmap(f"{key}.gif缺失"))
                self.current_anim = None
                return

            if force or self.current_anim != key:
                self.label.play_movie(key, mv, flip)
                self.current_anim = key
            else:
                self.label.set_flip(flip)
                self.label._render_frame()
            return

        # 2) 其他状态：方框+文字（兜底）
        self.label.stop_movie()
        self.current_anim = None

        txt = self.state
        self.label.setPixmap(self._fallback_pixmap(txt))

    # ---------- AI ----------

    def ai_think(self):
        # 安静模式只禁止气泡和提醒，不禁止移动！
        # （quiet_mode的检查在气泡触发和提醒触发的地方）
        
        if not bool(getattr(self, 'auto_walk_enabled', True)):
            # auto-walk disabled: stay idle
            if self.state == 'WALK':
                self.state = 'IDLE'
                self.vx = 0
            return
        # Life reminder mode: freeze movement until it ends (placeholder should stay still).
        if self.life_mode is not None:
            self.state = "IDLE"
            self.vx = 0
            return

        # 只有空闲时才跑 AI；任何交互/戳一下/提示都让位
        if self.poke_active or self._resizing or self.state in ["DRAG", "NOTICE", "SLEEP", "FALL", "WALL_SLIDE", "CEILING_HANG"]:
            return
        if self.state not in ["IDLE", "WALK"]:
            return

        # 发呆够久且兴奋期过了：即将进入睡眠，减少随机移动
        # 使用98%阈值，避免过早"僵住"（20分钟睡眠只会在最后24秒僵住）
        if (self.sleep_enabled and self.run_elapsed_ms > self.adrenaline_ms
                and self.stationary_elapsed_ms >= max(5_000, int(self.sleep_idle_ms * 0.98))):
            return

        now_ms = int(time.time() * 1000)

        # 正在走路且还没走够：不打断，让它走完
        if self.state == "WALK":
            walk_until = getattr(self, "_walk_until_ms", 0)
            if now_ms < walk_until:
                return

        # 重新做决策
        # 权重：IDLE 25%，WALK 75%（走路比例更高，减少碎步感）
        choice = random.choices(
            ["IDLE", "WALK_LEFT", "WALK_RIGHT"],
            weights=[25, 37, 38],
            k=1
        )[0]

        if choice == "IDLE":
            self.state = "IDLE"
            self.vx = 0.0
            # idle也持续4-8秒，避免频繁重新决策造成视觉闪现
            self._walk_until_ms = now_ms + random.randint(4000, 8000)
        elif choice == "WALK_LEFT":
            self.state = "WALK"
            self.vx = -self.walk_speed
            # 每次走路持续 6~15 秒再重新决策
            self._walk_until_ms = now_ms + random.randint(6000, 15000)
        else:
            self.state = "WALK"
            self.vx = self.walk_speed
            self._walk_until_ms = now_ms + random.randint(6000, 15000)

        self.update_appearance(force=True)
    # ---------- clamp ----------
    def _clamp_to_screen(self, x: int, y: int):
        m = int(getattr(self, 'edge_margin_px', 0) or 0)
        left = self.screen_rect.left() + m
        top = self.screen_rect.top() + m
        right = self.screen_rect.right() + 1 - self.pet_width - m
        bottom = int(self.floor_y - self.pet_height - m)
        return max(left, min(x, int(right))), max(top, min(y, int(bottom)))

    # ---------- 主循环（不动物理） ----------
    def _mark_user_interaction(self, suppress_ms: int = 900):
        try:
            now_ms = int(time.time() * 1000)
        except Exception:
            now_ms = 0
        self._suppress_activity_until_ms = max(getattr(self, '_suppress_activity_until_ms', 0), now_ms + int(suppress_ms))

    def game_loop(self):
        # 缩放提示：每帧轻量检查一次（不影响物理）
        self._maybe_show_scale_hint()

        # 活动气泡跟随：每帧更新位置（顶层窗不会被裁剪）
        if self._activity_bubble_follow and self.activity_bubble.isVisible():
            self._position_activity_bubble()

        if self.state == "DRAG":
            return

        # headpat 和 life_mode 期间锁住水平位移
        if getattr(self, "headpat_active", False) or getattr(self, "life_mode", None):
            self.vx = 0.0

        self.run_elapsed_ms += self.tick_ms

        # 计算当前是否在地面
        snap = 4  # 稍微放宽，避免walk时pos_y浮点误差导致stationary计时悄悄暂停
        grounded = abs((self.pos_y + self.pet_height) - self.floor_y) <= snap
        dt_ms = self.tick_ms
        resizing = self._resizing
        now_ms = int(time.time() * 1000)

        # sleep: rest-time tracking (NOT movement-based)
        # We want sleep to still happen even if auto-walk keeps the pet moving.
        # Count "rest time" while on ground in IDLE/WALK, not being dragged, and not in life-mode.
        # Life-mode pauses the counter but does not reset it.
        if (grounded and (self.state in ("IDLE", "WALK")) and (self.life_mode is None) and (not resizing)):
            self.stationary_elapsed_ms += dt_ms
        else:
            # Reset only on meaningful activity / leaving ground / interactions.
            if (not grounded) or (self.state in ("DRAG", "FALL", "POKE", "HEADPAT")):
                self.stationary_elapsed_ms = 0
            # else: pause (e.g., life_mode active)

        if self.state == "IDLE":
            self.idle_elapsed_ms += self.tick_ms

            # idle / idle2 交替（不打断交互/戳/摸头/缩放）
            if (not self.poke_active) and (not self.headpat_active) and (not self._resizing):
                if self.movies.get("idle2") is not None:
                    self._idle_switch_tick += 1
                    if self._idle_switch_tick >= self._idle_next_switch:
                        # 避免连续重复：如果上次是1就切到2，上次是2就切到1
                        if self._last_idle_variant == 1:
                            self.idle_variant = 2
                            # 根据idle2的帧数调整播放时长
                            mv = self.movies.get("idle2")
                            if mv:
                                frame_count = mv.frameCount()
                                # 估算播放时长：帧数少的短GIF不重复，帧数多的长GIF多播一会
                                if frame_count < 60:  # 约2秒（30fps）
                                    self._idle_next_switch = frame_count + 30  # 播放一次+1秒
                                elif frame_count < 150:  # 约5秒
                                    self._idle_next_switch = frame_count + 60  # 播放一次+2秒
                                else:
                                    self._idle_next_switch = random.randint(180, 300)  # 长GIF随机
                            else:
                                self._idle_next_switch = random.randint(180, 300)
                        else:
                            self.idle_variant = 1
                            # 根据idle的帧数调整播放时长
                            mv = self.movies.get("idle")
                            if mv:
                                frame_count = mv.frameCount()
                                if frame_count < 60:
                                    self._idle_next_switch = frame_count + 30
                                elif frame_count < 150:
                                    self._idle_next_switch = frame_count + 60
                                else:
                                    self._idle_next_switch = random.randint(180, 300)
                            else:
                                self._idle_next_switch = random.randint(180, 300)
                        
                        self._last_idle_variant = self.idle_variant
                        self._idle_switch_tick = 0
                        self.update_appearance(force=True)

        else:
            self.idle_elapsed_ms = 0

        # Sleep check: allow sleep even if auto-walk is enabled (state may not be strictly "IDLE").
        if self.sleep_enabled and (self.run_elapsed_ms > self.adrenaline_ms):
            wake_lock_until_ms = getattr(self, "wake_lock_until_ms", 0)
            if now_ms >= wake_lock_until_ms:
                # Only consider sleep when we are effectively stationary on the ground, not dragging, and not showing life reminder.
                if (grounded and (self.state != "DRAG") and (self.life_mode is None)):
                    if self.stationary_elapsed_ms >= self.sleep_idle_ms:
                        # Avoid entering sleep from transient / forced-motion states.
                        if self.state not in ["FALL", "POKE", "DRAG", "EDGE", "WALL", "CEILING"]:
                            # 判断当前是白天还是夜晚（19:00-7:00为夜晚）
                            from PyQt6.QtCore import QTime
                            current_hour = QTime.currentTime().hour()
                            self.is_night = (current_hour >= 19 or current_hour < 7)
                            self.state = "SLEEP"
                            self.sleep_started_at_ms = now_ms
                            self.update_appearance(force=True)
                            return

        if self.state in ["SLEEP", "NOTICE"]:
            return

        next_x = self.pos_x + self.vx
        next_y = self.pos_y + self.vy

        # roam radius: keep near home_x if configured
        rr = int(getattr(self, "roam_radius_px", 0) or 0)
        if rr > 0 and self.state == "WALK":
            left_bound = float(getattr(self, "home_x", self.pos_x)) - rr
            right_bound = float(getattr(self, "home_x", self.pos_x)) + rr
            if next_x < left_bound:
                next_x = left_bound
                self.vx = abs(self.vx) if self.vx != 0 else abs(getattr(self, "walk_speed", 1.5))
            elif next_x > right_bound:
                next_x = right_bound
                self.vx = -abs(self.vx) if self.vx != 0 else -abs(getattr(self, "walk_speed", 1.5))

        # 1) 天花板挂住
        if self.state == "CEILING_HANG":
            self.vx = 0.0
            self.vy = 0.0
            self.current_ceiling_tick += 1
            # 自动掉落开关：关闭时不会自动掉下来
            if getattr(self, 'auto_fall_enabled', True) and self.current_ceiling_tick > self.ceiling_time:
                self.state = "FALL"
                self.current_ceiling_tick = 0
                self.vy = 2.0
                self.pos_y = float(self.screen_rect.top() + 2)
                self.update_appearance(force=True)
            return

        # 顶天花板检测（不管速度多大都能卡住）
        if next_y <= self.screen_rect.top() and self.vy < 0:
            next_y = float(self.screen_rect.top())
            self.state = "CEILING_HANG"
            self.current_ceiling_tick = 0
            self.vx = 0.0
            self.vy = 0.0
            self.update_appearance(force=True)
            self.pos_x = float(next_x)
            self.pos_y = float(next_y)
            self.move(round(self.pos_x), round(self.pos_y))
            return

        # 2) 左右墙
        left = self.screen_rect.left()
        right_edge = self.screen_rect.right() + 1

        hit_wall = False
        if next_x <= left:
            next_x = float(left)
            hit_wall = True
        elif next_x + self.pet_width >= right_edge:
            next_x = float(right_edge - self.pet_width)
            hit_wall = True

        # 碰墙进入WALL_SLIDE状态（但如果已在地面则直接停住，避免闪一帧墙壁动画）
        if hit_wall and self.state != "IDLE":
            floor_y_check = self.floor_y
            if next_y + self.pet_height >= floor_y_check:
                self.state = "IDLE"
                self.vx = 0.0
                self.vy = 0.0
                next_y = float(floor_y_check - self.pet_height)
                self.update_appearance(force=True)
            else:
                self.state = "WALL_SLIDE"
                self.vx = 0.0
                self.update_appearance(force=True)

        # 3) 地面 / 重力
        floor_y = self.floor_y

        if self.state == "WALL_SLIDE":
            # 自动掉落开关：控制是否向下滑动
            if getattr(self, 'auto_fall_enabled', True):
                # 开启：正常下滑
                self.vy = self.slide_speed
            else:
                # 关闭：贴墙不动（vy=0）
                self.vy = 0.0
            
            # 到达地面后的处理
            if next_y + self.pet_height >= floor_y:
                next_y = float(floor_y - self.pet_height)
                if getattr(self, 'auto_fall_enabled', True):
                    # 开启：到底变idle
                    self.state = "IDLE"
                    self.vx = 0.0
                    self.vy = 0.0
                    self.update_appearance(force=True)
                # 关闭：继续保持wall_slide状态（贴墙扒着）
        else:
            if next_y + self.pet_height < floor_y:
                self.vy += self.gravity
                if self.state != "FALL":
                    self.state = "FALL"
                    self.update_appearance(force=True)
            else:
                next_y = float(floor_y - self.pet_height)
                self.vy = 0.0

                if self.state == "FALL":
                    self.vx = 0.0
                    self.state = "IDLE"
                    self.update_appearance(force=True)

        self.pos_x = float(next_x)
        self.pos_y = float(next_y)

        nx, ny = self._clamp_to_screen(int(self.pos_x), int(self.pos_y))
        self.pos_x = float(nx)
        self.pos_y = float(ny)
        self.move(nx, ny)

        if self.state in ["IDLE", "WALK"]:
            self.update_appearance(force=False)

    # ---------- Ctrl+拖边缩放：热区判定 ----------
    def _resize_hit_test(self, local_x: int, local_y: int):
        z = RESIZE_HOTZONE_PX
        w = self.pet_width
        h = self.pet_height

        left = local_x <= z
        right = local_x >= (w - z)
        top = local_y <= z
        bottom = local_y >= (h - z)

        if top and left:
            return "TL"
        if top and right:
            return "TR"
        if bottom and left:
            return "BL"
        if bottom and right:
            return "BR"
        if left:
            return "L"
        if right:
            return "R"
        if top:
            return "T"
        if bottom:
            return "B"
        return None

    def _set_resize_cursor(self, anchor):
        if anchor in ("TL", "BR"):
            self.setCursor(QCursor(Qt.CursorShape.SizeFDiagCursor))
        elif anchor in ("TR", "BL"):
            self.setCursor(QCursor(Qt.CursorShape.SizeBDiagCursor))
        elif anchor in ("L", "R"):
            self.setCursor(QCursor(Qt.CursorShape.SizeHorCursor))
        elif anchor in ("T", "B"):
            self.setCursor(QCursor(Qt.CursorShape.SizeVerCursor))
        else:
            self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))

    # ---------- 鼠标输入 ----------
    def mousePressEvent(self, event):
        self._mark_user_interaction()
        if event.button() != Qt.MouseButton.LeftButton:
            return

        self._headpat_timer.stop()

        mods = event.modifiers()
        local = event.position().toPoint()

        # Ctrl + 点边缘/角落 => 进入缩放（像缩放图片）
        if mods & Qt.KeyboardModifier.ControlModifier:
            anchor = self._resize_hit_test(local.x(), local.y())
            if anchor is not None:
                self._resizing = True
                self._resize_anchor = anchor
                self._resize_start_global = event.globalPosition().toPoint()
                self._resize_start_size = self.pet_size
                self._resize_center = (self.x() + self.half_w, self.y() + self.half_h)
                self._set_resize_cursor(anchor)
                return

        # 否则：先进入“待判定点击”，不立刻 DRAG
        self._pending_click = True
        self._drag_started = False
        self._press_global = event.globalPosition().toPoint()
        self.last_mouse_pos = self._press_global

        # 站立态长按触发摸头（Ctrl 缩放时不触发）
        self._headpat_timer.stop()
        if self.state in ["IDLE", "WALK"] and not (mods & Qt.KeyboardModifier.ControlModifier):
            self._headpat_timer.start(self.headpat_hold_ms)

    def mouseMoveEvent(self, event):
        mods = event.modifiers()
        local = event.position().toPoint()

        # 1) 缩放进行中
        if self._resizing:
            curr = event.globalPosition().toPoint()
            dx = curr.x() - self._resize_start_global.x()
            dy = curr.y() - self._resize_start_global.y()

            # 统一做等比缩放：根据“最影响”的方向变化
            if self._resize_anchor in ("BR", "R", "B"):
                delta = max(dx, dy)
            elif self._resize_anchor in ("TL", "L", "T"):
                delta = max(-dx, -dy)
            elif self._resize_anchor == "TR":
                delta = max(dx, -dy)
            elif self._resize_anchor == "BL":
                delta = max(-dx, dy)
            else:
                delta = max(dx, dy)

            steps = int(delta / SIZE_STEP)
            new_size = self._resize_start_size + steps * SIZE_STEP

            # 以中心为锚（像图片缩放）
            cx, cy = self._resize_center
            old_half_w = self.half_w
            old_half_h = self.half_h

            self._apply_size(new_size, keep_center=False)

            # 由于 _apply_size 已以窗口中心处理，这里不再额外 move
            return

        # 2) 没有交互时：Ctrl 悬停边缘显示缩放光标（像软件里）
        if (mods & Qt.KeyboardModifier.ControlModifier) and self.state != "DRAG" and self._pending_click is False:
            anchor = self._resize_hit_test(local.x(), local.y())
            self._set_resize_cursor(anchor)
        else:
            # 不要干扰拖拽的手型
            if self.state != "DRAG":
                self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))

        # 3) 待判定点击：移动超过阈值才开始 DRAG
        if self._pending_click and not self._drag_started:
            curr = event.globalPosition().toPoint()
            dx = curr.x() - self._press_global.x()
            dy = curr.y() - self._press_global.y()
            if (dx * dx + dy * dy) >= (DRAG_THRESHOLD_PX * DRAG_THRESHOLD_PX):
                # 开始拖拽：进入 DRAG（保持你现在的抛物线手感）
                self._headpat_timer.stop()
                self._pending_click = False
                self._drag_started = True

                # 拖拽打断life_mode（生活提醒），恢复正常物理
                if self.life_mode is not None:
                    self.life_mode = None
                    self.life_mode_until_ms = 0

                self.state = "DRAG"
                self.idle_elapsed_ms = 0
                self.last_mouse_pos = curr

                self.pos_x = float(self.x())
                self.pos_y = float(self.y())
                self.vx = 0.0
                self.vy = 0.0

                self.update_appearance(force=True)
                self.setCursor(QCursor(Qt.CursorShape.ClosedHandCursor))
                return

        # 4) 拖拽中（保持原逻辑）
        if self.state == "DRAG":
            curr_pos = event.globalPosition().toPoint()

            target_x = curr_pos.x() - self.half_w
            target_y = curr_pos.y() - self.half_h

            # 关键：拖拽过程就夹住在屏幕内
            target_x, target_y = self._clamp_to_screen(target_x, target_y)
            self.move(target_x, target_y)

            self.pos_x = float(self.x())
            self.pos_y = float(self.y())

            # 计算甩的速度（仍然保留）
            self.vx = (curr_pos.x() - self.last_mouse_pos.x()) * 1.5
            self.vy = (curr_pos.y() - self.last_mouse_pos.y()) * 1.5

            self.last_mouse_pos = curr_pos
            self.idle_elapsed_ms = 0

    def mouseReleaseEvent(self, event):
        self._mark_user_interaction()
        if event.button() != Qt.MouseButton.LeftButton:
            return

        self._headpat_timer.stop()

        # 1) 结束缩放
        if self._resizing:
            self._resizing = False
            self._resize_anchor = None
            self._resize_start_global = None
            self._resize_start_size = None
            self._resize_center = None
            self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
            return

        # 2) 拖拽松手：根据落点判定状态（顶 > 墙 > 地 > 空中）
        if self.state == "DRAG":
            left = self.screen_rect.left()
            top = self.screen_rect.top()
            right_edge = self.screen_rect.right() + 1
            floor_y = self.floor_y
            snap = 2

            x = int(self.pos_x)
            y = int(self.pos_y)

            on_top = abs(y - top) <= snap
            on_left = abs(x - left) <= snap
            on_right = abs((x + self.pet_width) - right_edge) <= snap
            on_floor = abs((y + self.pet_height) - floor_y) <= snap

            if on_top:
                self.state = "CEILING_HANG"
                self.current_ceiling_tick = 0
                self.vx = 0.0
                self.vy = 0.0
                self.pos_y = float(top)
                self.move(x, top)
            elif on_left or on_right:
                self.state = "WALL_SLIDE"
                self.vx = 0.0
                self.vy = self.slide_speed
                if on_left:
                    self.pos_x = float(left)
                    self.move(left, y)
                else:
                    rx = int(right_edge - self.pet_width)
                    self.pos_x = float(rx)
                    self.move(rx, y)
            elif on_floor:
                self.state = "IDLE"
                self.vx = 0.0
                self.vy = 0.0
                fy = int(floor_y - self.pet_height)
                self.pos_y = float(fy)
                self.move(x, fy)
                self._walk_until_ms = 0  # 落地后立刻允许ai_think重新决策
            else:
                # 空中：保持你甩出去的抛物线速度
                self.state = "FALL"

            self.update_appearance(force=True)
            self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
            return

        # 3) 点击松手：触发 POKE（未来替换成 poke.gif）
        if self._pending_click:
            self._pending_click = False
            self._trigger_poke()
            self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
            return
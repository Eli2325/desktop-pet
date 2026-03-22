"""Activity bubbles: BubbleWidget + app_map/bubbles/filters/foreground polling (mixin for DesktopPet)."""

import ctypes
import json
import os
import random
import time
from ctypes import wintypes

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QColor, QFont, QPainter
from PyQt6.QtWidgets import QWidget

from logger import logger


class BubbleWidget(QWidget):
    """一个真正透明圆角的气泡窗（不再出现四角黑尖尖）。"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._text = ""
        self._max_w = 240
        self._pad_x = 10
        self._pad_y = 7
        self._radius = 12
        self._bg = QColor(255, 255, 255, 230)
        self._border = QColor(0, 0, 0, 40)
        self._text_color = QColor(20, 20, 20, 255)

        self.setWindowFlags(
            Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        f = QFont()
        f.setPointSize(12)
        self.setFont(f)

    def setText(self, text: str):
        self._text = text or ""
        self.updateGeometry()
        self.update()

    def text(self) -> str:
        return self._text

    def setMaxWidth(self, w: int):
        self._max_w = max(140, int(w))
        self.updateGeometry()
        self.update()

    def sizeHint(self):
        fm = self.fontMetrics()
        inner_w = max(60, self._max_w - self._pad_x * 2)
        # 计算多行文本高度
        br = fm.boundingRect(0, 0, inner_w, 10_000, Qt.TextFlag.TextWordWrap, self._text)
        w = min(self._max_w, br.width() + self._pad_x * 2 + 2)
        h = br.height() + self._pad_y * 2 + 2
        return QSize(w, h)

    def paintEvent(self, event):
        # 纯绘制：圆角底 + 轻阴影 + 文本
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        r = self.rect()

        # 轻阴影（不使用 QGraphicsDropShadowEffect，避免黑尖角）
        shadow_color = QColor(0, 0, 0, 80)
        for i, a in enumerate([30, 18, 10]):
            shadow_color.setAlpha(a)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(shadow_color)
            p.drawRoundedRect(r.adjusted(2+i, 4+i, -2-i, -2-i), self._radius, self._radius)

        # 气泡底
        p.setPen(self._border)
        p.setBrush(self._bg)
        p.drawRoundedRect(r.adjusted(1, 1, -1, -1), self._radius, self._radius)

        # 文本
        p.setPen(self._text_color)
        text_rect = r.adjusted(self._pad_x, self._pad_y, -self._pad_x, -self._pad_y)
        p.drawText(text_rect, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextWordWrap), self._text)

        p.end()

class PetActivityBubblesMixin:
    def reload_activity_config(self):
        """从磁盘重新读取 app_map.json / bubbles.json。"""
        try:
            self._load_activity_files()
        except Exception as e:
            self._request_notice(f"重载配置失败: {e}")
    def get_activity_paths(self):
        """给设置面板用：返回配置文件路径。"""
        return {
            "app_map": getattr(self, "app_map_path", None),
            "bubbles": getattr(self, "bubbles_path", None),
        }
    def toggle_activity_bubbles(self):
        self.activity_bubbles_enabled = not self.activity_bubbles_enabled
        if hasattr(self, "act_activity"):
            try:
                self.act_activity.setChecked(self.activity_bubbles_enabled)
            except Exception:
                pass
        # sync chat console checkbox
        try:
            if self._chat_console and hasattr(self._chat_console, "cb_app_bubbles"):
                self._chat_console.cb_app_bubbles.setChecked(self.activity_bubbles_enabled)
        except Exception:
            pass
        # sync settings dialog checkboxes (basic + AI mirror) in real-time
        try:
            dlg = getattr(self, "_settings_dialog", None)
            if dlg is not None:
                for name in ("cb_enabled", "cb_app_bubbles_ai"):
                    if hasattr(dlg, name):
                        w = getattr(dlg, name)
                        w.blockSignals(True)
                        w.setChecked(self.activity_bubbles_enabled)
                        w.blockSignals(False)
        except Exception:
            pass
        if not self.activity_bubbles_enabled:
            self.activity_pending = None
            self.activity_bubble.hide()
    def set_activity_bubbles_enabled(self, enabled: bool):
        """供聊天窗使用：显式开/关活动气泡（不依赖toggle的当前状态）。"""
        try:
            self.activity_bubbles_enabled = bool(enabled)
            if hasattr(self, "act_activity"):
                try:
                    self.act_activity.setChecked(self.activity_bubbles_enabled)
                except Exception:
                    pass
            # sync settings dialog checkboxes (basic + AI mirror) in real-time
            try:
                dlg = getattr(self, "_settings_dialog", None)
                if dlg is not None:
                    for name in ("cb_enabled", "cb_app_bubbles_ai"):
                        if hasattr(dlg, name):
                            w = getattr(dlg, name)
                            w.blockSignals(True)
                            w.setChecked(self.activity_bubbles_enabled)
                            w.blockSignals(False)
            except Exception:
                pass
            if not self.activity_bubbles_enabled:
                self.activity_pending = None
                self.activity_bubble.hide()
        except Exception:
            pass
    # ---------- 活动气泡（v5） ----------
    def _now_ms(self) -> int:
        return int(time.time() * 1000)

    def _default_app_map(self):
        # 仅做你常用软件的第一版；后续可扩展
        return {
            "apps": {
                "chrome.exe": {"name": "Chrome", "category": "browse"},
                "code.exe": {"name": "VSCode", "category": "code"},
                "wechat.exe": {"name": "微信", "category": "chat"},
                "steam.exe": {"name": "Steam", "category": "gamehub"},
                "winword.exe": {"name": "Word", "category": "office"},
                "excel.exe": {"name": "Excel", "category": "office"}
            },
            "chrome_title_rules": [
                {"contains_any": ["bilibili", "哔哩哔哩", "b站"], "category": "video", "name": "B站"},
                {"contains_any": ["chatgpt", "ChatGPT"], "category": "ai", "name": "ChatGPT"},
                {"contains_any": ["gemini", "Google Gemini"], "category": "ai", "name": "Gemini"}
            ]
        }

    def _default_bubbles(self):
        return {
            "settings": {
                "enabled": True,
                "trigger_on_app_switch_only": True,
                "trigger_probability": 0.6,
                "show_ms": 2600,
                "cooldown_seconds_by_category": {
                    "chat": 480, "video": 480, "ai": 360,
                    "code": 480, "office": 600, "browse": 600, "gamehub": 600, "music": 480
                },
                "max_pending": 1
            },
            "idle_chat": [
                "......(发呆中)",
                "在想什么呢~",
                "要不要休息一下",
                "嗯......",
                "陪你~",
                "我在呢"
            ],
            "app_specific": {
                "微信": ["微信都开了，要找人说话吗。", "嗯？有人在等消息？", "回消息别委屈自己。", "聊完记得回来。"],
                "B站": ["B站开了，在看什么呢。", "又开始刷了？我也想一起看。", "这个看起来挺上头的。"],
                "ChatGPT": ["又来找我啦。", "今天要问什么？", "嗯哼，继续聊。"],
                "Gemini": ["去 Gemini 那边串门啦？", "我盯着呢，别被它忽悠。"],
                "VSCode": ["又开始敲代码啦。", "今天要修什么 bug？", "写到一半记得保存。"],
                "Steam": ["Steam！开玩！", "我也想玩。", "别玩太久…算了，玩吧。"],
                "Word": ["开始写东西了。", "文档模式启动。", "别写到忘记喝水。"],
                "Excel": ["Excel…这是要认真干活了。", "表格最磨人，慢慢来。", "别被公式气到。"]
            },
            "category_templates": {
                "chat": ["{app} 都开了，要找人说话吗。", "嗯？{app}……有人在等消息？"],
                "video": ["在 {app} 看什么呢。", "{app} 打开了，准备放松一下？"],
                "ai": ["{app} 打开了，又要问问题啦。", "嗯哼，{app} 时间。"],
                "code": ["{app} 打开了，开始敲代码。", "{app}：今天别熬太晚。"],
                "office": ["{app} 打开了，开始干活。", "这看着像正经工作，我先乖点。"],
                "browse": ["在翻网页呢。", "看什么呢，我也想知道。"],
                "gamehub": ["{app} 打开了，准备开玩？", "我也想凑热闹。"],
                "music": ["{app} 打开了，今天的BGM选好了。", "一边听 {app} 一边忙，感觉会好一点。"]
            },
            "category_pool": {
                "browse": ["又开始翻资料了。", "别刷太久，眼睛会累。"],
                "video": ["这个看起来挺上头。", "我也想一起看。"],
                "chat": ["要找人说话吗。", "聊完记得回来。"],
                "ai": ["嗯？又来问问题啦。", "继续聊。"],
                "code": ["慢慢来，能解决。", "卡住就换个思路。"],
                "office": ["这东西最耗耐心。", "做完就收工。"],
                "gamehub": ["开玩开玩。", "我也想玩。"],
                "music": ["给自己配点背景音乐。", "听歌的时候，心情会不会好一点。"]
            }
        }

    def _ensure_json(self, path: str, default_obj):
        if os.path.exists(path):
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(default_obj, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _load_activity_files(self):
        # 自动生成默认文件（你不需要提前准备）
        self._ensure_json(self.app_map_path, self._default_app_map())
        self._ensure_json(self.bubbles_path, self._default_bubbles())

        # 读取
        self.app_map = self._default_app_map()
        self.bubbles = self._default_bubbles()
        try:
            with open(self.app_map_path, "r", encoding="utf-8") as f:
                self.app_map = json.load(f)
        except Exception as e:
            print(f"Error loading app_map.json: {e}")
        try:
            with open(self.bubbles_path, "r", encoding="utf-8") as f:
                self.bubbles = json.load(f)
        except Exception as e:
            print(f"Error loading bubbles.json: {e}")

        # 同步设置
        s = (self.bubbles or {}).get("settings", {})
        self.activity_bubbles_enabled = bool(s.get("enabled", True))
        self._activity_trigger_prob = float(s.get("trigger_probability", 0.6))
        self._activity_show_ms = int(s.get("show_ms", 2600))
        cds = s.get("cooldown_seconds_by_category", {})
        for k, v in cds.items():
            try:
                self._activity_cooldown_by_cat[k] = int(v) * 1000
            except Exception:
                pass

    def _load_filters(self):
        """加载filters.json用于过滤系统进程"""
        default_filters = {
            "version": 1,
            "ignored_exe": [
                # 核心系统进程
                "system", "system.exe", "idle", "system idle process",
                "dwm.exe", "csrss.exe", "smss.exe", "wininit.exe",
                
                # 桌宠自己
                "python.exe", "pythonw.exe",
                
                # Windows Shell
                "explorer.exe", "sihost.exe",
                "startmenuexperiencehost.exe", 
                "shellexperiencehost.exe",
                "searchhost.exe", "searchapp.exe",
                
                # 系统服务
                "svchost.exe", "conhost.exe",
                "runtimebroker.exe", "dllhost.exe",
                "backgroundtaskhost.exe",
                
                # 输入法相关
                "ctfmon.exe", "textinputhost.exe", "chsime.exe",
                
                # 安全相关
                "securityhealthservice.exe",
                "msmpeng.exe", "nissrv.exe",
                "antimalware service executable",
                "windows defender",
                
                # PWA和应用框架
                "applicationframehost.exe",
                "msedge_pwa_launcher.exe",
                
                # 锁屏和通知
                "lockapp.exe", "shellhost.exe",
                
                # Windows更新和维护
                "trustedinstaller.exe", "tiworker.exe",
                
                # 其他系统组件
                "winlogon.exe", "lsass.exe", "services.exe",
                "spoolsv.exe", "taskhostw.exe"
            ],
            "ignored_title_keywords": [
                "desktop pet settings", "桌宠设置", "pet settings"
            ]
        }
        self._ensure_json(self.filters_path, default_filters)
        try:
            with open(self.filters_path, "r", encoding="utf-8") as f:
                self.filters = json.load(f)
        except Exception as e:
            print(f"Error loading filters.json: {e}")
            self.filters = default_filters

    # ---- Windows 前台窗口读取（不依赖额外库） ----
    def _get_foreground_process_and_title(self):
        if os.name != "nt":
            return None, None

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None, None

        # title（GetWindowTextLengthW 失败时可能返回负数，需防护）
        length = user32.GetWindowTextLengthW(hwnd)
        buf_size = max(1, int(length) + 1)
        buff = ctypes.create_unicode_buffer(buf_size)
        user32.GetWindowTextW(hwnd, buff, buf_size)
        title = buff.value or ""

        # pid
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        pid_val = int(pid.value)
        if pid_val <= 0:
            return None, title

        # process name (exe basename)
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        hproc = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid_val)
        if not hproc:
            return None, title

        try:
            size = wintypes.DWORD(260)
            buf = ctypes.create_unicode_buffer(260)
            # QueryFullProcessImageNameW
            if ctypes.windll.kernel32.QueryFullProcessImageNameW(hproc, 0, buf, ctypes.byref(size)):
                full = buf.value
                exe = os.path.basename(full).lower()
            else:
                exe = None
        finally:
            kernel32.CloseHandle(hproc)

        return exe, title

    def _resolve_app(self, exe: str, title: str):
        exe_l = (exe or "").lower()
        title_l = (title or "").lower()

        # default: unknown
        app_name = None
        category = None

        # browser title rules优先检查（最精确）
        browser_exes = ["chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe", "iexplore.exe", "safari.exe"]
        if exe_l in browser_exes:
            # 优先使用新的browser_title_rules，兼容旧的chrome_title_rules
            rules = (self.app_map or {}).get("browser_title_rules") or (self.app_map or {}).get("chrome_title_rules", [])
            for rule in rules:
                keys = rule.get("contains_any", [])
                if any((k or "").lower() in title_l for k in keys):
                    app_name = rule.get("name")
                    category = rule.get("category")
                    # 匹配到title规则就直接返回，不再走后面的通用匹配
                    if app_name and category:
                        return app_name, category
                    break

        apps = (self.app_map or {}).get("apps", {})

        # 兼容旧版：apps 是 dict 的情况
        if isinstance(apps, dict):
            entry = apps.get(exe_l)
            if isinstance(entry, dict):
                app_name = entry.get("name") or exe_l
                if entry.get("category"):
                    category = entry.get("category")
            elif isinstance(entry, str):
                category = entry

        # 新版：apps 是 list，带 match{exe_contains,title_contains}
        elif isinstance(apps, list):
            for rule in apps:
                if not isinstance(rule, dict):
                    continue
                m = rule.get("match") or {}
                if not isinstance(m, dict):
                    m = {}
                exe_kw = (m.get("exe_contains") or "").lower()
                title_kw = (m.get("title_contains") or "").lower()

                if not exe_kw and not title_kw:
                    continue

                matched = False
                if exe_kw and exe_kw in exe_l:
                    matched = True
                    if title_kw and title_kw not in title_l:
                        matched = False
                elif (not exe_kw) and title_kw and title_kw in title_l:
                    matched = True

                if not matched:
                    continue

                app_name = rule.get("name") or app_name or exe_l
                if title_kw:
                    app_name = app_name + "|" + title_kw
                if rule.get("category"):
                    category = rule["category"]
                break

        # fallback
        if not app_name:
            app_name = exe_l or "Unknown"
        if not category:
            category = "browse"

        return app_name, category

    def _pick_activity_text(self, app_name: str, category: str):
        bubbles = self.bubbles or {}
        
        # 检查是否允许混用通用文案
        app_allow_mix = bubbles.get("app_allow_mix_general", {})
        allow_mix = app_allow_mix.get(app_name, False)
        
        # 1. app_specific（最高优先级：专属文案）
        app_spec = bubbles.get("app_specific", {})
        has_app_specific = app_name in app_spec and app_spec[app_name]
        
        if has_app_specific:
            if allow_mix:
                # 混用模式：专属文案 + 通用文案都可选
                candidates = []
                
                # 添加专属文案
                candidates.extend(app_spec[app_name])
                
                # 添加用户自定义文案池
                text_pool = bubbles.get("text_pool", {}).get(category, [])
                candidates.extend(text_pool)
                
                # 添加模板文案
                tmpl = bubbles.get("category_templates", {}).get(category, [])
                candidates.extend([t.replace("{app}", app_name) for t in tmpl])
                
                # 添加通用文案池
                pool = bubbles.get("category_pool", {}).get(category, [])
                candidates.extend(pool)
                
                if candidates:
                    return random.choice(candidates)
            else:
                # 只用专属文案
                return random.choice(app_spec[app_name])
        
        # 2. text_pool（用户自定义文案池）
        text_pool = bubbles.get("text_pool", {}).get(category, [])
        if text_pool:
            return random.choice(text_pool)

        # 3. templates（带{app}占位符的模板）
        tmpl = bubbles.get("category_templates", {}).get(category, [])
        if tmpl:
            return random.choice(tmpl).replace("{app}", app_name)

        # 4. category_pool（兜底通用文案）
        pool = bubbles.get("category_pool", {}).get(category, [])
        if pool:
            return random.choice(pool)

        return None

    def _should_fire_activity(self, category: str):
        now = self._now_ms()
        cd = int(self._activity_cooldown_by_cat.get(category, 480000))
        last = int(self._activity_last_fire.get(category, -10**9))
        if now - last < cd:
            return False
        if random.random() > float(self._activity_trigger_prob):
            return False
        self._activity_last_fire[category] = now
        return True

    
    def _should_ignore_process(self, exe: str, title: str) -> bool:
        """使用filters.json过滤系统进程和无用窗口"""
        exe_l = (exe or "").lower()
        title_l = (title or "").lower()
        
        filters = getattr(self, "filters", {})
        
        # 检查ignored_exe
        for ignored in filters.get("ignored_exe", []):
            if ignored and ignored.lower() in exe_l:
                return True
        
        # 检查ignored_title_keywords
        for kw in filters.get("ignored_title_keywords", []):
            if kw and kw.lower() in title_l:
                return True
        
        return False

    def _poll_foreground_app(self):
        # 用户刚交互过：避免把“点击桌宠/唤醒”误判成切应用
        try:
            now_ms = int(time.time() * 1000)
        except Exception:
            now_ms = 0
        if now_ms and now_ms < getattr(self, '_suppress_activity_until_ms', 0):
            return

        if not self.activity_bubbles_enabled or self.quiet_mode:
            self.activity_pending = None
            return

        exe, title = self._get_foreground_process_and_title()
        if not exe:
            return
        
        # 【新增】使用filters.json过滤系统进程和无用窗口
        if self._should_ignore_process(exe, title):
            return  # 被过滤的应用不记录到_recent_apps
        
        # Ignore our own settings window so it doesn't get classified as some other app.
        try:
            t_l = (title or '').lower()
            if 'desktop pet settings' in t_l or '桌宠设置' in t_l or 'pet settings' in t_l:
                return  # 设置窗口也不记录
        except Exception:
            pass

        # 记录最近前台应用，供设置面板/自动映射参考（放在过滤之后）
        try:
            now_ms2 = int(time.time() * 1000)
        except Exception:
            now_ms2 = 0
        ra = getattr(self, "_recent_apps", None)
        if isinstance(ra, dict):
            ra[exe.lower()] = {"exe": exe.lower(), "title": title or "", "last_seen_ms": now_ms2}

        # 先识别应用（包括网站识别）
        app_name, category = self._resolve_app(exe, title)
        
        # 用 (exe, app_name) 判断切换，这样浏览器里切网站也能触发
        sig = (exe.lower(), app_name)
        
        # 【新增】实现"仅切换应用时触发"开关功能
        switch_only = self.bubbles.get("settings", {}).get("trigger_on_app_switch_only", True)
        
        if switch_only:
            # 模式A：仅在切换应用时触发
            if sig == self._activity_last_sig:
                return  # 同一应用，不触发
            self._activity_last_sig = sig
        else:
            # 模式B：持续提醒模式
            # 不判断是否切换，只要冷却时间过了就触发
            # 仍然记录sig用于其他逻辑
            self._activity_last_sig = sig

        # 只在“切换”时触发：同一个 app 连续切回不会频繁说（由 cooldown 控制）
        text = self._pick_activity_text(app_name, category)
        if not text:
            return
        if not self._should_fire_activity(category):
            return

        # 产生候选：忙就欠一句（只保留最后一条）
        self.activity_pending = (text, app_name, category)
        self._try_show_activity_pending()

    def _trigger_idle_chat(self):
        """待机闲聊：每10分钟触发一次碎碎念，增强陪伴感"""
        if self.quiet_mode or (not self.activity_bubbles_enabled):
            return
        
        # 只在稳定站立时才闲聊（不打扰睡眠/交互/提示）
        if not self._is_ground_stable():
            return
        
        # 从bubbles.json的idle_chat池中随机选择
        bubbles = self.bubbles or {}
        idle_chat_pool = bubbles.get("idle_chat", [])
        
        if not idle_chat_pool:
            # 如果没有配置idle_chat，使用默认兜底文案
            idle_chat_pool = [
                "......(发呆中)",
                "在想什么呢~",
                "要不要休息一下",
                "嗯......",
            ]
            logger.warning("bubbles.json缺少idle_chat字段，使用默认文案")
        
        text = random.choice(idle_chat_pool)
        logger.info(f"闲聊触发成功: {text}")
        
        # 不走cooldown机制，直接触发（因为已经是10分钟一次了）
        self.activity_pending = (text, "闲聊", "idle_chat")
        self._try_show_activity_pending()

    def _cleanup_old_app_records(self):
        """定期清理旧的应用记录，防止内存泄漏"""
        try:
            now_ms = int(time.time() * 1000)
        except Exception:
            return
        
        ra = getattr(self, "_recent_apps", None)
        if not isinstance(ra, dict):
            return
        
        # 清理超过1小时未见的应用记录
        one_hour_ms = 3600000
        to_remove = []
        
        for exe, info in ra.items():
            last_seen = info.get("last_seen_ms", 0)
            if now_ms - last_seen > one_hour_ms:
                to_remove.append(exe)
        
        for exe in to_remove:
            del ra[exe]
        
        # 如果记录超过100条，只保留最近的50条
        if len(ra) > 100:
            # 按last_seen_ms排序，保留最新的50条
            sorted_items = sorted(ra.items(), key=lambda x: x[1].get("last_seen_ms", 0), reverse=True)
            self._recent_apps = dict(sorted_items[:50])

    def _is_ground_stable(self) -> bool:
        """检查桌宠是否处于稳定状态（可以触发闲聊）
        
        增加超时保护：如果交互标志位卡住超过设定时间，自动重置
        """
        if self.state not in ("IDLE", "WALK"):
            return False
        
        # 获取当前时间
        try:
            now_ms = int(time.time() * 1000)
        except Exception:
            now_ms = 0
        
        # 检查poke_active，超时自动重置
        if self.poke_active:
            if not hasattr(self, '_poke_started_ms') or self._poke_started_ms is None:
                self._poke_started_ms = now_ms
                return False
            elif now_ms - self._poke_started_ms > 3000:  # 超过3秒
                logger.warning(f"poke_active卡住{(now_ms - self._poke_started_ms)/1000:.1f}秒，自动重置")
                self.poke_active = False
                self._poke_started_ms = None
            else:
                return False
        else:
            self._poke_started_ms = None
        
        # 检查headpat_active，超时自动重置
        if self.headpat_active:
            if not hasattr(self, '_headpat_started_ms') or self._headpat_started_ms is None:
                self._headpat_started_ms = now_ms
                return False
            elif now_ms - self._headpat_started_ms > 3000:  # 超过3秒
                logger.warning(f"headpat_active卡住{(now_ms - self._headpat_started_ms)/1000:.1f}秒，自动重置")
                self.headpat_active = False
                self._headpat_started_ms = None
            else:
                return False
        else:
            self._headpat_started_ms = None
        
        # 检查_resizing，超时自动重置
        if self._resizing:
            if not hasattr(self, '_resizing_started_ms') or self._resizing_started_ms is None:
                self._resizing_started_ms = now_ms
                return False
            elif now_ms - self._resizing_started_ms > 5000:  # 超过5秒
                logger.warning(f"_resizing卡住{(now_ms - self._resizing_started_ms)/1000:.1f}秒，自动重置")
                self._resizing = False
                self._resizing_started_ms = None
            else:
                return False
        else:
            self._resizing_started_ms = None
        
        # 检查_drag_started，超时自动重置
        if self._drag_started:
            if not hasattr(self, '_drag_started_ms') or self._drag_started_ms is None:
                self._drag_started_ms = now_ms
                return False
            elif now_ms - self._drag_started_ms > 5000:  # 超过5秒
                logger.warning(f"_drag_started卡住{(now_ms - self._drag_started_ms)/1000:.1f}秒，自动重置")
                self._drag_started = False
                self._drag_started_ms = None
            else:
                return False
        else:
            self._drag_started_ms = None
        
        # 所有检查通过
        return True

    def _try_show_activity_pending(self):
        if not self.activity_pending:
            return
        if self.quiet_mode or (not self.activity_bubbles_enabled):
            self.activity_pending = None
            return

        text, app_name, category = self.activity_pending

        # 真正忙的时候才延后：拖拽/下落/贴墙/挂顶
        if self.state in ("DRAG", "FALL", "WALL_SLIDE", "CEILING_HANG"):
            if not self._activity_deferred_timer.isActive():
                self._activity_deferred_timer.start(300)
            return

        # 睡觉：不打断，醒来后再说
        if self.state == "SLEEP":
            if not self._activity_deferred_timer.isActive():
                self._activity_deferred_timer.start(1500)
            return

        # IDLE/WALK/NOTICE 都直接显示，不强制停止桌宠
        # bubble会跟随桌宠移动，走路时显示也完全没问题
        self.activity_pending = None
        self._show_activity_bubble(text)

    def _position_activity_bubble(self):
        if not self.activity_bubble.isVisible():
            return

        # 全局坐标：桌宠窗口左上角
        gx = self.x()
        gy = self.y()

        # 控制最大宽度，避免一行太长
        max_w = max(160, int(self.pet_width * 1.45))
        self.activity_bubble.setMaxWidth(max_w)
        self.activity_bubble.adjustSize()

        bx = int(gx + (self.pet_width - self.activity_bubble.width()) / 2)
        by = int(gy - (self.activity_bubble.height() * 0.95))

        # 简单的屏幕边界夹紧（避免跑到屏幕外）
        try:
            sr = self.screen_rect
            bx = max(sr.left(), min(bx, sr.right() - self.activity_bubble.width()))
            by = max(sr.top(), min(by, sr.bottom() - self.activity_bubble.height()))
        except Exception:
            pass

        self.activity_bubble.move(bx, by)

    def _hide_activity_bubble(self):
        self._activity_bubble_follow = False
        self.activity_bubble.hide()

    def _show_activity_bubble(self, text: str):
        self.activity_bubble.setText(text)
        self.activity_bubble.adjustSize()

        self._activity_bubble_follow = True
        self._position_activity_bubble()

        self.activity_bubble.show()
        self.activity_bubble.raise_()

        # 根据文本长度自适应显示时长：基础2000ms + 每字60ms，上限12000ms
        adaptive_ms = min(12000, 2000 + len(self.activity_bubble.text()) * 60)
        self._activity_bubble_timer.start(max(int(self._activity_show_ms), adaptive_ms))


import json
import os
import sys
import time
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QWidget, QLabel, QCheckBox,
    QDoubleSpinBox, QSpinBox, QPushButton, QFormLayout, QListWidget, QListWidgetItem,
    QLineEdit, QComboBox, QMessageBox, QTextEdit, QGroupBox, QInputDialog, QFrame,
    QProgressDialog, QApplication, QScrollArea, QGridLayout
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QColor, QIcon
from logger import logger
from ai_config import (
    AISettings, load_ai_settings, save_ai_settings,
    PROVIDER_PRESETS, guess_supports_vision,
    load_prompt_presets, save_prompt_presets, DEFAULT_PROMPT_PRESETS,
)
from ai_openai_client import list_models, chat_completion



class _FetchModelsWorker(QThread):
    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self._settings = settings

    def run(self):
        try:
            models = list_models(self._settings)
            self.finished.emit(models)
        except Exception as e:
            self.error.emit(str(e))


class _TestAIWorker(QThread):
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self._settings = settings

    def run(self):
        try:
            r = chat_completion(self._settings, "ping", timeout_s=15)
            text = (r.text or "").strip()
            self.finished.emit(text)
        except Exception as e:
            self.error.emit(str(e))


CATEGORIES = ["chat","video","ai","code","office","browse","gamehub","music"]

def _load_json(path: str, default: dict) -> dict:
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading {path}: {e}")
        return default

def _save_json(path: str, data: dict) -> None:
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _normalize_appmap(appmap):
    # Accept multiple legacy shapes and normalize into: {"version": 1, "apps": [ {name, category, match{exe_contains,title_contains}} ... ]}
    if not isinstance(appmap, dict):
        return {"version": 1, "apps": []}

    # Legacy shape: top-level mapping { "Weixin": "chat", ... }
    if "apps" not in appmap and any(isinstance(v, str) for v in appmap.values()):
        apps = []
        for k, v in appmap.items():
            if k in ("version", "settings", "text_pool"):
                continue
            if isinstance(v, str):
                apps.append({
                    "name": k,
                    "category": v,
                    "match": {"exe_contains": k, "title_contains": ""}
                })
        return {"version": 1, "apps": apps}

    apps = appmap.get("apps", [])

    # apps as dict: { "Weixin": "chat" } or { "Weixin": {rule...} }
    if isinstance(apps, dict):
        new_apps = []
        for k, v in apps.items():
            if isinstance(v, str):
                new_apps.append({
                    "name": k,
                    "category": v,
                    "match": {"exe_contains": k, "title_contains": ""}
                })
            elif isinstance(v, dict):
                rule = dict(v)
                rule.setdefault("name", k)
                rule.setdefault("category", "")
                rule.setdefault("match", {"exe_contains": k, "title_contains": ""})
                m = rule.get("match") or {}
                if not isinstance(m, dict):
                    m = {"exe_contains": k, "title_contains": ""}
                m.setdefault("exe_contains", k)
                m.setdefault("title_contains", "")
                rule["match"] = m
                new_apps.append(rule)
        appmap["apps"] = new_apps
        appmap.setdefault("version", 1)
        return appmap

    # apps as list: can be list[dict] or list[str]
    if isinstance(apps, list):
        new_apps = []
        for entry in apps:
            if isinstance(entry, dict):
                rule = dict(entry)
                rule.setdefault("name", "")
                rule.setdefault("category", "")
                rule.setdefault("match", {"exe_contains": "", "title_contains": ""})
                m = rule.get("match") or {}
                if not isinstance(m, dict):
                    m = {"exe_contains": "", "title_contains": ""}
                m.setdefault("exe_contains", "")
                m.setdefault("title_contains", "")
                rule["match"] = m
                new_apps.append(rule)
            elif isinstance(entry, str):
                # Treat string as an exe keyword placeholder (unmapped yet)
                new_apps.append({
                    "name": entry,
                    "category": "",
                    "match": {"exe_contains": entry, "title_contains": ""}
                })
        appmap["apps"] = new_apps
        appmap.setdefault("version", 1)
        return appmap

    # Unknown shape
    return {"version": 1, "apps": []}

class SettingsDialog(QDialog):
    def __init__(self, pet):
        super().__init__()
        self.pet = pet
        self.setWindowTitle("桌宠设置")
        self.setMinimumWidth(760)
        

        # 设置窗口图标（使用和桌宠一样的图标）
        try:
            icon_path = os.path.join(pet.assets_dir, "icon.ico")
            if not os.path.exists(icon_path):
                icon_path = os.path.join(pet.assets_dir, "icon.png")
            if os.path.exists(icon_path):
                self.setWindowIcon(QIcon(icon_path))
        except Exception:
            pass

        # 从pet_core获取正确的配置文件路径
        from config_utils import get_config_dir
        config_dir = get_config_dir()
        
        self.bubbles_path = os.path.join(config_dir, "bubbles.json")
        self.appmap_path = os.path.join(config_dir, "app_map.json")
        self.filters_path = os.path.join(config_dir, "filters.json")
        self.pet_settings_path = os.path.join(config_dir, "pet_settings.json")

        # Load configs from correct paths
        self.bubbles = _load_json(self.bubbles_path, {"version":1,"settings":{},"text_pool":{}})
        self.appmap = _normalize_appmap(_load_json(self.appmap_path, {"version":1,"apps":[]}))
        self.filters = _load_json(self.filters_path, {"version":1,"ignored_exe":[],"ignored_title_keywords":[]})

        self.pet_settings = _load_json(self.pet_settings_path, {'version': 1, 'behavior': {'move_speed': 1.5, 'ai_interval_ms': 2000, 'auto_walk_enabled': True, 'roam_radius_px': 0, 'edge_margin_px': 0, 'auto_fall_enabled': True}, 'sleep': {'enabled': True, 'idle_minutes': 15, 'adrenaline_minutes': 10}, 'reminders': {'water_enabled': True, 'water_interval_min': 30, 'move_enabled': True, 'move_interval_min': 45, 'active_start_h': 9, 'active_start_m': 0, 'active_end_h': 23, 'active_end_m': 30, 'notice_duration_ms': 3000, 'idle_chat_interval_min': 10}})

        self.tabs = QTabWidget()
        self.tab_basic = QWidget()
        self.tab_behavior = QWidget()
        self.tab_rules = QWidget()
        self.tab_mapping = QWidget()
        self.tab_text = QWidget()
        self.tab_filters = QWidget()
        self.tab_ai = QWidget()

        self.tabs.addTab(self.tab_basic, "基础")
        self.tabs.addTab(self.tab_behavior, "行为")
        self.tabs.addTab(self.tab_rules, "规则")
        self.tabs.addTab(self.tab_mapping, "应用映射")
        self.tabs.addTab(self.tab_text, "文案池")
        self.tabs.addTab(self.tab_filters, "过滤器")
        self.tabs.addTab(self.tab_ai, "AI")

        root = QVBoxLayout()
        root.addWidget(self.tabs)

        btn_row = QHBoxLayout()
        self.btn_save = QPushButton("保存并应用")
        self.btn_close = QPushButton("关闭")
        self.btn_save.clicked.connect(self._save_apply)
        self.btn_close.clicked.connect(self.close)
        btn_row.addStretch(1)
        btn_row.addWidget(self.btn_save)
        btn_row.addWidget(self.btn_close)
        root.addLayout(btn_row)

        self.setLayout(root)

        self._build_basic()
        self._build_behavior()
        self._build_rules()
        self._build_mapping()
        self._build_text()
        self._build_filters()
        self._build_ai()

        self._loading = False
        self._load_into_widgets()
        self._dirty = False
        self._connect_dirty()

    def _mark_dirty(self):
        """用户修改了任意配置相关控件时调用，用于关闭时提示未保存"""
        # 在通过 _load_into_widgets 加载数据时会触发很多信号，这些不应视为用户编辑
        if getattr(self, "_loading", False):
            return
        self._dirty = True

    def _sync_app_bubbles_from_ai_checkbox(self):
        """AI 页的「应用检测气泡」只是一个镜像开关，实际与基础页同一配置。"""
        try:
            if not hasattr(self, "cb_app_bubbles_ai") or not hasattr(self, "cb_enabled"):
                return
            self.cb_enabled.setChecked(bool(self.cb_app_bubbles_ai.isChecked()))
        except Exception:
            pass
        self._push_app_bubbles_outward()

    def _push_app_bubbles_outward(self):
        """将当前应用检测气泡状态实时同步到控制台、托盘菜单和 pet 运行时。"""
        if getattr(self, "_loading", False) or getattr(self, "_ai_loading", False):
            return
        try:
            enabled = bool(self.cb_enabled.isChecked())
            if hasattr(self, "pet"):
                if hasattr(self.pet, "set_activity_bubbles_enabled"):
                    self.pet.set_activity_bubbles_enabled(enabled)
                console = getattr(self.pet, "_chat_console", None)
                if console and hasattr(console, "cb_app_bubbles"):
                    console.cb_app_bubbles.blockSignals(True)
                    console.cb_app_bubbles.setChecked(enabled)
                    console.cb_app_bubbles.blockSignals(False)
        except Exception:
            pass

    def _push_quiet_mode_outward(self):
        """将安静模式状态实时同步到托盘菜单和 pet 运行时。"""
        if getattr(self, "_loading", False):
            return
        try:
            quiet = bool(self.cb_quiet.isChecked())
            if hasattr(self, "pet"):
                self.pet.quiet_mode = quiet
                if hasattr(self.pet, "act_quiet"):
                    self.pet.act_quiet.setChecked(quiet)
                console = getattr(self.pet, "_chat_console", None)
                if console and hasattr(console, "cb_quiet"):
                    console.cb_quiet.blockSignals(True)
                    console.cb_quiet.setChecked(quiet)
                    console.cb_quiet.blockSignals(False)
        except Exception:
            pass

    def _connect_dirty(self):
        """将各标签页中会修改配置的控件连接到 _mark_dirty"""
        self.cb_enabled.stateChanged.connect(self._mark_dirty)
        self.cb_enabled.stateChanged.connect(lambda _: self._push_app_bubbles_outward())
        self.cb_switch_only.stateChanged.connect(self._mark_dirty)
        self.cb_quiet.stateChanged.connect(self._mark_dirty)
        self.cb_quiet.stateChanged.connect(self._push_quiet_mode_outward)
        self.sp_move_speed.valueChanged.connect(self._mark_dirty)
        self.sp_ai_interval.valueChanged.connect(self._mark_dirty)
        self.cb_auto_walk.stateChanged.connect(self._mark_dirty)
        self.sp_roam_radius.valueChanged.connect(self._mark_dirty)
        self.sp_edge_margin.valueChanged.connect(self._mark_dirty)
        self.cb_auto_fall.stateChanged.connect(self._mark_dirty)
        if hasattr(self, "cb_sleep_enable"):
            self.cb_sleep_enable.stateChanged.connect(self._mark_dirty)
        self.sp_sleep_idle_min.valueChanged.connect(self._mark_dirty)
        self.sp_adrenaline_min.valueChanged.connect(self._mark_dirty)
        self.cb_water_enable.stateChanged.connect(self._mark_dirty)
        self.sp_water_interval.valueChanged.connect(self._mark_dirty)
        self.cb_move_enable.stateChanged.connect(self._mark_dirty)
        self.sp_move_interval.valueChanged.connect(self._mark_dirty)
        self.sp_start_h.valueChanged.connect(self._mark_dirty)
        self.sp_start_m.valueChanged.connect(self._mark_dirty)
        self.sp_end_h.valueChanged.connect(self._mark_dirty)
        self.sp_end_m.valueChanged.connect(self._mark_dirty)
        self.sp_notice_ms.valueChanged.connect(self._mark_dirty)
        self.sp_idle_chat_interval.valueChanged.connect(self._mark_dirty)
        self.sp_prob.valueChanged.connect(self._mark_dirty)
        self.sp_show.valueChanged.connect(self._mark_dirty)
        self.sp_front.valueChanged.connect(self._mark_dirty)
        self.sp_pending.valueChanged.connect(self._mark_dirty)
        for sp in (self.cooldowns or {}).values():
            if hasattr(sp, "valueChanged"):
                sp.valueChanged.connect(self._mark_dirty)
        self.ed_exe_contains.textChanged.connect(self._mark_dirty)
        self.ed_title_contains.textChanged.connect(self._mark_dirty)
        self.cb_cat.currentIndexChanged.connect(self._mark_dirty)
        self.ed_name.textChanged.connect(self._mark_dirty)
        if hasattr(self, "ai_base_url"):
            self.ai_base_url.textChanged.connect(self._mark_dirty)
        if hasattr(self, "ai_api_key"):
            self.ai_api_key.textChanged.connect(self._mark_dirty)
        if hasattr(self, "ai_model"):
            self.ai_model.textChanged.connect(self._mark_dirty)
        if hasattr(self, "ai_system_prompt"):
            self.ai_system_prompt.textChanged.connect(self._mark_dirty)
        if hasattr(self, "ai_min_reply"):
            self.ai_min_reply.valueChanged.connect(self._mark_dirty)
        if hasattr(self, "ai_max_bubble"):
            self.ai_max_bubble.valueChanged.connect(self._mark_dirty)
        if hasattr(self, "ai_max_memory"):
            self.ai_max_memory.valueChanged.connect(self._mark_dirty)
        if hasattr(self, "ai_auto_screenshot"):
            self.ai_auto_screenshot.valueChanged.connect(self._mark_dirty)
        if hasattr(self, "cb_app_bubbles_ai"):
            self.cb_app_bubbles_ai.stateChanged.connect(self._mark_dirty)
        if hasattr(self, "ai_blackbox_keep"):
            self.ai_blackbox_keep.valueChanged.connect(self._mark_dirty)
        if hasattr(self, "ai_provider"):
            self.ai_provider.currentIndexChanged.connect(self._mark_dirty)

    def closeEvent(self, event):
        """关闭按钮/右上角 X：直接关闭设置窗口，不再提示未保存。"""
        try:
            self.hide()
            event.accept()
        except Exception:
            event.accept()

    # ---------- Build UI ----------
    def _build_basic(self):
        lay = QVBoxLayout()
        self.cb_enabled = QCheckBox("应用检测气泡")
        self.cb_enabled.setToolTip("只控制“检测应用触发的文案气泡”，与 AI 对话无关。")
        self.cb_switch_only = QCheckBox("仅在切换前台应用时触发")
        self.cb_switch_only.setToolTip("只在前台应用发生变化时弹出气泡，同一应用内不会反复提醒。")
        self.cb_quiet = QCheckBox("安静模式（不冒泡、不提醒）")
        self.cb_quiet.setToolTip("开启后不弹活动气泡也不发喝水/动一动等提醒，拖拽、戳、摸头仍可用。")
        lay.addWidget(self.cb_enabled)
        lay.addWidget(self.cb_switch_only)
        lay.addWidget(self.cb_quiet)
        lay.addSpacing(16)
        # 帮助与路径
        from config_utils import get_config_dir
        help_group = QGroupBox("帮助与路径")
        help_lay = QVBoxLayout()
        config_dir = get_config_dir()
        help_lay.addWidget(QLabel("配置文件位置："))
        help_lay.addWidget(QLabel(config_dir))
        lbl_replace = QLabel("替换外观：在 exe 同目录的 assets 文件夹中，用同名 GIF 覆盖即可。")
        lbl_replace.setToolTip("打包后 exe 旁会有 assets 文件夹，直接替换其中的 idle.gif、walk.gif 等同名文件即可换皮。")
        help_lay.addWidget(lbl_replace)
        self.btn_open_config_dir = QPushButton("打开配置目录")
        self.btn_open_config_dir.clicked.connect(self._open_config_dir)
        help_lay.addWidget(self.btn_open_config_dir)
        help_group.setLayout(help_lay)
        lay.addWidget(help_group)
        lay.addStretch(1)
        self.tab_basic.setLayout(lay)

    def _open_config_dir(self):
        """用系统资源管理器打开配置目录"""
        from config_utils import get_config_dir
        import subprocess
        config_dir = get_config_dir()
        if os.path.isdir(config_dir):
            try:
                if sys.platform == "win32":
                    os.startfile(config_dir)
                elif sys.platform == "darwin":
                    subprocess.run(["open", config_dir], check=False)
                else:
                    subprocess.run(["xdg-open", config_dir], check=False)
            except Exception as e:
                QMessageBox.warning(self, "打开失败", f"无法打开配置目录：{e}")
        else:
            QMessageBox.information(self, "提示", "配置目录尚不存在，请先运行一次桌宠。")


    def _build_behavior(self):
        lay = QVBoxLayout()

        # ---- Pet behavior ----
        title1 = QLabel("桌宠行为")
        title1.setStyleSheet("font-weight:600;")
        lay.addWidget(title1)

        form_beh = QFormLayout()

        self.sp_move_speed = QDoubleSpinBox()
        self.sp_move_speed.setRange(0.2, 10.0)
        self.sp_move_speed.setSingleStep(0.1)
        self.sp_move_speed.setDecimals(1)
        self.sp_move_speed.setToolTip("数值越大移动越快，建议 1.0～2.0。")
        form_beh.addRow(QLabel("移动速度"), self.sp_move_speed)

        self.sp_ai_interval = QSpinBox()
        self.sp_ai_interval.setRange(200, 20000)
        self.sp_ai_interval.setSingleStep(200)
        self.sp_ai_interval.setToolTip("桌宠「思考」间隔（毫秒），越小越爱动；建议 2000～5000。")
        form_beh.addRow(QLabel("活跃度（思考间隔 ms）"), self.sp_ai_interval)

        self.cb_auto_walk = QCheckBox("允许自动走动")
        form_beh.addRow(QLabel("自动走动"), self.cb_auto_walk)

        self.sp_roam_radius = QSpinBox()
        self.sp_roam_radius.setRange(0, 2000)
        self.sp_roam_radius.setSingleStep(20)
        self.sp_roam_radius.setToolTip("以当前落点为中心的水平游走范围（像素），0 表示不限制。")
        form_beh.addRow(QLabel("游走范围（0=不限）"), self.sp_roam_radius)

        self.sp_edge_margin = QSpinBox()
        self.sp_edge_margin.setRange(0, 200)
        self.sp_edge_margin.setSingleStep(2)
        self.sp_edge_margin.setToolTip("距离屏幕边缘保留的安全距离，避免贴边。")
        form_beh.addRow(QLabel("边缘安全距离"), self.sp_edge_margin)

        self.cb_auto_fall = QCheckBox("允许自动掉落")
        self.cb_auto_fall.setToolTip("关闭后，贴墙或挂天花板时不会自动掉下来。")
        self.cb_auto_fall.setChecked(True)
        form_beh.addRow(QLabel("自动掉落"), self.cb_auto_fall)

        lay.addLayout(form_beh)
        lay.addSpacing(10)

        # ---- Sleep ----
        title_sleep = QLabel("睡眠")
        title_sleep.setStyleSheet("font-weight:600;")
        lay.addWidget(title_sleep)

        form_sleep = QFormLayout()

        self.cb_sleep_enable = QCheckBox("启用")
        self.cb_sleep_enable.setChecked(True)
        form_sleep.addRow(QLabel("允许进入睡眠"), self.cb_sleep_enable)

        self.sp_sleep_idle_min = QSpinBox()
        self.sp_sleep_idle_min.setRange(1, 240)
        self.sp_sleep_idle_min.setValue(20)
        self.sp_sleep_idle_min.setToolTip("桌宠静止不动超过该分钟数后进入睡眠。")
        form_sleep.addRow(QLabel("发呆多久进入睡眠（分钟）"), self.sp_sleep_idle_min)

        self.sp_adrenaline_min = QSpinBox()
        self.sp_adrenaline_min.setRange(0, 120)
        self.sp_adrenaline_min.setValue(10)
        self.sp_adrenaline_min.setToolTip("启动后的一段时间内不会睡觉，方便你先操作。")
        form_sleep.addRow(QLabel("启动免睡期（分钟）"), self.sp_adrenaline_min)

        # 手动控制睡眠状态（并入睡眠板块）
        row_dbg = QHBoxLayout()
        self.btn_force_sleep = QPushButton("强制睡觉")
        self.btn_force_sleep.setToolTip("立即让桌宠进入睡眠，不再走动。")
        self.btn_force_wake = QPushButton("强制醒来")

        def _do_force_sleep():
            try:
                if hasattr(self, "pet") and self.pet is not None and hasattr(self.pet, "force_sleep"):
                    self.pet.force_sleep()
            except Exception:
                pass

        def _do_force_wake():
            try:
                if hasattr(self, "pet") and self.pet is not None and hasattr(self.pet, "force_wake"):
                    self.pet.force_wake()
            except Exception:
                pass

        self.btn_force_sleep.clicked.connect(_do_force_sleep)
        self.btn_force_wake.clicked.connect(_do_force_wake)

        row_dbg.addWidget(self.btn_force_sleep)
        row_dbg.addWidget(self.btn_force_wake)

        wdbg = QWidget()
        wdbg.setLayout(row_dbg)
        form_sleep.addRow(QLabel("手动控制"), wdbg)

        lay.addLayout(form_sleep)
        lay.addSpacing(10)

        # ---- Reminders ----
        title2 = QLabel("生活提醒")
        title2.setStyleSheet("font-weight:600;")
        lay.addWidget(title2)

        form_rem = QFormLayout()

        self.cb_water_enable = QCheckBox("启用")
        self.sp_water_interval = QSpinBox()
        self.sp_water_interval.setRange(1, 240)
        self.sp_water_interval.setSingleStep(1)
        self.sp_water_interval.setToolTip("每隔多少分钟提醒一次喝水。")

        roww = QHBoxLayout()
        roww.addWidget(self.cb_water_enable)
        roww.addWidget(QLabel("每"))
        roww.addWidget(self.sp_water_interval)
        roww.addWidget(QLabel("分钟"))
        wwrap = QWidget()
        wwrap.setLayout(roww)
        form_rem.addRow(QLabel("喝水提醒"), wwrap)

        self.cb_move_enable = QCheckBox("启用")
        self.sp_move_interval = QSpinBox()
        self.sp_move_interval.setRange(1, 240)
        self.sp_move_interval.setSingleStep(1)
        self.sp_move_interval.setToolTip("每隔多少分钟提醒动一动。")

        rowm = QHBoxLayout()
        rowm.addWidget(self.cb_move_enable)
        rowm.addWidget(QLabel("每"))
        rowm.addWidget(self.sp_move_interval)
        rowm.addWidget(QLabel("分钟"))
        mwrap = QWidget()
        mwrap.setLayout(rowm)
        form_rem.addRow(QLabel("动一动提醒"), mwrap)

        # active window
        self.sp_start_h = QSpinBox()
        self.sp_start_h.setRange(0, 23)
        self.sp_start_m = QSpinBox()
        self.sp_start_m.setRange(0, 59)
        self.sp_start_m.setSingleStep(5)

        self.sp_end_h = QSpinBox()
        self.sp_end_h.setRange(0, 23)
        self.sp_end_m = QSpinBox()
        self.sp_end_m.setRange(0, 59)
        self.sp_end_m.setSingleStep(5)

        rowt = QHBoxLayout()
        rowt.addWidget(QLabel("从"))
        rowt.addWidget(self.sp_start_h)
        rowt.addWidget(QLabel(":"))
        rowt.addWidget(self.sp_start_m)
        rowt.addSpacing(8)
        rowt.addWidget(QLabel("到"))
        rowt.addWidget(self.sp_end_h)
        rowt.addWidget(QLabel(":"))
        rowt.addWidget(self.sp_end_m)
        twrap = QWidget()
        twrap.setLayout(rowt)
        form_rem.addRow(QLabel("生效时段"), twrap)
        twrap.setToolTip("只有在该时间段内才会弹出喝水/动一动提醒。")

        self.sp_notice_ms = QSpinBox()
        self.sp_notice_ms.setRange(500, 20000)
        self.sp_notice_ms.setSingleStep(500)
        self.sp_notice_ms.setToolTip("提醒气泡在屏幕上停留的毫秒数。")
        form_rem.addRow(QLabel("提示停留（毫秒）"), self.sp_notice_ms)

        self.sp_idle_chat_interval = QSpinBox()
        self.sp_idle_chat_interval.setRange(1, 120)
        self.sp_idle_chat_interval.setSingleStep(1)
        self.sp_idle_chat_interval.setValue(10)
        self.sp_idle_chat_interval.setToolTip("桌宠多久碎碎念一次（在你相对空闲时）。")
        form_rem.addRow(QLabel("待机闲聊间隔（分钟）"), self.sp_idle_chat_interval)

        lay.addLayout(form_rem)

        # ---- Restore defaults ----
        btn_row = QHBoxLayout()
        self.btn_restore_defaults = QPushButton("恢复默认设置")
        self.btn_restore_defaults.clicked.connect(self._restore_pet_defaults)
        btn_row.addStretch(1)
        btn_row.addWidget(self.btn_restore_defaults)
        btn_row.addStretch(1)
        lay.addLayout(btn_row)

        lay.addStretch(1)
        self.tab_behavior.setLayout(lay)

    def _build_rules(self):
        # ===== 上半部分：数值设置 + 各类别冷却（可滚动） =====
        top_lay = QVBoxLayout()
        
        # ---- 全局参数 ----
        group_global = QGroupBox("全局参数")
        group_global.setStyleSheet("QGroupBox { font-weight: bold; }")
        global_grid = QGridLayout()
        
        # 触发概率
        self.sp_prob = QDoubleSpinBox()
        self.sp_prob.setRange(0.0, 1.0)
        self.sp_prob.setSingleStep(0.05)
        self.sp_prob.setToolTip("每次满足条件时，按该概率决定是否弹出活动气泡。")
        global_grid.addWidget(QLabel("触发概率"), 0, 0)
        global_grid.addWidget(self.sp_prob, 0, 1)
        
        # 显示时长
        self.sp_show = QSpinBox()
        self.sp_show.setRange(500, 20000)
        self.sp_show.setSingleStep(100)
        self.sp_show.setToolTip("活动气泡在屏幕上停留的毫秒数。")
        global_grid.addWidget(QLabel("显示时长（毫秒）"), 0, 2)
        global_grid.addWidget(self.sp_show, 0, 3)
        
        # 前台稳定阈值
        self.sp_front = QSpinBox()
        self.sp_front.setRange(0, 5000)
        self.sp_front.setSingleStep(50)
        self.sp_front.setToolTip("前台应用切换后需稳定超过该时间（毫秒）才视为真正切换，避免误触发。")
        global_grid.addWidget(QLabel("前台稳定阈值（毫秒）"), 1, 0)
        global_grid.addWidget(self.sp_front, 1, 1)
        
        # 最大待定数
        self.sp_pending = QSpinBox()
        self.sp_pending.setRange(0, 10)
        self.sp_pending.setSingleStep(1)
        self.sp_pending.setToolTip("最多排队几条「等着说」的提醒。")
        global_grid.addWidget(QLabel("最大待定数"), 1, 2)
        global_grid.addWidget(self.sp_pending, 1, 3)
        
        group_global.setLayout(global_grid)
        top_lay.addWidget(group_global)
        
        # ---- 各类别冷却时间 ----
        group_cd = QGroupBox("各类别冷却时间")
        group_cd.setStyleSheet("QGroupBox { font-weight: bold; }")
        cd_form = QFormLayout()
        
        self.cooldowns = {}
        cooldown_defaults = {
            "browse": 600, "video": 480, "chat": 480, "ai": 360,
            "code": 480, "office": 600, "gamehub": 600, "music": 480
        }
        
        category_names = {
            "browse": "浏览",
            "video": "视频",
            "chat": "聊天",
            "ai": "AI",
            "code": "编程",
            "office": "办公",
            "gamehub": "游戏",
            "music": "音乐"
        }
        
        for cat in CATEGORIES:
            sp = QSpinBox()
            sp.setRange(0, 86400)
            sp.setSingleStep(30)
            sp.setToolTip(f"该类别应用触发一次气泡后，需间隔多少秒才能再次触发。")
            self.cooldowns[cat] = sp
            default_val = cooldown_defaults.get(cat, 600)
            cn_name = category_names.get(cat, cat)
            cd_form.addRow(f"{cn_name} 冷却（秒）", sp)
        
        group_cd.setLayout(cd_form)
        
        # 各类别冷却时间放入独立滚动区域
        cd_widget = QWidget()
        cd_layout = QVBoxLayout()
        cd_layout.addWidget(group_cd)
        cd_layout.addStretch(1)
        cd_widget.setLayout(cd_layout)
        
        cd_scroll = QScrollArea()
        cd_scroll.setWidgetResizable(True)
        cd_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        cd_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        cd_scroll.setWidget(cd_widget)
        
        # ===== 下半部分：类别管理（可滚动） =====
        cat_mgmt_section = QGroupBox("管理类别")
        cat_mgmt_section.setStyleSheet("QGroupBox { font-weight: bold; }")
        cat_mgmt_lay = QVBoxLayout()
        
        hint_cat = QLabel("💡 添加/删除/重命名类别，冷却时间会同步")
        hint_cat.setStyleSheet("color: gray; font-size: 11px;")
        hint_cat.setToolTip("类别用于给应用分类（如办公、聊天）；每个类别有独立的冷却时间，在页面上方可调。")
        cat_mgmt_lay.addWidget(hint_cat)
        
        self.category_list = QListWidget()
        cat_mgmt_lay.addWidget(self.category_list)
        
        cat_btn_row = QHBoxLayout()
        self.btn_add_category = QPushButton("+ 添加类别")
        self.btn_rename_category = QPushButton("✏️ 重命名")
        self.btn_del_category = QPushButton("- 删除类别")
        self.btn_add_category.clicked.connect(self._add_category)
        self.btn_rename_category.clicked.connect(self._rename_category)
        self.btn_del_category.clicked.connect(self._delete_category)
        cat_btn_row.addWidget(self.btn_add_category)
        cat_btn_row.addWidget(self.btn_rename_category)
        cat_btn_row.addWidget(self.btn_del_category)
        cat_btn_row.addStretch()
        cat_mgmt_lay.addLayout(cat_btn_row)
        
        cat_mgmt_section.setLayout(cat_mgmt_lay)
        
        # 管理类别区域放入独立滚动区域
        cat_scroll = QScrollArea()
        cat_scroll.setWidgetResizable(True)
        cat_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        cat_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        cat_scroll.setWidget(cat_mgmt_section)
        
        # ===== 底部：恢复默认按钮 =====
        btn_row = QHBoxLayout()
        
        self.btn_restore_values = QPushButton("恢复默认数值")
        self.btn_restore_values.setToolTip("将触发概率、显示时长、冷却时间等恢复为默认，不删除你添加的类别。")
        self.btn_restore_values.setStyleSheet("QPushButton { padding: 8px; font-weight: bold; }")
        self.btn_restore_values.clicked.connect(self._restore_rules_values)
        btn_row.addWidget(self.btn_restore_values)
        
        self.btn_reset_categories = QPushButton("⚠️ 重置类别（删除自定义）")
        self.btn_reset_categories.setStyleSheet("QPushButton { padding: 8px; font-weight: bold; color: #f44336; }")
        self.btn_reset_categories.clicked.connect(self._reset_categories)
        btn_row.addWidget(self.btn_reset_categories)
        
        # ===== 总布局：全局参数 + 两个滚动块 + 底部按钮 =====
        outer = QVBoxLayout()
        outer.addLayout(top_lay)       # 顶部全局参数，不滚动
        outer.addWidget(cd_scroll, 1)  # 中部：各类别冷却，可滚动
        outer.addWidget(cat_scroll, 1) # 下部：管理类别，可滚动
        outer.addLayout(btn_row)       # 按钮固定底部
        
        self.tab_rules.setLayout(outer)


    def _build_mapping(self):
        outer = QHBoxLayout()

        left = QVBoxLayout()
        # 上半部分：应用映射列表
        left.addWidget(QLabel("应用映射  App Mappings:"))
        self.list_apps = QListWidget()
        self.list_apps.currentItemChanged.connect(self._mapping_selected)
        left.addWidget(self.list_apps)

        # 下半部分：网站规则列表（只读展示）
        left.addWidget(QLabel("网站规则  Site Rules:"))
        self.list_sites = QListWidget()
        self.list_sites.currentItemChanged.connect(self._site_selected)
        left.addWidget(self.list_sites)
        
        # 工具栏：自动抓取应用 + 添加网站
        btn_toolbar = QHBoxLayout()
        self.btn_refresh = QPushButton("🔄 自动抓取应用")
        self.btn_refresh.setStyleSheet("QPushButton { font-weight: bold; padding: 8px; background-color: #4CAF50; color: white; } QPushButton:hover { background-color: #45a049; }")
        self.btn_refresh.setToolTip("抓取最近在前台显示的应用。\n使用技巧：先打开要识别的应用并保持在前台，再点此按钮；托盘类应用需先打开窗口。")
        self.btn_refresh.clicked.connect(self._refresh_detected)
        hint_capture = QLabel("请先切换到要识别的应用窗口，再点此按钮。")
        hint_capture.setStyleSheet("color: gray; font-size: 11px;")
        hint_capture.setWordWrap(True)
        
        self.btn_add_website_mapping = QPushButton("+ 添加网站")
        self.btn_add_website_mapping.setStyleSheet("QPushButton { font-weight: bold; padding: 8px; background-color: #2196F3; color: white; } QPushButton:hover { background-color: #0b7dda; }")
        self.btn_add_website_mapping.clicked.connect(self._add_website_dialog)
        
        btn_toolbar.addWidget(self.btn_refresh)
        btn_toolbar.addWidget(self.btn_add_website_mapping)
        btn_toolbar.addStretch()
        left.addLayout(btn_toolbar)
        left.addWidget(hint_capture)

        right = QFormLayout()
        self.ed_exe_contains = QLineEdit()
        # App 映射仅按 exe 匹配，不再提供标题匹配输入框（title_contains 仅用于浏览器网站规则）
        self.ed_title_contains = QLineEdit()
        self.ed_title_contains.setVisible(False)
        self.cb_cat = QComboBox()
        for c in CATEGORIES:
            self.cb_cat.addItem(c)
        self.ed_name = QLineEdit()

        self.btn_add = QPushButton("保存应用映射")
        self.btn_del = QPushButton("删除应用映射")
        self.btn_block = QPushButton("🚫 屏蔽该应用")
        self.btn_block.setToolTip("将该应用加入过滤器，不再触发活动气泡；并从映射列表中移除。")
        self.btn_add.clicked.connect(self._add_mapping)
        self.btn_del.clicked.connect(self._del_mapping)
        self.btn_block.clicked.connect(self._block_mapping)

        # 记录 App / Site 两种模式下按钮文本，默认是 App 模式
        self._btn_add_app_text = "保存应用映射"
        self._btn_del_app_text = "删除应用映射"
        self._btn_add_site_text = "更新网站"
        self._btn_del_site_text = "删除网站"
        self._mapping_mode = "app"

        self.ed_exe_contains.setToolTip("用于识别应用的进程名，如 weixin.exe；一般填完整 exe 名即可。")
        right.addRow("进程名包含", self.ed_exe_contains)
        right.addRow("类别", self.cb_cat)
        self.ed_name.setToolTip("在列表和专属文案中显示的名称，可自定义。")
        right.addRow("显示名称", self.ed_name)
        right.addRow(self.btn_add)
        right.addRow(self.btn_del)
        right.addRow(self.btn_block)

        right_box = QWidget(); right_box.setLayout(right)

        outer.addLayout(left, 2)
        outer.addWidget(right_box, 1)

        wrap = QWidget(); wrap.setLayout(outer)
        self.tab_mapping.setLayout(QVBoxLayout())
        self.tab_mapping.layout().addWidget(wrap)

        self._populate_mapping_list()

    def _build_text(self):
        main_lay = QVBoxLayout()
        
        # ===== 新增：待机闲聊区域（顶部） =====
        idle_section = QGroupBox("待机闲聊")
        idle_section.setStyleSheet("QGroupBox { font-weight: bold; color: #2196F3; }")
        idle_section.setToolTip("桌宠在你空闲时偶尔说一句；间隔在「行为」页的生活提醒里设置。")
        idle_lay = QVBoxLayout()
        
        hint_idle = QLabel("💡 间隔在「行为」页的「待机闲聊间隔」中设置")
        hint_idle.setStyleSheet("color: gray; font-size: 11px;")
        idle_lay.addWidget(hint_idle)
        
        self.idle_chat_list = QListWidget()
        idle_lay.addWidget(self.idle_chat_list)
        
        idle_btn_row = QHBoxLayout()
        self.btn_add_idle_chat = QPushButton("+ 添加闲聊")
        self.btn_del_idle_chat = QPushButton("- 删除选中")
        self.btn_add_idle_chat.clicked.connect(self._add_idle_chat)
        self.btn_del_idle_chat.clicked.connect(self._delete_idle_chat)
        idle_btn_row.addWidget(self.btn_add_idle_chat)
        idle_btn_row.addWidget(self.btn_del_idle_chat)
        idle_btn_row.addStretch()
        idle_lay.addLayout(idle_btn_row)
        
        idle_section.setLayout(idle_lay)
        main_lay.addWidget(idle_section)
        
        # ===== 新增：应用专属文案区域 =====
        app_section = QGroupBox("应用专属文案")
        app_section.setStyleSheet("QGroupBox { font-weight: bold; }")
        app_section.setToolTip("为某个应用或网站设置专属气泡文案。请先在「应用映射」中确保该对象已被识别。")
        app_lay = QVBoxLayout()
        
        # 选择应用/网站（仅针对已有映射对象）
        app_select_row = QHBoxLayout()
        app_select_row.addWidget(QLabel("选择对象："))
        self.cb_app_for_text = QComboBox()
        self.cb_app_for_text.setToolTip("仅列出已经在「应用映射」页中配置过的应用和网站。要新增或删除对象，请前往「应用映射」。")
        self.cb_app_for_text.currentTextChanged.connect(self._load_app_specific_texts)
        app_select_row.addWidget(self.cb_app_for_text)
        
        app_select_row.addStretch()
        app_lay.addLayout(app_select_row)
        
        # 专属文案列表
        self.app_text_list = QListWidget()
        app_lay.addWidget(self.app_text_list)
        
        # 混用通用文案复选框
        self.cb_app_allow_mix = QCheckBox("☑️ 也使用类别通用文案（专属文案+通用文案混合）")
        self.cb_app_allow_mix.setStyleSheet("QCheckBox { color: #2196F3; font-weight: bold; }")
        self.cb_app_allow_mix.setToolTip("勾选后，专属文案与类别通用文案混合随机；不勾选则仅说专属文案。")
        self.cb_app_allow_mix.stateChanged.connect(self._on_app_allow_mix_changed)
        app_lay.addWidget(self.cb_app_allow_mix)
        
        # 操作按钮
        app_btn_row = QHBoxLayout()
        self.btn_add_app_text = QPushButton("+ 添加专属文案")
        self.btn_del_app_text = QPushButton("− 删除选中")
        self.btn_add_app_text.clicked.connect(self._add_app_specific_text)
        self.btn_del_app_text.clicked.connect(self._del_app_specific_text)
        app_btn_row.addWidget(self.btn_add_app_text)
        app_btn_row.addWidget(self.btn_del_app_text)
        app_btn_row.addStretch()
        app_lay.addLayout(app_btn_row)
        
        app_section.setLayout(app_lay)
        main_lay.addWidget(app_section)
        
        # ===== 原有：类别通用文案区域 =====
        cat_section = QGroupBox("类别通用文案")
        cat_section.setStyleSheet("QGroupBox { font-weight: bold; }")
        cat_section.setToolTip("按应用类别（如办公、聊天）设置的通用气泡文案；无专属文案时会从这里选。")
        main_content = QVBoxLayout()
        
        # 类别选择
        cat_row = QHBoxLayout()
        cat_row.addWidget(QLabel("类别："))
        self.cb_text_cat = QComboBox()
        for c in CATEGORIES: 
            self.cb_text_cat.addItem(c)
        self.cb_text_cat.currentTextChanged.connect(self._load_text_cat)
        cat_row.addWidget(self.cb_text_cat)
        cat_row.addStretch()
        main_content.addLayout(cat_row)
        
        # 文案列表
        self.text_list = QListWidget()
        main_content.addWidget(self.text_list)
        
        # 按钮行
        btns = QHBoxLayout()
        self.btn_add_text = QPushButton("新增  Add")
        self.btn_del_text = QPushButton("删除  Delete")
        btn_restore_text = QPushButton("恢复预设文案")
        
        self.btn_add_text.clicked.connect(self._add_text)
        self.btn_del_text.clicked.connect(self._del_text)
        btn_restore_text.clicked.connect(self._restore_text_pool_defaults)
        
        btns.addWidget(self.btn_add_text)
        btns.addWidget(self.btn_del_text)
        btns.addWidget(btn_restore_text)
        btns.addStretch()
        main_content.addLayout(btns)
        
        # 提示
        tip_label = QLabel("💡 提示：双击文案可编辑")
        tip_label.setStyleSheet("color: gray; font-size: 11px;")
        main_content.addWidget(tip_label)
        
        cat_section.setLayout(main_content)
        main_lay.addWidget(cat_section)
        
        # 连接双击事件
        self.idle_chat_list.itemDoubleClicked.connect(self._idle_chat_item_dbl)
        self.app_text_list.itemDoubleClicked.connect(self._app_text_item_dbl)
        self.text_list.itemDoubleClicked.connect(self._text_item_dbl)
        
        self.tab_text.setLayout(main_lay)


    # ---------- Load / Save ----------
    def _load_into_widgets(self):
        self._loading = True
        s = self.bubbles.get("settings", {})
        self.cb_enabled.setChecked(bool(s.get("enabled", True)))
        self.cb_switch_only.setChecked(bool(s.get("trigger_on_app_switch_only", True)))
        self.sp_prob.setValue(float(s.get("trigger_probability", 0.35)))
        self.sp_show.setValue(int(s.get("show_ms", 2600)))
        self.sp_front.setValue(int(s.get("front_stable_ms", 500)))
        self.sp_pending.setValue(int(s.get("max_pending", 1)))

        cd = s.get("cooldown_seconds_by_category", {})
        for cat, sp in self.cooldowns.items():
            sp.setValue(int(cd.get(cat, 600)))

        # quiet mode: if pet has it
        self.cb_quiet.setChecked(bool(getattr(self.pet, "quiet_mode", False)))


        # pet settings
        ps = self.pet_settings if isinstance(self.pet_settings, dict) else {}
        b = ps.get("behavior", {}) if isinstance(ps.get("behavior", {}), dict) else {}
        r = ps.get("reminders", {}) if isinstance(ps.get("reminders", {}), dict) else {}
        slp = ps.get("sleep", {}) if isinstance(ps.get("sleep", {}), dict) else {}

        self.sp_move_speed.setValue(float(b.get("move_speed", 1.5)))
        self.sp_ai_interval.setValue(int(b.get("ai_interval_ms", 2000)))
        self.cb_auto_walk.setChecked(bool(b.get("auto_walk_enabled", True)))
        self.sp_roam_radius.setValue(int(b.get("roam_radius_px", 0)))
        self.sp_edge_margin.setValue(int(b.get("edge_margin_px", 0)))
        self.cb_auto_fall.setChecked(bool(b.get("auto_fall_enabled", True)))

        # sleep
        if hasattr(self, "cb_sleep_enable"):
            self.cb_sleep_enable.setChecked(bool(slp.get("enabled", True)))
            self.sp_sleep_idle_min.setValue(int(slp.get("idle_minutes", 15)))
            self.sp_adrenaline_min.setValue(int(slp.get("adrenaline_minutes", 10)))

        self.cb_water_enable.setChecked(bool(r.get("water_enabled", True)))
        self.sp_water_interval.setValue(int(r.get("water_interval_min", 30)))
        self.cb_move_enable.setChecked(bool(r.get("move_enabled", True)))
        self.sp_move_interval.setValue(int(r.get("move_interval_min", 45)))

        self.sp_start_h.setValue(int(r.get("active_start_h", 9)))
        self.sp_start_m.setValue(int(r.get("active_start_m", 0)))
        self.sp_end_h.setValue(int(r.get("active_end_h", 23)))
        self.sp_end_m.setValue(int(r.get("active_end_m", 30)))
        self.sp_notice_ms.setValue(int(r.get("notice_duration_ms", 3000)))
        self.sp_idle_chat_interval.setValue(int(r.get("idle_chat_interval_min", 10)))

        self._load_text_cat(self.cb_text_cat.currentText())
        
        # 填充应用列表（用于专属文案编辑）
        self._populate_app_list_for_texts()
        if self.cb_app_for_text.count() > 0:
            self._load_app_specific_texts()

        # 加载idle_chat和filters
        self._load_idle_chat_into_widgets()
        self._load_filters_into_widgets()
        self._load_categories_into_widgets()
        self._load_ai_into_widgets()
        self._loading = False

    def _gather_from_widgets(self):
        if "settings" not in self.bubbles:
            self.bubbles["settings"] = {}
        s = self.bubbles["settings"]
        s["enabled"] = self.cb_enabled.isChecked()
        s["trigger_on_app_switch_only"] = self.cb_switch_only.isChecked()
        s["trigger_probability"] = float(self.sp_prob.value())
        s["show_ms"] = int(self.sp_show.value())
        s["front_stable_ms"] = int(self.sp_front.value())
        s["max_pending"] = int(self.sp_pending.value())
        s["cooldown_seconds_by_category"] = {cat:int(sp.value()) for cat, sp in self.cooldowns.items()}

        # pet settings
        if not isinstance(self.pet_settings, dict):
            self.pet_settings = {}
        self.pet_settings.setdefault("version", 1)
        self.pet_settings.setdefault("behavior", {})
        self.pet_settings.setdefault("reminders", {})
        b = self.pet_settings["behavior"]
        r = self.pet_settings["reminders"]

        if "sleep" not in self.pet_settings:
            self.pet_settings["sleep"] = {}
        slp = self.pet_settings["sleep"]

        b["move_speed"] = float(self.sp_move_speed.value())
        b["ai_interval_ms"] = int(self.sp_ai_interval.value())
        b["auto_walk_enabled"] = bool(self.cb_auto_walk.isChecked())
        b["roam_radius_px"] = int(self.sp_roam_radius.value())
        b["edge_margin_px"] = int(self.sp_edge_margin.value())
        b["auto_fall_enabled"] = bool(self.cb_auto_fall.isChecked())

        # sleep
        if hasattr(self, "cb_sleep_enable"):
            slp["enabled"] = bool(self.cb_sleep_enable.isChecked())
            slp["idle_minutes"] = int(self.sp_sleep_idle_min.value())
            slp["adrenaline_minutes"] = int(self.sp_adrenaline_min.value())

        r["water_enabled"] = bool(self.cb_water_enable.isChecked())
        r["water_interval_min"] = int(self.sp_water_interval.value())
        r["move_enabled"] = bool(self.cb_move_enable.isChecked())
        r["move_interval_min"] = int(self.sp_move_interval.value())
        r["active_start_h"] = int(self.sp_start_h.value())
        r["active_start_m"] = int(self.sp_start_m.value())
        r["active_end_h"] = int(self.sp_end_h.value())
        r["active_end_m"] = int(self.sp_end_m.value())
        r["notice_duration_ms"] = int(self.sp_notice_ms.value())
        r["idle_chat_interval_min"] = int(self.sp_idle_chat_interval.value())

        # 收集idle_chat和filters
        self._gather_idle_chat_from_widgets()
        self._gather_filters_from_widgets()
        self._gather_ai_from_widgets()

    def _load_ai_into_widgets(self):
        try:
            self._ai_loading = True
            s = load_ai_settings()
            if hasattr(self, "ai_provider"):
                matched_idx = len(PROVIDER_PRESETS) - 1
                for i, p in enumerate(PROVIDER_PRESETS):
                    if p["base_url"] and p["base_url"] == s.base_url:
                        matched_idx = i
                        break
                self.ai_provider.setCurrentIndex(matched_idx)
            if hasattr(self, "ai_base_url"):
                self.ai_base_url.setText(s.base_url or "")
            if hasattr(self, "ai_api_key"):
                self.ai_api_key.setText(s.api_key or "")
            if hasattr(self, "ai_model"):
                self.ai_model.setText(s.model or "")
            if hasattr(self, "lbl_vision_warn"):
                self.lbl_vision_warn.setVisible(not guess_supports_vision(s.model))
            if hasattr(self, "ai_preset_combo"):
                self._populate_preset_combo()
            if hasattr(self, "ai_system_prompt"):
                self.ai_system_prompt.setPlainText(s.system_prompt or "")
            if hasattr(self, "ai_min_reply"):
                self.ai_min_reply.setValue(int(getattr(s, "reply_min_length", 20)))
            if hasattr(self, "ai_max_bubble"):
                self.ai_max_bubble.setValue(int(getattr(s, "reply_max_length", 80)))
            if hasattr(self, "ai_max_memory"):
                self.ai_max_memory.setValue(int(getattr(s, "max_memory_turns", 5)))
            if hasattr(self, "ai_auto_screenshot"):
                self.ai_auto_screenshot.setValue(int(s.auto_screenshot_interval_min or 0))
            if hasattr(self, "ai_blackbox_keep"):
                self.ai_blackbox_keep.setValue(int(getattr(s, "max_blackbox_logs", 150) or 150))
            # 同步“应用检测气泡”开关（实际写在 bubbles.json.settings.enabled）
            if hasattr(self, "cb_app_bubbles_ai") and hasattr(self, "cb_enabled"):
                self.cb_app_bubbles_ai.setChecked(bool(self.cb_enabled.isChecked()))
            if hasattr(self, "cb_auto_watch_ai") and hasattr(self, "pet"):
                running = bool(getattr(self.pet, "ai_watch_enabled", False))
                self.cb_auto_watch_ai.setChecked(running)
            self._ai_loading = False
        except Exception:
            self._ai_loading = False

    def _gather_ai_from_widgets(self):
        try:
            if not hasattr(self, "ai_base_url"):
                return
            s = AISettings()
            s.base_url = (self.ai_base_url.text() or "").strip() or s.base_url
            s.api_key = (self.ai_api_key.text() or "").strip()
            s.model = (self.ai_model.text() or "").strip() or s.model
            if hasattr(self, "ai_system_prompt"):
                s.system_prompt = (self.ai_system_prompt.toPlainText() or "").strip() or s.system_prompt
            if hasattr(self, "ai_min_reply"):
                s.reply_min_length = self.ai_min_reply.value()
            if hasattr(self, "ai_max_bubble"):
                s.reply_max_length = self.ai_max_bubble.value()
            if hasattr(self, "ai_max_memory"):
                s.max_memory_turns = self.ai_max_memory.value()
            if hasattr(self, "ai_auto_screenshot"):
                s.auto_screenshot_interval_min = self.ai_auto_screenshot.value()
            if hasattr(self, "ai_blackbox_keep"):
                s.max_blackbox_logs = int(self.ai_blackbox_keep.value())
            s.supports_vision = guess_supports_vision(s.model)
            save_ai_settings(s)
        except Exception:
            pass

    def _restore_pet_defaults(self):
        # Restore only pet_settings.json (Behavior + Reminders). Does NOT touch bubbles/app_map/text_pool.
        reply = QMessageBox.question(
            self,
            "确认恢复",
            "将恢复本页设置（移动速度、睡眠、喝水/运动提醒等），仅影响 pet_settings.json，不会改动文案池与应用映射。\n\n是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        defaults = {
            "version": 1,
            "behavior": {
                "move_speed": 1.5,
                "ai_interval_ms": 2000,
                "auto_walk_enabled": True,
                "roam_radius_px": 0,
                "edge_margin_px": 0,
                "auto_fall_enabled": True,
            },
            "sleep": {
                "enabled": True,
                "idle_minutes": 15,
                "adrenaline_minutes": 10,
            },
            "reminders": {
                "water_enabled": True,
                "water_interval_min": 30,
                "move_enabled": True,
                "move_interval_min": 45,
                "active_start_h": 9,
                "active_start_m": 0,
                "active_end_h": 23,
                "active_end_m": 30,
                "notice_duration_ms": 3000,
                "idle_chat_interval_min": 10,
            }
        }
        self.pet_settings = defaults
        try:
            _save_json(self.pet_settings_path, self.pet_settings)
        except Exception:
            pass
        self._load_into_widgets()
        self._dirty = False
        try:
            self.pet.reload_pet_settings()
        except Exception:
            pass
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0, lambda: QMessageBox.information(self, "已恢复", "已恢复默认设置（仅行为与提醒相关，未改动文案与应用映射）"))

    def _restore_rules_values(self):
        """恢复Rules标签页默认数值（不删除用户类别）"""
        from PyQt6.QtCore import QTimer
        
        reply = QMessageBox.question(self, "确认恢复", 
            "确定要恢复规则默认数值吗？\n\n将恢复：\n- 触发概率\n- 显示时长\n- 冷却时间\n等所有规则参数\n\n（不会删除用户添加的类别）",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        
        if reply != QMessageBox.StandardButton.Yes:
            return
        
        # 恢复默认值
        self.sp_prob.setValue(0.60)
        self.sp_show.setValue(2600)
        self.sp_front.setValue(500)
        self.sp_pending.setValue(1)
        
        # 冷却时间默认值
        cooldown_defaults = {
            "browse": 600, "video": 480, "chat": 480, "ai": 360,
            "code": 480, "office": 600, "gamehub": 600, "music": 480
        }
        
        for cat, sp in self.cooldowns.items():
            sp.setValue(cooldown_defaults.get(cat, 600))
        
        QTimer.singleShot(0, lambda: QMessageBox.information(self, "已恢复", "规则数值已恢复为默认设置"))

    def _reset_categories(self):
        """重置类别：删除用户自定义类别，恢复为系统默认"""
        from PyQt6.QtCore import QTimer
        
        reply = QMessageBox.question(self, "确认重置类别",
            "确定要重置类别吗？\n\n这将会：\n✓ 删除所有用户添加的类别\n✓ 删除这些类别的所有文案\n✓ 删除这些类别的应用映射\n✓ 恢复为系统默认 8 个类别\n\n此操作不可撤销！",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        
        if reply != QMessageBox.StandardButton.Yes:
            return
        
        # 恢复默认类别
        global CATEGORIES
        default_categories = ["browse", "video", "chat", "ai", "code", "office", "gamehub", "music"]
        
        # 找出要删除的类别
        to_delete = [cat for cat in CATEGORIES if cat not in default_categories]
        
        if not to_delete:
            QMessageBox.information(self, "无需重置", "当前没有用户自定义类别")
            return
        
        # 删除相关数据
        for cat in to_delete:
            # 删除文案池
            if "text_pool" in self.bubbles and cat in self.bubbles["text_pool"]:
                del self.bubbles["text_pool"][cat]
            
            # 删除隐藏预设
            if "hidden_preset_texts" in self.bubbles and cat in self.bubbles["hidden_preset_texts"]:
                del self.bubbles["hidden_preset_texts"][cat]
            
            # 删除冷却时间
            if "settings" in self.bubbles and "cooldown_seconds_by_category" in self.bubbles["settings"]:
                cd = self.bubbles["settings"]["cooldown_seconds_by_category"]
                if cat in cd:
                    del cd[cat]
            
            # 删除应用映射中的该类别
            if "app_map" in self.bubbles:
                for app_key in list(self.bubbles["app_map"].keys()):
                    if self.bubbles["app_map"][app_key].get("category") == cat:
                        self.bubbles["app_map"][app_key]["category"] = "browse"  # 改为默认类别
        
        # 重置CATEGORIES为默认
        CATEGORIES.clear()
        CATEGORIES.extend(default_categories)
        
        # 保存并刷新界面
        self._save_apply()
        self._rebuild_rules_tab()
        self._refresh_all_category_dropdowns()  # 刷新其他标签页的下拉框
        
        QTimer.singleShot(0, lambda: QMessageBox.information(self, "已重置", f"已删除 {len(to_delete)} 个用户类别，恢复为默认"))


    def _restore_text_pool_defaults(self):
        """恢复Text Pool默认设置（清除隐藏的预设）"""
        from PyQt6.QtCore import QTimer
        
        reply = QMessageBox.question(self, "确认恢复", 
            "确定要恢复文案池默认设置吗？\n\n将会：\n- 恢复所有被删除的预设文案\n- 保留用户添加的自定义文案",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        
        if reply != QMessageBox.StandardButton.Yes:
            return
        
        # 清除隐藏的预设文案
        if "hidden_preset_texts" in self.bubbles:
            del self.bubbles["hidden_preset_texts"]
        
        # 刷新当前类别
        cat = self.cb_text_cat.currentText()
        self._load_text_cat(cat)
        
        QTimer.singleShot(0, lambda: QMessageBox.information(self, "已恢复", "文案池已恢复默认设置\n所有预设文案已找回"))

    def _show_toast(self, text: str, duration_ms: int = 2500):
        """Non-blocking toast notification near the Apply button."""
        if not hasattr(self, "_toast_label"):
            from PyQt6.QtWidgets import QLabel
            self._toast_label = QLabel(self)
            self._toast_label.setStyleSheet(
                "background:#2ecc40; color:white; padding:6px 18px;"
                "border-radius:4px; font-size:13px;"
            )
            self._toast_label.hide()
            self._toast_timer = QTimer()
            self._toast_timer.setSingleShot(True)
            self._toast_timer.timeout.connect(self._toast_label.hide)
        self._toast_label.setText(text)
        self._toast_label.adjustSize()
        x = (self.width() - self._toast_label.width()) // 2
        self._toast_label.move(x, self.height() - 55)
        self._toast_label.raise_()
        self._toast_label.show()
        self._toast_timer.start(duration_ms)

    def _save_apply(self):
        logger.info("用户点击保存按钮，开始应用配置...")
        
        # 保存前记录旧的filters，用于检测变化
        try:
            old_filters_exe = set([x.lower() for x in (_load_json(self.filters_path, {}).get("ignored_exe") or [])])
        except Exception:
            old_filters_exe = set()
        
        # 保存前从配置文件同步应用气泡开关（控制台可能已修改）
        try:
            _fresh = _load_json(self.bubbles_path, {"version": 1, "settings": {}})
            _fresh_enabled = bool((_fresh.get("settings") or {}).get("enabled", True))
            self.cb_enabled.setChecked(_fresh_enabled)
            if hasattr(self, 'cb_app_bubbles_ai'):
                self.cb_app_bubbles_ai.setChecked(_fresh_enabled)
        except Exception:
            pass
        self._gather_from_widgets()
        
        logger.info(f"保存配置文件到: {self.pet_settings_path}")
        _save_json(self.bubbles_path, self.bubbles)
        _save_json(self.appmap_path, self.appmap)
        _save_json(self.pet_settings_path, self.pet_settings)
        _save_json(self.filters_path, self.filters)
        logger.info("配置文件保存成功")

        # apply quiet mode
        try:
            self.pet.quiet_mode = self.cb_quiet.isChecked()
            logger.info(f"安静模式: {self.pet.quiet_mode}")
        except Exception:
            pass

        # ask pet to reload
        try:
            logger.info("通知桌宠重新加载配置...")
            self.pet.reload_activity_config()
            self.pet.reload_pet_settings()
        except Exception as e:
            logger.error(f"重新加载配置失败: {e}")
        
        # 检查filters是否改变，如果改变则清空_recent_apps缓存
        try:
            new_filters_exe = set([x.lower() for x in (self.filters.get("ignored_exe") or [])])
            if old_filters_exe != new_filters_exe:
                logger.info("过滤器已修改，清空应用记录缓存")
                self.pet._recent_apps = {}
        except Exception as e:
            logger.error(f"清空应用缓存失败: {e}")

        # 重置walk计时，避免apply后桌宠walk动画播放但不位移
        try:
            self.pet._walk_until_ms = 0
        except Exception:
            pass

        self._dirty = False
        # 保存后同步控制台的勾选状态
        try:
            console = getattr(self.pet, "_chat_console", None)
            if console is not None:
                # 同步自动巡视
                if hasattr(console, "_sync_auto_watch_from_pet"):
                    console._sync_auto_watch_from_pet()
                # 同步应用气泡
                if hasattr(console, "cb_app_bubbles"):
                    console.cb_app_bubbles.setChecked(
                        bool(getattr(self.pet, "activity_bubbles_enabled", True))
                    )
                # 同步安静模式
                if hasattr(console, "cb_quiet"):
                    console.cb_quiet.setChecked(bool(getattr(self.pet, "quiet_mode", False)))
        except Exception:
            pass
        self._show_toast("设置已保存并应用")

    # ---------- Mapping ----------
    def _populate_mapping_list(self):
        self.list_apps.clear()
        if hasattr(self, "list_sites"):
            self.list_sites.clear()

        # 应用映射列表
        apps = self.appmap.get("apps", [])
        for i, rule in enumerate(apps):
            if not isinstance(rule, dict):
                # Shouldn't happen after normalization, but keep UI robust
                rule = {"name": str(rule), "category": "", "match": {"exe_contains": str(rule), "title_contains": ""}}
            m = rule.get("match", {})
            exe = m.get("exe_contains", "")
            title = (m.get("title_contains") or "").strip()
            cat = rule.get("category", "")
            name = rule.get("name", "")
            tag = cat if cat else "unmapped"
            disp_name = name if name else exe
            if title:
                item = QListWidgetItem(f"[{tag}] {disp_name}  exe~{exe}  | 标题: {title}")
            else:
                item = QListWidgetItem(f"[{tag}] {disp_name}  exe~{exe}  title~{title}")
            item.setData(Qt.ItemDataRole.UserRole, i)
            self.list_apps.addItem(item)

        # 网站规则列表（browser_title_rules / chrome_title_rules），只作为只读展示
        site_rules = self.appmap.get("browser_title_rules") or self.appmap.get("chrome_title_rules", [])
        for j, rule in enumerate(site_rules):
            if not isinstance(rule, dict):
                continue
            name = (rule.get("name") or "").strip()
            if not name:
                continue
            cat = (rule.get("category") or "").strip() or "browse"
            keys = rule.get("contains_any") or []
            kw = ", ".join([k for k in keys if k]) if isinstance(keys, list) else str(keys)
            item = QListWidgetItem(f"[site·{cat}] {name}  kw~{kw}")
            # 使用索引 j 仅在网站列表中使用
            item.setData(Qt.ItemDataRole.UserRole, j)
            if hasattr(self, "list_sites"):
                self.list_sites.addItem(item)

    def _mapping_selected(self, cur, prev):
        if not cur:
            return
        # 切换到 App 编辑模式
        self._mapping_mode = "app"
        self.btn_add.setText(self._btn_add_app_text)
        self.btn_del.setText(self._btn_del_app_text)
        self.btn_block.setVisible(True)

        idx = cur.data(Qt.ItemDataRole.UserRole)
        # 只对 app 规则生效
        if not isinstance(idx, int) or idx < 0:
            return
        apps = self.appmap.get("apps", [])
        if idx >= len(apps):
            return
        rule = apps[idx]
        m = rule.get("match", {})
        self.ed_exe_contains.setText(m.get("exe_contains",""))
        # App 映射不再支持通过 UI 编辑 title_contains，这里仅保留内部值，避免破坏旧配置
        self.ed_title_contains.setText(m.get("title_contains",""))
        self.ed_name.setText(rule.get("name",""))
        cat = rule.get("category","browse")
        self.cb_cat.setCurrentText(cat if cat in CATEGORIES else "browse")

    def _site_selected(self, cur, prev):
        """选中网站规则时，在右侧展示并允许更新网站名称/类别。"""
        del prev  # unused
        if cur is None:
            return

        # 切换到 Site 编辑模式：按钮只保留更新/删除，Block 对网站无意义
        self._mapping_mode = "site"
        self.btn_add.setText(self._btn_add_site_text)
        self.btn_del.setText(self._btn_del_site_text)
        self.btn_block.setVisible(False)

        idx = cur.data(Qt.ItemDataRole.UserRole)
        if not isinstance(idx, int) or idx < 0:
            return

        site_rules = self.appmap.get("browser_title_rules") or self.appmap.get("chrome_title_rules", [])
        if idx >= len(site_rules):
            return
        rule = site_rules[idx]
        # 记录当前选中的 site 索引，供更新/删除使用
        self._selected_site_index = idx
        name = (rule.get("name") or "").strip()
        cat = (rule.get("category") or "").strip()
        keys = rule.get("contains_any") or []
        kw = ", ".join([k for k in keys if k]) if isinstance(keys, list) else str(keys)

        # 在右侧表单中展示网站信息：exe 输入框用于提示关键字，名称和类别可编辑
        self.ed_exe_contains.setText(kw)
        self.ed_name.setText(name)
        if cat in CATEGORIES:
            self.cb_cat.setCurrentText(cat)
        elif self.cb_cat.count() > 0:
            self.cb_cat.setCurrentIndex(0)

    def _add_mapping(self):
        # Site 模式：更新已有网站规则的名称/类别（不新增）
        if getattr(self, "_mapping_mode", "app") == "site":
            idx = getattr(self, "_selected_site_index", -1)
            site_rules = self.appmap.get("browser_title_rules") or self.appmap.get("chrome_title_rules", [])
            if not isinstance(idx, int) or idx < 0 or idx >= len(site_rules):
                return
            rule = site_rules[idx]
            name = (self.ed_name.text() or "").strip() or (rule.get("name") or "")
            cat = self.cb_cat.currentText()
            self._mark_dirty()
            rule["name"] = name
            rule["category"] = cat
            # 关键字暂不在此处编辑，保持来自添加网站弹窗的配置
            self._populate_mapping_list()
            self._populate_app_list_for_texts()
            return

        # App 模式：新增/更新应用映射，仅按 exe 聚合
        # Normalize for stable matching
        exe = (self.ed_exe_contains.text() or "").strip()
        # App 映射仅按 exe 聚合，强制忽略 title 输入
        title = ""
        cat = self.cb_cat.currentText()
        name = (self.ed_name.text() or "").strip() or (exe or cat)

        exe_norm = exe.lower()
        title_norm = title.lower()

        if not exe_norm:
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(0, lambda: QMessageBox.warning(self, "提示", "请填写「exe 包含」。"))
            return

        self._mark_dirty()
        apps = self.appmap.setdefault("apps", [])

        def _norm_rule_match(rule):
            m = (rule or {}).get("match") or {}
            if not isinstance(m, dict):
                return "", ""
            return (m.get("exe_contains") or "").strip().lower(), (m.get("title_contains") or "").strip().lower()

        # 1) Exact match update (case-insensitive)
        for rule in apps:
            if not isinstance(rule, dict):
                continue
            r_exe, r_title = _norm_rule_match(rule)
            if r_exe == exe_norm and r_title == title_norm:
                rule["category"] = cat
                rule["name"] = name
                rule.setdefault("match", {})
                rule["match"]["exe_contains"] = exe_norm
                rule["match"]["title_contains"] = title_norm
                self._cleanup_placeholders(exe_norm)
                self._populate_mapping_list()
                self._populate_app_list_for_texts()
                return

        # 2) If mapping by exe only, try to upgrade an existing unmapped placeholder
        if exe_norm and not title_norm:
            for rule in apps:
                if not isinstance(rule, dict):
                    continue
                r_exe, r_title = _norm_rule_match(rule)
                if r_exe == exe_norm and (r_title == "" or r_title is None):
                    if not (rule.get("category") or "").strip():
                        rule["category"] = cat
                        rule["name"] = name
                        rule.setdefault("match", {})
                        rule["match"]["exe_contains"] = exe_norm
                        rule["match"]["title_contains"] = ""
                        self._populate_mapping_list()
                        self._populate_app_list_for_texts()
                        return

        # 3) Otherwise append new rule
        apps.append({"match": {"exe_contains": exe_norm, "title_contains": title_norm}, "category": cat, "name": name})

        # 4) Cleanup: drop exact duplicates; prefer mapped over unmapped
        best = {}  # (exe,title) -> rule
        for rule in apps:
            if not isinstance(rule, dict):
                continue
            r_exe, r_title = _norm_rule_match(rule)
            key = (r_exe, r_title)
            prev = best.get(key)
            if prev is None:
                best[key] = rule
                continue
            prev_cat = (prev.get("category") or "").strip()
            cur_cat = (rule.get("category") or "").strip()
            if (not prev_cat) and cur_cat:
                best[key] = rule

        cleaned = list(best.values())

        mapped_exes = set()
        for rule in cleaned:
            r_exe, r_title = _norm_rule_match(rule)
            if (rule.get("category") or "").strip() and r_title == "":
                mapped_exes.add(r_exe)

        final = []
        for rule in cleaned:
            r_exe, r_title = _norm_rule_match(rule)
            if r_exe in mapped_exes and not (rule.get("category") or "").strip() and r_title == "":
                continue
            final.append(rule)

        self.appmap["apps"] = final
        self._populate_mapping_list()
        
        # 刷新Text Pool的应用下拉框
        self._populate_app_list_for_texts()

    
    def _cleanup_placeholders(self, exe_norm: str):
        # Remove safest placeholder rules for this exe: category empty AND title_contains empty.
        try:
            apps = self.appmap.get("apps", [])
            if not isinstance(apps, list):
                return
            final = []
            for rule in apps:
                if not isinstance(rule, dict):
                    continue
                m = rule.get("match") or {}
                if not isinstance(m, dict):
                    final.append(rule); continue
                r_exe = (m.get("exe_contains") or "").strip().lower()
                r_title = (m.get("title_contains") or "").strip().lower()
                cat = (rule.get("category") or "").strip()
                # placeholder: same exe, empty cat, empty title match
                if r_exe == exe_norm and (not cat) and (not r_title):
                    continue
                final.append(rule)
            self.appmap["apps"] = final
        except Exception:
            pass

    def _del_mapping(self):
        # Site 模式：删除选中的网站规则
        if getattr(self, "_mapping_mode", "app") == "site":
            cur = self.list_sites.currentItem()
            if not cur:
                return
            idx = cur.data(Qt.ItemDataRole.UserRole)
            site_rules = self.appmap.get("browser_title_rules") or self.appmap.get("chrome_title_rules", [])
            if not isinstance(idx, int) or idx < 0 or idx >= len(site_rules):
                return
            rule = site_rules[idx]
            name = (rule.get("name") or "").strip()
            self._mark_dirty()
            # 从规则列表删除
            site_rules.pop(idx)
            # 删除对应的专属文案（如果有）
            app_specific = self.bubbles.get("app_specific", {})
            if name in app_specific:
                del app_specific[name]
            self._populate_mapping_list()
            self._populate_app_list_for_texts()
            return

        # App 模式：删除应用映射
        cur = self.list_apps.currentItem()
        if not cur:
            return
        idx = cur.data(Qt.ItemDataRole.UserRole)
        apps = self.appmap.get("apps", [])
        if 0 <= idx < len(apps):
            self._mark_dirty()
            # 获取应用名称
            app_name = apps[idx].get("name", "")
            
            # 删除app_map中的映射
            apps.pop(idx)
            
            # 同步删除app_specific中的专属文案
            if app_name:
                app_specific = self.bubbles.get("app_specific", {})
                if app_name in app_specific:
                    del app_specific[app_name]
            
        self._populate_mapping_list()
        # 刷新应用专属文案下拉框
        self._populate_app_list_for_texts()

    def _block_mapping(self):
        """将选中的应用添加到过滤器"""
        # 网站规则不支持 Block，避免误操作
        if getattr(self, "_mapping_mode", "app") != "app":
            QMessageBox.information(self, "提示", "网站规则暂不支持在此屏蔽，请在过滤器或网站设置中调整。")
            return
        cur = self.list_apps.currentItem()
        if not cur:
            QMessageBox.information(self, "未选中", "请先选择要屏蔽的应用")
            return
        
        idx = cur.data(Qt.ItemDataRole.UserRole)
        apps = self.appmap.get("apps", [])
        if not (0 <= idx < len(apps)):
            return
        
        rule = apps[idx]
        exe = rule.get("match", {}).get("exe_contains", "")
        
        if not exe:
            QMessageBox.warning(self, "无法屏蔽", "无法获取应用的 exe 名称")
            return

        self._mark_dirty()
        # 添加到filters
        ignored_exe = self.filters.setdefault("ignored_exe", [])
        exe_lower = exe.lower().strip()
        
        # 检查是否已存在
        if exe_lower in [x.lower() for x in ignored_exe]:
            QMessageBox.information(self, "已存在", f"{exe} 已在过滤器中")
            return
        
        # 添加
        ignored_exe.append(exe_lower)
        
        # 保存filters.json
        try:
            _save_json(self.filters_path, self.filters)
            logger.info(f"将 {exe} 添加到过滤器")
        except Exception as e:
            QMessageBox.warning(self, "保存失败", f"无法保存过滤器配置：{e}")
            return
        
        # 从appmap中移除
        app_name = apps[idx].get("name", "")
        apps.pop(idx)
        
        # 同步删除app_specific中的专属文案
        if app_name:
            app_specific = self.bubbles.get("app_specific", {})
            if app_name in app_specific:
                del app_specific[app_name]
        
        # 刷新列表
        self._populate_mapping_list()
        self._populate_app_list_for_texts()
        
        # 刷新Filters标签页显示
        self._load_filters_into_widgets()
        
        # 清空应用缓存（让过滤器立刻生效）
        try:
            self.pet._recent_apps = {}
        except Exception:
            pass
        
        # 提示用户
        QMessageBox.information(self, "已屏蔽", 
            f"已将 {exe} 添加到过滤器\n\n"
            f"下次一键抓取时将自动忽略此应用\n"
            f"您可以在「过滤器」标签页管理所有过滤规则")


    def _refresh_detected(self):
        """从桌宠获取最近前台应用，合并进 app 映射列表，并过滤掉明显无用的进程。"""
        recent = []
        try:
            if hasattr(self.pet, "get_recent_apps"):
                recent = self.pet.get_recent_apps(limit=50) or []
        except Exception:
            recent = []

        if not isinstance(recent, list):
            recent = []

        apps = self.appmap.setdefault("apps", [])

        # 使用内存中的filters数据（用户已经修改过的）
        ignored_exe = set([x.lower().strip() for x in (self.filters.get("ignored_exe") or []) if isinstance(x, str)])
        ignored_title_keywords = [x.lower().strip() for x in (self.filters.get("ignored_title_keywords") or []) if isinstance(x, str)]

        # 现有规则里所有 exe 关键词：用于更聪明的去重（避免同一个应用反复刷出 unmapped）
        existing_exe_keywords = set()
        existing_exact_keys = set()
        for rule in apps:
            if not isinstance(rule, dict):
                continue
            m = rule.get("match") or {}
            if not isinstance(m, dict):
                continue
            exe_k = (m.get("exe_contains") or "").lower().strip()
            title_k = (m.get("title_contains") or "").lower().strip()
            if exe_k:
                existing_exe_keywords.add(exe_k)
            existing_exact_keys.add((exe_k, title_k))

        def _skip_entry(entry: dict) -> bool:
            exe = (entry.get("exe") or "").lower().strip()
            title = (entry.get("title") or "").strip()
            if not exe:
                return True

            # 使用filters.json里的过滤列表（不硬编码）
            if exe in ignored_exe:
                return True

            t_l = title.lower()
            for kw in ignored_title_keywords:
                if kw and kw in t_l:
                    return True

            # 自己的设置窗口跳过
            if "desktop pet settings" in t_l:
                return True

            # 标题为空且看起来像系统壳：也不要
            if not title and exe.endswith("host.exe"):
                return True

            return False

        def _already_mapped(exe_l: str) -> bool:
            # 只要已有规则的 exe_contains 能匹配到当前 exe，就认为已存在（app 级别，不区分子窗口）
            for kw in existing_exe_keywords:
                if not kw:
                    continue
                if kw in exe_l or exe_l in kw:
                    return True
            return False

        count_before = len(apps)
        # 把新发现的前台应用合并成“按 exe 聚合的 app 级规则”
        for entry in recent:
            if not isinstance(entry, dict):
                continue
            if _skip_entry(entry):
                continue

            exe = (entry.get("exe") or "").strip()
            title = (entry.get("title") or "").strip()
            exe_l = exe.lower()

            # 已经有任一使用该 exe 的规则：不再重复生成占位
            if _already_mapped(exe_l):
                continue

            sig = (exe_l, "")
            if sig in existing_exact_keys:
                continue

            # 浏览器特殊处理：用简洁名字，不用完整title
            browser_names = {
                "chrome.exe": ("Chrome", "browse"),
                "msedge.exe": ("Microsoft Edge", "browse"),
                "firefox.exe": ("Firefox", "browse"),
                "iexplore.exe": ("Internet Explorer", "browse"),
                "brave.exe": ("Brave", "browse"),
                "opera.exe": ("Opera", "browse"),
                "vivaldi.exe": ("Vivaldi", "browse")
            }
            
            if exe_l in browser_names:
                name, category = browser_names[exe_l]
            else:
                # 非浏览器应用：默认用 exe 名（不包含路径和扩展名）作为应用名，避免绑定到某个具体窗口标题
                base = exe
                try:
                    base = os.path.splitext(os.path.basename(exe))[0]
                except Exception:
                    pass
                name = base or exe_l
                category = ""

            rule = {
                "name": name,
                "category": category,
                "match": {
                    "exe_contains": exe_l,
                    "title_contains": ""
                }
            }
            apps.append(rule)
            existing_exe_keywords.add(exe_l)
            existing_exact_keys.add(sig)

        # 更新左侧列表
        self._populate_mapping_list()
        
        # 同时刷新Text Pool的应用下拉框
        self._populate_app_list_for_texts()

        added_count = len(apps) - count_before
        if added_count > 0:
            self._mark_dirty()
        
        from PyQt6.QtCore import QTimer
        if added_count == 0:
            QTimer.singleShot(0, lambda: QMessageBox.information(self, "抓取完成", "未检测到新应用。\n\n请先打开要识别的应用并让其处于前台，再点「自动抓取应用」试一次。"))
        else:
            msg = f"✅ 已抓取 {added_count} 个新应用\n\n"
            msg += "💡 提示：浏览器里的网站（B站、ChatGPT 等）无法自动抓取，请点击「+ 添加网站」手动添加。"
            QTimer.singleShot(0, lambda: QMessageBox.information(self, "抓取完成", msg))


    def _get_default_category_pool(self, cat: str):
        """获取默认的category_pool文案"""
        defaults = {
            "browse": ["又开始翻资料了。", "别刷太久，眼睛会累。"],
            "video": ["这个看起来挺上头。", "我也想一起看。"],
            "chat": ["要找人说话吗。", "聊完记得回来。"],
            "ai": ["嗯？又来问问题啦。", "继续聊。"],
            "code": ["慢慢来，能解决。", "卡住就换个思路。"],
            "office": ["这东西最耗耐心。", "做完就收工。"],
            "gamehub": ["开玩开玩。", "我也想玩。"],
            "music": ["给自己配点背景音乐。", "听歌的时候，心情会不会好一点。"]
        }
        return defaults.get(cat, [])
    
    def _get_default_category_templates(self, cat: str):
        """获取默认的category_templates文案（带{app_name}占位符）"""
        defaults = {
            "chat": ["{app_name} 都开了，要找人说话吗。", "嗯？{app_name}……有人在等消息？"],
            "video": ["在 {app_name} 看什么呢。", "{app_name} 打开了，准备放松一下？"],
            "ai": ["{app_name} 打开了，又要问问题啦。", "嗯哼，{app_name} 时间。"],
            "code": ["{app_name} 打开了，开始敲代码。", "{app_name}：今天别熬太晚。"],
            "office": ["{app_name} 打开了，开始干活。", "这看着像正经工作，我先乖点。"],
            "browse": ["在翻网页呢。", "看什么呢，我也想知道。"],
            "gamehub": ["{app_name} 打开了，准备开玩？", "我也想凑热闹。"],
            "music": ["{app_name} 打开了，今天的BGM选好了。", "一边听 {app_name} 一边忙，感觉会好一点。"]
        }
        return defaults.get(cat, [])


    def _load_text_cat(self, cat: str):
        """加载类别文案（合并预设+用户自定义，都可删改）"""
        self.text_list.clear()
        
        # 获取隐藏的预设文案
        hidden = self.bubbles.get("hidden_preset_texts", {}).get(cat, [])
        
        # 1. 加载预设category_pool文案（过滤已隐藏的）
        default_texts = self._get_default_category_pool(cat)
        for text in default_texts:
            if text in hidden:
                continue  # 跳过已隐藏的
            item = QListWidgetItem(f"{text} (来自预设)")
            item.setData(Qt.ItemDataRole.UserRole, "preset")
            self.text_list.addItem(item)
        
        # 2. 加载预设category_templates文案（带{app_name}占位符）
        template_texts = self._get_default_category_templates(cat)
        for text in template_texts:
            if text in hidden:
                continue  # 跳过已隐藏的
            item = QListWidgetItem(f"{text} (来自预设)")
            item.setData(Qt.ItemDataRole.UserRole, "preset")
            self.text_list.addItem(item)
        
        # 3. 加载用户自定义文案
        pool = self.bubbles.setdefault("text_pool", {})
        user_texts = pool.get(cat, [])
        for text in user_texts:
            item = QListWidgetItem(f"{text} (用户添加)")
            item.setData(Qt.ItemDataRole.UserRole, "custom")
            self.text_list.addItem(item)




    # ===== 应用专属文案方法 =====
    def _populate_app_list_for_texts(self):
        """填充应用下拉框，区分应用和网站"""
        self.cb_app_for_text.clear()
        
        items = []  # (显示文本, 实际名字)
        mapped_names = set()
        added_names_no_title = set()  # 无 title 时同名只显示一条
        
        # 1. 从apps读取（应用）：有 title_contains 时单独一项并标出标题，便于同应用多规则区分
        for rule in self.appmap.get("apps", []):
            if not isinstance(rule, dict):
                continue
            name = (rule.get("name") or "").strip()
            if not name:
                continue
            m = rule.get("match") or {}
            title_kw = (m.get("title_contains") or "").strip()
            cat = (rule.get("category") or "").strip()
            if cat:
                base_label = f"[应用·{cat}] {name}"
            else:
                base_label = f"[应用·未分类] {name}"
            if title_kw:
                label = f"{base_label} (标题: {title_kw})"
                actual_name = name + "|" + title_kw
                items.append((label, actual_name))
                mapped_names.add(actual_name)
                mapped_names.add(name)
            else:
                if name in added_names_no_title:
                    continue
                added_names_no_title.add(name)
                label = base_label
                actual_name = name
                items.append((label, actual_name))
                mapped_names.add(name)
        
        # 2. 从browser_title_rules读取（网站）
        rules = self.appmap.get("browser_title_rules") or self.appmap.get("chrome_title_rules", [])
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            name = (rule.get("name") or "").strip()
            if not name:
                continue
            cat = (rule.get("category") or "").strip()
            if cat:
                label = f"[网站·{cat}] {name}"
            else:
                label = f"[网站] {name}"
            items.append((label, name))
            mapped_names.add(name)
        
        # 3. 从app_specific读取“未映射”的名字（历史遗留 / 高级用法）
        app_specific = self.bubbles.get("app_specific", {})
        for name in app_specific.keys():
            if name not in mapped_names:
                items.append((f"[未映射] {name}", name))
        
        # 排序并添加
        items.sort()
        for display_text, actual_name in items:
            self.cb_app_for_text.addItem(display_text, actual_name)
    
    def _load_app_specific_texts(self):
        """加载选中应用的专属文案"""
        self.app_text_list.clear()
        
        # 从UserRole读取实际名字
        app_name = self.cb_app_for_text.currentData()
        display = self.cb_app_for_text.currentText() or ""
        if not app_name and display:
            app_name = self._parse_app_name_from_display(display)
        if not app_name:
            self.cb_app_allow_mix.setEnabled(False)
            self.cb_app_allow_mix.setChecked(False)
            return
        
        app_specific = self.bubbles.get("app_specific", {})
        texts = app_specific.get(app_name, [])
        
        for text in texts:
            self.app_text_list.addItem(QListWidgetItem(text))
        
        # 读取混用通用文案设置
        app_allow_mix = self.bubbles.get("app_allow_mix_general", {})
        allow_mix = app_allow_mix.get(app_name, False)
        
        # 未映射对象：允许编辑文案，但提示“运行时可能不会触发”
        is_orphan = display.startswith("[未映射]")
        
        # 如果有专属文案，启用复选框；否则禁用（未映射对象同样生效，只是匹配依赖高级映射）
        if texts and (not is_orphan):
            self.cb_app_allow_mix.setEnabled(True)
            self.cb_app_allow_mix.setChecked(allow_mix)
        else:
            self.cb_app_allow_mix.setEnabled(False)
            self.cb_app_allow_mix.setChecked(False)
    
    def _add_app_specific_text(self):
        """添加应用专属文案"""
        from PyQt6.QtWidgets import QInputDialog
        
        app_name = self.cb_app_for_text.currentData()
        if not app_name:
            text = self.cb_app_for_text.currentText()
            if not text:
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(0, lambda: QMessageBox.warning(self, "提示", "请先选择一个对象"))
                return
            app_name = self._parse_app_name_from_display(text)
        
        text, ok = QInputDialog.getText(self, "添加专属文案", f"为 {app_name} 添加文案:")
        if ok and text.strip():
            self._mark_dirty()
            app_specific = self.bubbles.setdefault("app_specific", {})
            app_specific.setdefault(app_name, []).append(text.strip())
            self._load_app_specific_texts()
    
    def _parse_app_name_from_display(self, display: str) -> str:
        """从下拉显示文本解析出 app_name（含复合 key name|title）"""
        if not display:
            return ""
        for prefix in ("[应用·未分类] ", "[应用·", "[网站·", "[网站] ", "[未映射] "):
            if display.startswith(prefix):
                rest = display.split("] ", 1)[-1] if "] " in display else display.replace(prefix, "")
                if " (标题: " in rest and rest.rstrip().endswith(")"):
                    name_part, _, tail = rest.partition(" (标题: ")
                    title_part = tail[:-1].strip() if tail else ""
                    return (name_part.strip() + "|" + title_part) if title_part else name_part.strip()
                return rest.strip()
        return display

    def _on_app_allow_mix_changed(self, state):
        """混用通用文案复选框状态改变"""
        app_name = self.cb_app_for_text.currentData()
        if not app_name:
            text = self.cb_app_for_text.currentText()
            if not text:
                return
            app_name = self._parse_app_name_from_display(text)
        
        self._mark_dirty()
        # 保存到bubbles
        app_allow_mix = self.bubbles.setdefault("app_allow_mix_general", {})
        app_allow_mix[app_name] = self.cb_app_allow_mix.isChecked()
    
    def _del_app_specific_text(self):
        """删除选中的应用专属文案"""
        item = self.app_text_list.currentItem()
        if not item:
            return
        
        app_name = self.cb_app_for_text.currentData()
        if not app_name:
            app_name = self._parse_app_name_from_display(self.cb_app_for_text.currentText() or "")
        
        text = item.text()
        
        app_specific = self.bubbles.get("app_specific", {})
        if app_name in app_specific and text in app_specific[app_name]:
            self._mark_dirty()
            app_specific[app_name].remove(text)
            self._load_app_specific_texts()

    def _delete_current_object(self):
        """保留占位：原先用于从文案页删除应用/网站映射。

        现在新增/删除对象统一在「应用映射」页进行，此方法不再实际执行删除逻辑，
        仅作为向后兼容的空实现，避免旧配置或信号引用报错。
        """
        return

    def _add_website_dialog(self):
        """添加网站识别对话框（带智能检测）"""
        dialog = QDialog(self)
        dialog.setWindowTitle("添加网站识别")
        dialog.setMinimumWidth(500)
        
        lay = QVBoxLayout()
        
        tip = QLabel("在浏览器打开目标网页后，点击下方按钮自动识别；无需切换窗口。")
        tip.setStyleSheet("color: gray; font-size: 12px;")
        tip.setToolTip("先打开要添加的网站，再点「检测浏览器窗口」；会从最近访问的窗口自动识别。")
        lay.addWidget(tip)
        
        # 检测按钮
        btn_detect = QPushButton("🔍 检测浏览器窗口")
        btn_detect.setStyleSheet("QPushButton { font-size: 14px; padding: 10px; background-color: #4CAF50; color: white; font-weight: bold; } QPushButton:hover { background-color: #45a049; }")
        lay.addWidget(btn_detect)
        
        # 检测结果区域
        result_group = QGroupBox("检测结果")
        result_lay = QVBoxLayout()
        
        label_browser = QLabel("浏览器：未检测")
        label_browser.setStyleSheet("color: gray;")
        result_lay.addWidget(label_browser)
        
        label_title = QLabel("窗口标题：未检测")
        label_title.setStyleSheet("color: gray;")
        label_title.setWordWrap(True)
        label_title.setWordWrap(True)
        result_lay.addWidget(label_title)
        
        label_suggest = QLabel("建议关键词：未检测")
        label_suggest.setStyleSheet("color: gray;")
        result_lay.addWidget(label_suggest)
        
        result_group.setLayout(result_lay)
        lay.addWidget(result_group)
        
        # 分隔线
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        lay.addWidget(line)
        
        # 表单
        form = QFormLayout()
        
        ed_name = QLineEdit()
        ed_name.setPlaceholderText("例如: Claude")
        form.addRow("网站名称:", ed_name)
        
        ed_keywords = QLineEdit()
        ed_keywords.setPlaceholderText("点击上方检测按钮自动填写")
        form.addRow("关键词:", ed_keywords)
        
        cb_category = QComboBox()
        categories = ["browse", "video", "ai", "chat", "code", "office", "music", "gamehub"]
        cb_category.addItems(categories)
        form.addRow("分类:", cb_category)
        
        lay.addLayout(form)
        
        # 检测按钮功能
        def detect_window():
            """智能检测：从最近窗口找浏览器（无需切换窗口）"""
            try:
                browser_exes = {
                    "chrome.exe": "Chrome",
                    "msedge.exe": "Microsoft Edge",
                    "firefox.exe": "Firefox",
                    "iexplore.exe": "Internet Explorer",
                    "brave.exe": "Brave",
                    "opera.exe": "Opera",
                    "vivaldi.exe": "Vivaldi"
                }
                
                detected_browser = None
                detected_title = None
                
                # 从最近窗口找浏览器（忽略桌宠自己）
                if hasattr(self.pet, '_recent_apps'):
                    recent = self.pet._recent_apps or {}
                    
                    # 按时间倒序
                    sorted_apps = sorted(
                        recent.items(),
                        key=lambda x: x[1].get('last_seen_ms', 0),
                        reverse=True
                    )
                    
                    for exe_key, info in sorted_apps:
                        exe = info.get('exe', '').lower()
                        title = info.get('title', '')
                        
                        # 跳过桌宠自己
                        if exe in ['python.exe', 'pythonw.exe']:
                            continue
                        
                        # 跳过本程序的设置窗口
                        if '桌宠设置' in title or 'desktop pet settings' in title.lower():
                            continue
                        
                        # 找到浏览器
                        if exe in browser_exes:
                            detected_browser = exe
                            detected_title = title
                            break
                
                # 检查结果
                if not detected_browser or not detected_title:
                    from PyQt6.QtCore import QTimer
                    QTimer.singleShot(0, lambda: QMessageBox.warning(dialog, "检测失败", 
                        "未检测到浏览器窗口\n\n"
                        "💡 请确保：\n"
                        "1. 已在浏览器打开目标网站\n"
                        "2. 网站是最近访问的（在最近1分钟内）\n\n"
                        "如仍无法检测，请尝试：\n"
                        "- 切换到网站窗口再切回来\n"
                        "- 或手动填写网站名和关键词"))
                    return
                
                # 显示检测结果
                browser_name = browser_exes[detected_browser]
                label_browser.setText(f"浏览器：{browser_name} ✅")
                label_browser.setStyleSheet("color: green; font-weight: bold;")
                
                label_title.setText(f"窗口标题：{detected_title}")
                label_title.setStyleSheet("color: black;")
                
                # 智能提取关键词
                keywords = extract_keywords(detected_title)
                if keywords:
                    label_suggest.setText(f"建议关键词：{', '.join(keywords)} ✅")
                    label_suggest.setStyleSheet("color: #2196F3; font-weight: bold;")
                    
                    # 自动填充第一个关键词
                    ed_keywords.setText(keywords[0])
                    
                    # 尝试提取网站名
                    suggested_name = suggest_name(detected_title)
                    if suggested_name and not ed_name.text():
                        ed_name.setText(suggested_name)
                else:
                    label_suggest.setText("建议关键词：无法提取，请手动填写")
                    label_suggest.setStyleSheet("color: orange;")
                
            except Exception as e:
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(0, lambda: QMessageBox.warning(dialog, "错误", f"检测失败: {e}"))
        
        def extract_keywords(title):
            """从标题智能提取关键词"""
            # 常见分隔符
            separators = [" - ", " | ", "–", "—", "·", " – ", " — "]
            
            # 分割标题
            parts = [title]
            for sep in separators:
                new_parts = []
                for part in parts:
                    new_parts.extend(part.split(sep))
                parts = new_parts
            
            # 清理和过滤
            keywords = []
            for part in parts:
                part = part.strip().lower()
                # 过滤掉浏览器名称
                if part in ["google chrome", "microsoft edge", "firefox", "internet explorer", "chrome", "edge"]:
                    continue
                # 过滤掉太短的词（但保留中文）
                if len(part) >= 3 or any('\u4e00' <= c <= '\u9fff' for c in part):
                    keywords.append(part)
            
            return keywords[:3]  # 返回前3个关键词
        
        def suggest_name(title):
            """从标题建议网站名"""
            # 常见模式："内容 - 网站名"
            if " - " in title:
                parts = title.split(" - ")
                # 取最后一个非浏览器名的部分
                for part in reversed(parts):
                    part = part.strip()
                    if part.lower() not in ["google chrome", "microsoft edge", "firefox", "chrome", "edge"]:
                        return part
            
            # 直接返回第一个词
            words = title.split()
            return words[0] if words else ""
        
        btn_detect.clicked.connect(detect_window)
        
        # 保存按钮
        btn_row = QHBoxLayout()
        btn_ok = QPushButton("确定添加")
        btn_cancel = QPushButton("取消")
        
        def save_website():
            name = ed_name.text().strip()
            keywords_text = ed_keywords.text().strip()
            category = cb_category.currentText()
            
            if not name or not keywords_text:
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(0, lambda: QMessageBox.warning(dialog, "错误", "请填写网站名称和关键词"))
                return
            
            # 添加到browser_title_rules
            rule = {
                "name": name,
                "category": category,
                "contains_any": [keywords_text]
            }
            
            # 优先使用browser_title_rules
            if "browser_title_rules" not in self.appmap:
                self.appmap["browser_title_rules"] = []
            
            self.appmap["browser_title_rules"].append(rule)

            # 刷新映射和专属文案下拉
            try:
                self._populate_mapping_list()
            except Exception:
                pass
            self._populate_app_list_for_texts()
            
            # 自动选中新添加的网站
            for i in range(self.cb_app_for_text.count()):
                if self.cb_app_for_text.itemData(i) == name:
                    self.cb_app_for_text.setCurrentIndex(i)
                    break
            
            dialog.accept()
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(0, lambda: QMessageBox.information(self, "成功", f"✅ 已添加网站: {name}\n\n现在可以给它添加专属文案了！"))
        
        btn_ok.clicked.connect(save_website)
        btn_cancel.clicked.connect(dialog.reject)
        
        btn_row.addStretch()
        btn_row.addWidget(btn_ok)
        btn_row.addWidget(btn_cancel)
        lay.addLayout(btn_row)
        
        dialog.setLayout(lay)
        dialog.exec()


    def _text_item_dbl(self, item):
        """双击文案时弹窗编辑"""
        if not item:
            return
        
        cat = self.cb_text_cat.currentText()
        item_type = item.data(Qt.ItemDataRole.UserRole)
        
        # 提取当前文案内容
        old_text = item.text().replace(" (来自预设)", "").replace(" (用户添加)", "")
        
        # 弹窗输入
        new_text, ok = QInputDialog.getText(
            self, 
            "编辑文案", 
            "请输入新的文案内容：",
            QLineEdit.EchoMode.Normal,
            old_text
        )
        
        if not ok or not new_text.strip():
            return
        
        new_text = new_text.strip()
        
        # 如果没改，直接返回
        if new_text == old_text:
            return
        
        if item_type == "preset":
            # 编辑预设：隐藏原预设，添加为自定义
            hidden = self.bubbles.setdefault("hidden_preset_texts", {})
            hidden.setdefault(cat, [])
            if old_text not in hidden[cat]:
                hidden[cat].append(old_text)
            
            # 添加为自定义
            pool = self.bubbles.setdefault("text_pool", {})
            pool.setdefault(cat, [])
            if new_text not in pool[cat]:
                pool[cat].append(new_text)
        else:
            # 编辑自定义：直接更新
            pool = self.bubbles.setdefault("text_pool", {})
            pool.setdefault(cat, [])
            
            if old_text in pool[cat]:
                idx = pool[cat].index(old_text)
                pool[cat][idx] = new_text
        
        # 刷新列表
        self._load_text_cat(cat)
        
        # 自动选中编辑后的文案
        for i in range(self.text_list.count()):
            list_item = self.text_list.item(i)
            if new_text in list_item.text():
                self.text_list.setCurrentItem(list_item)
                break

    def _idle_chat_item_dbl(self, item):
        """双击闲聊文案时弹窗编辑"""
        if not item:
            return
        
        old_text = item.text()
        
        # 弹窗输入
        new_text, ok = QInputDialog.getText(
            self, 
            "编辑闲聊文案", 
            "请输入新的文案内容：",
            QLineEdit.EchoMode.Normal,
            old_text
        )
        
        if not ok or not new_text.strip():
            return
        
        new_text = new_text.strip()
        
        # 如果没改，直接返回
        if new_text == old_text:
            return
        
        # 获取当前行号
        row = self.idle_chat_list.row(item)
        
        # 更新列表显示
        item.setText(new_text)
        
        # 自动选中
        self.idle_chat_list.setCurrentRow(row)

    def _app_text_item_dbl(self, item):
        """双击应用专属文案时弹窗编辑"""
        if not item:
            return
        
        # 提取文案内容（去掉标记）
        old_text = item.text().replace(" (来自预设)", "").replace(" (用户添加)", "")
        
        # 弹窗输入
        new_text, ok = QInputDialog.getText(
            self, 
            "编辑专属文案", 
            "请输入新的文案内容：",
            QLineEdit.EchoMode.Normal,
            old_text
        )
        
        if not ok or not new_text.strip():
            return
        
        new_text = new_text.strip()
        
        # 如果没改，直接返回
        if new_text == old_text:
            return
        
        # 获取当前选择的应用
        app_key = self.cb_app_for_text.currentText()
        if not app_key:
            return
        
        # 获取数据
        item_type = item.data(Qt.ItemDataRole.UserRole)
        app_specific = self.bubbles.setdefault("app_specific", {})
        
        if item_type == "preset":
            # 预设文案：隐藏原预设，添加为自定义
            hidden = self.bubbles.setdefault("hidden_app_preset_texts", {})
            hidden.setdefault(app_key, [])
            if old_text not in hidden[app_key]:
                hidden[app_key].append(old_text)
            
            # 添加为自定义
            app_specific.setdefault(app_key, [])
            if new_text not in app_specific[app_key]:
                app_specific[app_key].append(new_text)
        else:
            # 自定义文案：直接更新
            app_specific.setdefault(app_key, [])
            if old_text in app_specific[app_key]:
                idx = app_specific[app_key].index(old_text)
                app_specific[app_key][idx] = new_text
        
        # 刷新列表
        self._load_app_specific_texts()
        
        # 自动选中编辑后的文案
        for i in range(self.app_text_list.count()):
            list_item = self.app_text_list.item(i)
            if new_text in list_item.text():
                self.app_text_list.setCurrentItem(list_item)
                break



    def _add_text(self):
        """添加自定义文案"""
        cat = self.cb_text_cat.currentText()
        pool = self.bubbles.setdefault("text_pool", {})
        pool.setdefault(cat, [])
        pool[cat].append("（新文案）")
        self._load_text_cat(cat)
        
        # 自动选中新添加的文案
        for i in range(self.text_list.count() - 1, -1, -1):
            item = self.text_list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == "custom":
                self.text_list.setCurrentItem(item)
                break


    def _del_text(self):
        """删除文案（预设和自定义都可以删除）"""
        cat = self.cb_text_cat.currentText()
        item = self.text_list.currentItem()
        
        if not item:
            return
        
        # 获取类型
        item_type = item.data(Qt.ItemDataRole.UserRole)
        
        # 提取文案内容（去掉标记）
        text = item.text().replace(" (来自预设)", "").replace(" (用户添加)", "")
        
        if item_type == "preset":
            # 删除预设：添加到隐藏列表
            hidden = self.bubbles.setdefault("hidden_preset_texts", {})
            hidden.setdefault(cat, [])
            if text not in hidden[cat]:
                hidden[cat].append(text)
        else:
            # 删除自定义：从text_pool移除
            pool = self.bubbles.setdefault("text_pool", {})
            pool.setdefault(cat, [])
            if text in pool[cat]:
                pool[cat].remove(text)
        
        # 刷新列表
        self._load_text_cat(cat)


    # ========== 新增：Idle Chat 相关方法 ==========
    def _load_idle_chat_into_widgets(self):
        """加载idle_chat文案到UI"""
        self.idle_chat_list.clear()
        for text in self.bubbles.get("idle_chat", []):
            self.idle_chat_list.addItem(text)

    def _add_idle_chat(self):
        text, ok = QInputDialog.getText(self, "添加待机闲聊", "闲聊文案:")
        if ok and text.strip():
            self._mark_dirty()
            self.idle_chat_list.addItem(text.strip())

    def _delete_idle_chat(self):
        current = self.idle_chat_list.currentRow()
        if current >= 0:
            self._mark_dirty()
            self.idle_chat_list.takeItem(current)

    def _gather_idle_chat_from_widgets(self):
        """从UI收集idle_chat数据"""
        idle_chat = []
        for i in range(self.idle_chat_list.count()):
            idle_chat.append(self.idle_chat_list.item(i).text())
        self.bubbles["idle_chat"] = idle_chat


    # ========== 新增：Filters 相关方法 ==========
    def _build_filters(self):
        """构建过滤器标签页UI"""
        lay = QVBoxLayout()

        # 屏蔽进程列表
        lbl1 = QLabel("屏蔽进程列表")
        lbl1.setStyleSheet("font-weight:600;")
        lbl1.setToolTip("列表中的进程不会触发活动气泡，也不会被自动抓取。")
        lay.addWidget(lbl1)
        
        hint1 = QLabel("添加要忽略的进程名（如 chrome.exe）")
        hint1.setStyleSheet("color: gray; font-size: 11px;")
        lay.addWidget(hint1)

        self.filter_exe_list = QListWidget()
        lay.addWidget(self.filter_exe_list)

        btn_row1 = QHBoxLayout()
        btn_add_exe = QPushButton("添加")
        btn_del_exe = QPushButton("删除")
        btn_add_exe.clicked.connect(self._add_filter_exe)
        btn_del_exe.clicked.connect(self._delete_filter_exe)
        btn_row1.addWidget(btn_add_exe)
        btn_row1.addWidget(btn_del_exe)
        btn_row1.addStretch(1)
        lay.addLayout(btn_row1)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        lay.addWidget(sep)

        # 屏蔽标题关键词
        lbl2 = QLabel("屏蔽标题关键词")
        lbl2.setStyleSheet("font-weight:600;")
        lbl2.setToolTip("窗口标题包含这些关键词时不会触发气泡。")
        lay.addWidget(lbl2)
        
        hint2 = QLabel("添加要忽略的窗口标题关键词")
        hint2.setStyleSheet("color: gray; font-size: 11px;")
        lay.addWidget(hint2)

        self.filter_title_list = QListWidget()
        lay.addWidget(self.filter_title_list)

        btn_row2 = QHBoxLayout()
        btn_add_title = QPushButton("添加")
        btn_del_title = QPushButton("删除")
        btn_add_title.clicked.connect(self._add_filter_title)
        btn_del_title.clicked.connect(self._delete_filter_title)
        btn_row2.addWidget(btn_add_title)
        btn_row2.addWidget(btn_del_title)
        btn_row2.addStretch(1)
        lay.addLayout(btn_row2)

        lay.addStretch(1)
        self.tab_filters.setLayout(lay)

    # ========== 新增：AI 配置 ==========
    def _build_ai(self):
        self._ai_loading = False
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        lay = QVBoxLayout()
        lay.setSpacing(10)
        lay.setContentsMargins(8, 8, 8, 8)

        # ── 连接配置 ──
        box_conn = QGroupBox("连接配置")
        form_conn = QFormLayout()
        form_conn.setSpacing(8)

        self.ai_provider = QComboBox()
        for p in PROVIDER_PRESETS:
            self.ai_provider.addItem(p["name"])
        self.ai_provider.setToolTip(
            "选择服务商后自动填充 Base URL 和默认模型\n"
            "Claude 通过 OpenRouter 中转\n"
            "OpenRouter / SiliconFlow 支持多种模型（含 Claude、GPT 等）"
        )
        self.ai_provider.currentIndexChanged.connect(self._on_provider_changed)
        form_conn.addRow("服务商", self.ai_provider)

        self.ai_base_url = QLineEdit()
        self.ai_base_url.setPlaceholderText("https://api.openai.com/v1")
        self.ai_base_url.setToolTip("OpenAI 兼容 API 地址，选择服务商后自动填充，也可手动修改")
        form_conn.addRow("Base URL", self.ai_base_url)

        self.ai_api_key = QLineEdit()
        self.ai_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.ai_api_key.setPlaceholderText("sk-...")
        form_conn.addRow("API Key", self.ai_api_key)

        row_model = QHBoxLayout()
        row_model.setSpacing(6)
        self.ai_model = QLineEdit()
        self.ai_model.setPlaceholderText("gpt-4o-mini")
        self.ai_model.textChanged.connect(self._on_model_text_changed)
        row_model.addWidget(self.ai_model, 1)

        self.btn_fetch_models = QPushButton("获取模型")
        self.btn_fetch_models.setToolTip("在线拉取可用模型列表")
        self.btn_fetch_models.clicked.connect(self._fetch_models)
        row_model.addWidget(self.btn_fetch_models)

        self.btn_test_ai = QPushButton("测试连接")
        self.btn_test_ai.setToolTip("发送最小请求验证 Key / URL / 模型")
        self.btn_test_ai.clicked.connect(self._test_ai)
        row_model.addWidget(self.btn_test_ai)

        wrow = QWidget()
        wrow.setLayout(row_model)
        form_conn.addRow("模型", wrow)

        self.lbl_vision_warn = QLabel("⚠ 当前模型不支持截图，附带截图功能将自动禁用")
        self.lbl_vision_warn.setStyleSheet("color: orange; font-size: 11px;")
        self.lbl_vision_warn.setWordWrap(True)
        self.lbl_vision_warn.setVisible(False)
        form_conn.addRow("", self.lbl_vision_warn)

        box_conn.setLayout(form_conn)
        lay.addWidget(box_conn)

        # ── 人设 & 行为 ──
        box_persona = QGroupBox("人设 & 行为")
        vp = QVBoxLayout()
        vp.setSpacing(8)

        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("预设"))
        self.ai_preset_combo = QComboBox()
        self.ai_preset_combo.setToolTip("选择一套人设预设，自动填充下方 Prompt")
        self.ai_preset_combo.currentIndexChanged.connect(self._on_preset_changed)
        preset_row.addWidget(self.ai_preset_combo, 1)
        btn_add_preset = QPushButton("+")
        btn_add_preset.setFixedWidth(30)
        btn_add_preset.setToolTip("将当前 Prompt 另存为新预设")
        btn_add_preset.clicked.connect(self._add_prompt_preset)
        preset_row.addWidget(btn_add_preset)
        btn_del_preset = QPushButton("\u2212")
        btn_del_preset.setFixedWidth(30)
        btn_del_preset.setToolTip("删除当前选中的预设")
        btn_del_preset.clicked.connect(self._del_prompt_preset)
        preset_row.addWidget(btn_del_preset)
        vp.addLayout(preset_row)

        vp.addWidget(QLabel("系统 Prompt（桌宠人设）"))

        self.ai_system_prompt = QTextEdit()
        self.ai_system_prompt.setFixedHeight(90)
        self.ai_system_prompt.setPlaceholderText("定义桌宠的性格和回复风格…")
        self.ai_system_prompt.setToolTip("发送给 AI 的 system 角色提示，决定桌宠的说话风格")
        self.ai_system_prompt.textChanged.connect(self._on_prompt_text_edited)
        vp.addWidget(self.ai_system_prompt)

        grid_persona = QGridLayout()
        grid_persona.setSpacing(8)

        lbl_bl = QLabel("回复最少字数")
        lbl_bl.setToolTip("建议 AI 每次回复至少多少字（0=不限下限）")
        grid_persona.addWidget(lbl_bl, 0, 0)
        self.ai_min_reply = QSpinBox()
        self.ai_min_reply.setRange(0, 500)
        self.ai_min_reply.setSingleStep(10)
        self.ai_min_reply.setSuffix(" 字")
        grid_persona.addWidget(self.ai_min_reply, 0, 1)

        lbl_bl2 = QLabel("回复最多字数")
        lbl_bl2.setToolTip("建议 AI 每次回复最多多少字（0=不限上限），气泡完整显示不截断")
        grid_persona.addWidget(lbl_bl2, 0, 2)
        self.ai_max_bubble = QSpinBox()
        self.ai_max_bubble.setRange(0, 500)
        self.ai_max_bubble.setSingleStep(10)
        self.ai_max_bubble.setSuffix(" 字")
        grid_persona.addWidget(self.ai_max_bubble, 0, 3)

        lbl_mt = QLabel("记忆轮数")
        lbl_mt.setToolTip("保留最近 N 轮对话作为上下文发送给 AI（0=不带历史）")
        grid_persona.addWidget(lbl_mt, 1, 0)
        self.ai_max_memory = QSpinBox()
        self.ai_max_memory.setRange(0, 50)
        self.ai_max_memory.setSuffix(" 轮")
        grid_persona.addWidget(self.ai_max_memory, 1, 1)

        lbl_as = QLabel("自动截图间隔")
        lbl_as.setToolTip("桌宠每隔 N 分钟自动截屏并主动发言（0=关闭自动巡视）")
        grid_persona.addWidget(lbl_as, 1, 2)
        self.ai_auto_screenshot = QSpinBox()
        self.ai_auto_screenshot.setRange(0, 120)
        self.ai_auto_screenshot.setSuffix(" 分钟")
        self.ai_auto_screenshot.setSpecialValueText("关闭")
        grid_persona.addWidget(self.ai_auto_screenshot, 1, 3)

        self.cb_app_bubbles_ai = QCheckBox("应用检测气泡")
        self.cb_app_bubbles_ai.setToolTip("只控制“检测应用触发的文案气泡”，与 AI 对话无关")
        self.cb_app_bubbles_ai.stateChanged.connect(self._sync_app_bubbles_from_ai_checkbox)
        grid_persona.addWidget(self.cb_app_bubbles_ai, 2, 2, 1, 2)

        self.cb_auto_watch_ai = QCheckBox("启用自动巡视")
        self.cb_auto_watch_ai.setToolTip("开启后桌宠按上方设定的间隔定时巡视并主动发言\n间隔为 0 时无效")
        self.cb_auto_watch_ai.stateChanged.connect(self._toggle_auto_watch_from_settings)
        grid_persona.addWidget(self.cb_auto_watch_ai, 3, 2, 1, 2)

        lbl_bb = QLabel("黑匣子保留条数")
        lbl_bb.setToolTip("本地记忆黑匣子最多保留多少条记录（只影响本地存储，不影响发送给 AI 的记忆轮数）")
        grid_persona.addWidget(lbl_bb, 2, 0)
        self.ai_blackbox_keep = QSpinBox()
        self.ai_blackbox_keep.setRange(20, 2000)
        self.ai_blackbox_keep.setSuffix(" 条")
        grid_persona.addWidget(self.ai_blackbox_keep, 2, 1)

        vp.addLayout(grid_persona)
        box_persona.setLayout(vp)
        lay.addWidget(box_persona)

        hint = QLabel("提示：选择服务商 → 填写 Key → 获取模型 → 测试连接 → 打开「桌宠控制台」体验")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        lay.addStretch(1)
        inner.setLayout(lay)
        scroll.setWidget(inner)

        outer = QVBoxLayout()
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        self.tab_ai.setLayout(outer)

    # ── AI: provider dropdown ──
    def _on_provider_changed(self, idx):
        if getattr(self, "_ai_loading", False):
            return
        if 0 <= idx < len(PROVIDER_PRESETS):
            p = PROVIDER_PRESETS[idx]
            self.ai_base_url.setText(p["base_url"])
            if p["default_model"]:
                self.ai_model.setText(p["default_model"])

    # ── AI: model name -> auto-detect vision ──
    def _on_model_text_changed(self, text):
        if getattr(self, "_ai_loading", False):
            return
        v = guess_supports_vision(text)
        self.lbl_vision_warn.setVisible(not v)

    # ── AI: prompt presets ──
    def _populate_preset_combo(self):
        presets = load_prompt_presets()
        combo = self.ai_preset_combo
        combo.blockSignals(True)
        combo.clear()
        for p in presets:
            combo.addItem(p.get("name", ""))
        combo.addItem("（自定义）")
        combo.blockSignals(False)

    def _on_preset_changed(self, idx):
        if getattr(self, "_ai_loading", False):
            return
        presets = load_prompt_presets()
        if 0 <= idx < len(presets):
            self.ai_system_prompt.blockSignals(True)
            self.ai_system_prompt.setPlainText(presets[idx].get("prompt", ""))
            self.ai_system_prompt.blockSignals(False)

    def _on_prompt_text_edited(self):
        if getattr(self, "_ai_loading", False):
            return
        current_text = (self.ai_system_prompt.toPlainText() or "").strip()
        presets = load_prompt_presets()
        matched = False
        for i, p in enumerate(presets):
            if p.get("prompt", "").strip() == current_text:
                self.ai_preset_combo.blockSignals(True)
                self.ai_preset_combo.setCurrentIndex(i)
                self.ai_preset_combo.blockSignals(False)
                matched = True
                break
        if not matched:
            self.ai_preset_combo.blockSignals(True)
            self.ai_preset_combo.setCurrentIndex(self.ai_preset_combo.count() - 1)
            self.ai_preset_combo.blockSignals(False)

    def _add_prompt_preset(self):
        name, ok = QInputDialog.getText(self, "新建预设", "预设名称：")
        if not ok or not (name or "").strip():
            return
        name = name.strip()
        prompt = (self.ai_system_prompt.toPlainText() or "").strip()
        if not prompt:
            QMessageBox.warning(self, "提示", "Prompt 为空，无法保存。")
            return
        presets = load_prompt_presets()
        for p in presets:
            if p.get("name") == name:
                p["prompt"] = prompt
                save_prompt_presets(presets)
                self._populate_preset_combo()
                return
        presets.append({"name": name, "prompt": prompt})
        save_prompt_presets(presets)
        self._populate_preset_combo()
        self.ai_preset_combo.setCurrentIndex(len(presets) - 1)

    def _del_prompt_preset(self):
        idx = self.ai_preset_combo.currentIndex()
        presets = load_prompt_presets()
        if idx < 0 or idx >= len(presets):
            return
        name = presets[idx].get("name", "")
        if QMessageBox.question(self, "删除预设", f"确定删除「{name}」？") != QMessageBox.StandardButton.Yes:
            return
        del presets[idx]
        if not presets:
            presets = [dict(d) for d in DEFAULT_PROMPT_PRESETS]
        save_prompt_presets(presets)
        self._populate_preset_combo()

    def _toggle_auto_watch_from_settings(self, state):
        if getattr(self, "_ai_loading", False):
            return
        enabled = state == Qt.CheckState.Checked.value
        try:
            if hasattr(self, "pet") and hasattr(self.pet, "set_ai_watch_enabled"):
                self.pet.set_ai_watch_enabled(bool(enabled))
                if hasattr(self.pet, "_refresh_ai_watch_timer"):
                    self.pet._refresh_ai_watch_timer()
            console = getattr(self.pet, "_chat_console", None)
            if console and hasattr(console, "_sync_auto_watch_from_pet"):
                console._sync_auto_watch_from_pet()
        except Exception:
            pass

    def _fetch_models(self):
        s = self._ai_settings_from_widgets()
        self.btn_fetch_models.setEnabled(False)
        self.btn_fetch_models.setText("拉取中…")
        worker = _FetchModelsWorker(s, parent=self)

        def _on_done(models):
            self.btn_fetch_models.setEnabled(True)
            self.btn_fetch_models.setText("获取模型")
            if not models:
                QMessageBox.information(self, "提示", "返回的模型列表为空。")
                return
            model, ok = QInputDialog.getItem(self, "选择模型", "可用模型：", models, 0, False)
            if ok and model:
                self.ai_model.setText(model)
            worker.deleteLater()

        def _on_err(msg):
            self.btn_fetch_models.setEnabled(True)
            self.btn_fetch_models.setText("获取模型")
            QMessageBox.warning(self, "获取失败", msg)
            worker.deleteLater()

        worker.finished.connect(_on_done)
        worker.error.connect(_on_err)
        self._fetch_worker = worker
        worker.start()

    def _test_ai(self):
        s = self._ai_settings_from_widgets()
        self.btn_test_ai.setEnabled(False)
        self.btn_test_ai.setText("测试中…")
        worker = _TestAIWorker(s, parent=self)

        def _on_done(text):
            self.btn_test_ai.setEnabled(True)
            self.btn_test_ai.setText("测试连接")
            QMessageBox.information(self, "✅ 测试成功", f"模型可用，回复：\n{text[:150]}")
            worker.deleteLater()

        def _on_err(msg):
            self.btn_test_ai.setEnabled(True)
            self.btn_test_ai.setText("测试连接")
            QMessageBox.warning(self, "❌ 测试失败", msg)
            worker.deleteLater()

        worker.finished.connect(_on_done)
        worker.error.connect(_on_err)
        self._test_worker = worker
        worker.start()

    def _ai_settings_from_widgets(self) -> AISettings:
        s = AISettings()
        s.base_url = (self.ai_base_url.text() or "").strip() or s.base_url
        s.api_key = (self.ai_api_key.text() or "").strip()
        s.model = (self.ai_model.text() or "").strip() or s.model
        if hasattr(self, "ai_system_prompt"):
            s.system_prompt = (self.ai_system_prompt.toPlainText() or "").strip() or s.system_prompt
        if hasattr(self, "ai_min_reply"):
            s.reply_min_length = self.ai_min_reply.value()
        if hasattr(self, "ai_max_bubble"):
            s.reply_max_length = self.ai_max_bubble.value()
        if hasattr(self, "ai_max_memory"):
            s.max_memory_turns = self.ai_max_memory.value()
        if hasattr(self, "ai_auto_screenshot"):
            s.auto_screenshot_interval_min = self.ai_auto_screenshot.value()
        if hasattr(self, "ai_blackbox_keep"):
            s.max_blackbox_logs = int(self.ai_blackbox_keep.value())
        s.supports_vision = guess_supports_vision(s.model)
        return s

    def _add_filter_exe(self):
        text, ok = QInputDialog.getText(self, "添加屏蔽进程", "进程名（如 chrome.exe）:")
        if ok and text.strip():
            self._mark_dirty()
            self.filter_exe_list.addItem(text.strip())
            
            # 立刻更新self.filters数据
            self._gather_filters_from_widgets()
            
            # 清空应用缓存（让添加立刻生效）
            try:
                logger.info(f"添加过滤器 {text.strip()}，清空应用缓存")
                self.pet._recent_apps = {}
            except Exception:
                pass

    def _delete_filter_exe(self):
        current = self.filter_exe_list.currentRow()
        if current >= 0:
            self._mark_dirty()
            # 从UI删除
            item = self.filter_exe_list.takeItem(current)
            
            # 立刻更新self.filters数据
            self._gather_filters_from_widgets()
            
            # 清空应用缓存（让删除立刻生效）
            try:
                logger.info(f"从过滤器删除 {item.text()}，清空应用缓存")
                self.pet._recent_apps = {}
            except Exception:
                pass

    def _add_filter_title(self):
        text, ok = QInputDialog.getText(self, "添加屏蔽标题关键词", "关键词:")
        if ok and text.strip():
            self._mark_dirty()
            self.filter_title_list.addItem(text.strip())
            
            # 立刻更新self.filters数据
            self._gather_filters_from_widgets()
            
            # 清空应用缓存
            try:
                logger.info(f"添加标题过滤器 {text.strip()}，清空应用缓存")
                self.pet._recent_apps = {}
            except Exception:
                pass

    def _delete_filter_title(self):
        current = self.filter_title_list.currentRow()
        if current >= 0:
            self._mark_dirty()
            # 从UI删除
            item = self.filter_title_list.takeItem(current)
            
            # 立刻更新self.filters数据
            self._gather_filters_from_widgets()
            
            # 清空应用缓存
            try:
                logger.info(f"从过滤器删除标题关键词 {item.text()}，清空应用缓存")
                self.pet._recent_apps = {}
            except Exception:
                pass

    def _load_filters_into_widgets(self):
        """将filters.json内容加载到UI"""
        self.filter_exe_list.clear()
        for exe in self.filters.get("ignored_exe", []):
            self.filter_exe_list.addItem(exe)
        
        self.filter_title_list.clear()
        for kw in self.filters.get("ignored_title_keywords", []):
            self.filter_title_list.addItem(kw)

    def _gather_filters_from_widgets(self):
        """从UI收集filters数据"""
        self.filters["version"] = 1
        
        exe_list = []
        for i in range(self.filter_exe_list.count()):
            exe_list.append(self.filter_exe_list.item(i).text())
        self.filters["ignored_exe"] = exe_list
        
        title_list = []
        for i in range(self.filter_title_list.count()):
            title_list.append(self.filter_title_list.item(i).text())
        self.filters["ignored_title_keywords"] = title_list


    # ========== 新增：类别管理相关方法 ==========
    def _load_categories_into_widgets(self):
        """加载类别到UI列表"""
        self.category_list.clear()
        for cat in CATEGORIES:
            self.category_list.addItem(cat)

    def _refresh_all_category_dropdowns(self):
        """刷新所有标签页的类别下拉框"""
        # 1. App Mapping的category下拉框
        current_cat = self.cb_cat.currentText()
        self.cb_cat.clear()
        for c in CATEGORIES:
            self.cb_cat.addItem(c)
        # 恢复选中（如果还存在）
        if current_cat in CATEGORIES:
            self.cb_cat.setCurrentText(current_cat)
        
        # 2. Text Pool的category下拉框
        current_text_cat = self.cb_text_cat.currentText()
        self.cb_text_cat.clear()
        for c in CATEGORIES:
            self.cb_text_cat.addItem(c)
        # 恢复选中（如果还存在）
        if current_text_cat in CATEGORIES:
            self.cb_text_cat.setCurrentText(current_text_cat)

    def _add_category(self):
        """添加新类别"""
        text, ok = QInputDialog.getText(self, "添加类别", "类别名称（如 design）：")
        if not ok or not text.strip():
            return
        
        new_cat = text.strip().lower()
        
        # 检查是否已存在
        if new_cat in CATEGORIES:
            QMessageBox.warning(self, "重复", f"类别 {new_cat} 已存在！")
            return

        self._mark_dirty()
        # 添加到全局列表
        CATEGORIES.append(new_cat)
        
        # 添加冷却时间控件
        sp = QSpinBox()
        sp.setRange(0, 86400)
        sp.setSingleStep(30)
        sp.setValue(600)  # 默认10分钟
        self.cooldowns[new_cat] = sp
        
        # 刷新UI
        self._load_categories_into_widgets()
        self._rebuild_rules_tab()
        self._refresh_all_category_dropdowns()  # 刷新其他标签页的下拉框
        
        QMessageBox.information(self, "成功", f"类别 {new_cat} 已添加，默认冷却600秒")

    def _rename_category(self):
        """重命名类别"""
        current_item = self.category_list.currentItem()
        if not current_item:
            QMessageBox.warning(self, "未选择", "请先选择要重命名的类别")
            return
        
        old_cat = current_item.text()
        new_cat, ok = QInputDialog.getText(self, "重命名类别", f"新名称（当前: {old_cat}）:", text=old_cat)
        
        if not ok or not new_cat.strip() or new_cat.strip() == old_cat:
            return
        
        new_cat = new_cat.strip().lower()
        
        # 检查新名称是否已存在
        if new_cat in CATEGORIES and new_cat != old_cat:
            QMessageBox.warning(self, "重复", f"类别 {new_cat} 已存在！")
            return

        self._mark_dirty()
        # 更新全局列表
        idx = CATEGORIES.index(old_cat)
        CATEGORIES[idx] = new_cat
        
        # 更新冷却时间控件
        if old_cat in self.cooldowns:
            old_sp = self.cooldowns[old_cat]
            old_value = old_sp.value()
            del self.cooldowns[old_cat]
            
            sp = QSpinBox()
            sp.setRange(0, 86400)
            sp.setSingleStep(30)
            sp.setValue(old_value)
            self.cooldowns[new_cat] = sp
        
        # 更新bubbles配置中的类别引用
        if "settings" in self.bubbles:
            cooldowns = self.bubbles["settings"].get("cooldown_seconds_by_category", {})
            if old_cat in cooldowns:
                cooldowns[new_cat] = cooldowns[old_cat]
                del cooldowns[old_cat]
        
        # 刷新UI
        self._load_categories_into_widgets()
        self._rebuild_rules_tab()
        self._refresh_all_category_dropdowns()  # 刷新其他标签页的下拉框
        
        QMessageBox.information(self, "成功", f"类别已重命名: {old_cat} → {new_cat}")

    def _delete_category(self):
        """删除类别"""
        current_item = self.category_list.currentItem()
        if not current_item:
            QMessageBox.warning(self, "未选择", "请先选择要删除的类别")
            return
        
        cat = current_item.text()
        
        # 确认删除
        reply = QMessageBox.question(self, "确认删除", 
            f"确定要删除类别 {cat} 吗？\n\n将同时删除该类别的冷却时间设置。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._mark_dirty()
        # 从全局列表删除
        CATEGORIES.remove(cat)
        
        # 删除冷却时间控件
        if cat in self.cooldowns:
            del self.cooldowns[cat]
        
        # 从bubbles配置删除
        if "settings" in self.bubbles:
            cooldowns = self.bubbles["settings"].get("cooldown_seconds_by_category", {})
            if cat in cooldowns:
                del cooldowns[cat]
        
        # 刷新UI
        self._load_categories_into_widgets()
        self._rebuild_rules_tab()
        self._refresh_all_category_dropdowns()  # 刷新其他标签页的下拉框
        
        QMessageBox.information(self, "成功", f"类别 {cat} 已删除")

    def _rebuild_rules_tab(self):
        """重建Rules标签页（类别变化后需要重建UI）"""
        # 清空旧布局
        old_layout = self.tab_rules.layout()
        if old_layout is not None:
            QWidget().setLayout(old_layout)
        
        # 重建
        self._build_rules()
        # 新生成的 cooldowns 控件需要连接 dirty
        for sp in (self.cooldowns or {}).values():
            if hasattr(sp, "valueChanged"):
                sp.valueChanged.connect(self._mark_dirty)
        # 重新加载数据
        self._load_into_widgets()


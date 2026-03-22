from __future__ import annotations

import sys
import os
from PyQt6.QtCore import QEvent, QMessageLogContext, Qt, QtMsgType, QTimer, qInstallMessageHandler
from PyQt6.QtGui import QColor, QFont, QHelpEvent, QPalette
from PyQt6.QtWidgets import QApplication, QLabel, QMessageBox, QStyleFactory, QWidget


class _QtLogFilter:
    """过滤 Qt 无害警告（如 QFont::setPointSize(-1)），其余交给原处理器。"""
    prev_handler = None


def _qt_message_handler(mode: QtMsgType, context: QMessageLogContext, message: str) -> None:
    if not message:
        return
    if "QFont::setPointSize" in message and "(-1)" in message:
        return
    ph = _QtLogFilter.prev_handler
    if ph is not None:
        ph(mode, context, message)
    else:
        print(message, file=sys.stderr)


def _tooltip_text_for_widget(w: QWidget) -> str:
    """沿父链查找第一个非空 toolTip（子控件常无独立提示，应显示父级）。"""
    while w is not None:
        t = (w.toolTip() or "").strip()
        if t:
            return w.toolTip()
        w = w.parentWidget()
    return ""


class DesktopPetApp(QApplication):
    """Windows 下默认样式对 QToolTip 的文本绘制与系统深色模式/样式表冲突时会出现「只有框、字完全不画」。
    在 notify 里拦截 ToolTip，用独立 QLabel 绘制，不依赖 QToolTip 原生路径。"""

    def __init__(self, argv):
        super().__init__(argv)
        self._tip_popup: QLabel | None = None
        self._tip_timer = QTimer(self)
        self._tip_timer.setSingleShot(True)
        self._tip_timer.timeout.connect(self._hide_tip_popup)
        fusion = QStyleFactory.create("Fusion")
        if fusion is not None:
            self.setStyle(fusion)
        pal = self.palette()
        for grp in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):
            pal.setColor(grp, QPalette.ColorRole.ToolTipBase, QColor("#1e293b"))
            pal.setColor(grp, QPalette.ColorRole.ToolTipText, QColor("#f8fafc"))
        self.setPalette(pal)

    def _ensure_tip_popup(self) -> QLabel:
        if self._tip_popup is not None:
            return self._tip_popup
        lbl = QLabel()
        lbl.setWindowFlags(
            Qt.WindowType.ToolTip
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        lbl.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        lbl.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        lbl.setTextFormat(Qt.TextFormat.PlainText)
        lbl.setWordWrap(True)
        lbl.setMaximumWidth(480)
        lbl.setStyleSheet(
            "QLabel { background-color: #1e293b; color: #f8fafc; padding: 10px 14px; "
            "border: 1px solid #475569; border-radius: 8px; }"
        )
        lbl.setFont(QFont("Microsoft YaHei UI", 9))
        self._tip_popup = lbl
        return lbl

    def _hide_tip_popup(self) -> None:
        if self._tip_popup is not None:
            self._tip_popup.hide()

    def notify(self, receiver, event):
        if event.type() == QEvent.Type.ToolTip:
            if isinstance(receiver, QWidget) and isinstance(event, QHelpEvent):
                tip = _tooltip_text_for_widget(receiver)
                if tip:
                    try:
                        lbl = self._ensure_tip_popup()
                        lbl.setText(tip)
                        lbl.adjustSize()
                        gp = event.globalPos()
                        lbl.move(int(gp.x()) + 10, int(gp.y()) + 6)
                        lbl.raise_()
                        lbl.show()
                        self._tip_timer.stop()
                        self._tip_timer.start(10000)
                        return True
                    except Exception:
                        pass
        return super().notify(receiver, event)


# 导入日志模块
from logger import logger, log_exception

# Shared helpers live in config_utils to avoid circular imports.
from config_utils import get_resource_path, get_config_dir  # noqa: F401  re-exported

# Import only the pet core. All config file bootstrapping is handled inside DesktopPet.
from pet_core import DesktopPet


def ensure_default_configs():
    """首次运行时，从assets复制默认配置到用户目录"""
    config_dir = get_config_dir()
    logger.info(f"配置目录: {config_dir}")

    # 需要复制的配置文件
    config_files = [
        "bubbles.json",
        "pet_settings.json",
        "app_map.json",
        "filters.json",
    ]

    for filename in config_files:
        user_config = os.path.join(config_dir, filename)

        # 如果用户配置不存在，从assets复制默认配置
        if not os.path.exists(user_config):
            try:
                # 尝试从assets目录读取默认配置
                default_config = get_resource_path(os.path.join("assets", filename))
                if os.path.exists(default_config):
                    import shutil

                    shutil.copy(default_config, user_config)
                    logger.info(f"创建默认配置: {filename}")
            except Exception as e:
                logger.error(f"无法复制默认配置 {filename}: {e}")
                log_exception(logger)


def main() -> None:
    try:
        logger.info("开始初始化...")

        # 确保配置文件存在
        ensure_default_configs()

        logger.info("创建QApplication...")
        app = DesktopPetApp(sys.argv)
        _QtLogFilter.prev_handler = qInstallMessageHandler(_qt_message_handler)
        # 只允许通过菜单 Exit 退出，避免关掉设置窗口就把桌宠一起关了
        app.setQuitOnLastWindowClosed(False)

        logger.info("创建桌宠实例...")
        pet = DesktopPet()
        pet.show()
        logger.info("桌宠启动成功！")

        sys.exit(app.exec())

    except Exception as e:
        logger.error("程序启动失败！")
        log_exception(logger, "致命错误")

        # 显示错误对话框
        try:
            from logger import get_log_dir

            log_dir = get_log_dir()
            QMessageBox.critical(
                None,
                "启动失败",
                f"桌宠启动失败！\n\n错误信息：{str(e)}\n\n" f"请查看日志文件：\n{log_dir}",
            )
        except Exception:
            pass

        sys.exit(1)


if __name__ == "__main__":
    main()

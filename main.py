import sys
import os
from PyQt6.QtCore import QMessageLogContext, QtMsgType, qInstallMessageHandler
from PyQt6.QtWidgets import QApplication, QMessageBox


class _QtLogFilter:
    """过滤 Qt 无害警告（如 QFont::setPointSize(-1)），其余交给原处理器。"""
    prev_handler = None


def _qt_message_handler(mode: QtMsgType, context: QMessageLogContext, message: str) -> None:
    if "QFont::setPointSize" in message and "(-1)" in message:
        return
    ph = _QtLogFilter.prev_handler
    if ph is not None:
        ph(mode, context, message)
    else:
        # 无前置处理器时与 Qt 默认行为接近：输出到 stderr
        print(message, file=sys.stderr)

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
        "filters.json"
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
        app = QApplication(sys.argv)
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
            QMessageBox.critical(None, "启动失败", 
                f"桌宠启动失败！\n\n错误信息：{str(e)}\n\n"
                f"请查看日志文件：\n{log_dir}")
        except Exception:
            pass
        
        sys.exit(1)


if __name__ == "__main__":
    main()

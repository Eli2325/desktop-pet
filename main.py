import sys
import os
from PyQt6.QtWidgets import QApplication, QMessageBox

# 导入日志模块
from logger import logger, log_exception

# Import only the pet core. All config file bootstrapping is handled inside DesktopPet.
from pet_core import DesktopPet


def get_resource_path(relative_path):
    """获取资源文件路径（兼容开发环境和打包后的exe）
    
    优先级：
    1. exe同级目录的资源（用户可修改）
    2. 打包内置资源（fallback）
    """
    try:
        # 打包后，优先从exe所在目录加载
        if getattr(sys, 'frozen', False):
            # 获取exe所在目录
            exe_dir = os.path.dirname(sys.executable)
            external_path = os.path.join(exe_dir, relative_path)
            
            # 如果exe同级目录有资源，用外部的（用户可修改）
            if os.path.exists(external_path):
                return external_path
            
            # 否则用打包内置的资源
            base_path = sys._MEIPASS
        else:
            # 开发环境，资源在脚本所在目录
            base_path = os.path.dirname(os.path.abspath(__file__))
    except AttributeError:
        # 开发环境fallback
        base_path = os.path.dirname(os.path.abspath(__file__))
    
    return os.path.join(base_path, relative_path)


def get_config_dir():
    """获取配置文件目录（用户可写目录）"""
    # 配置文件放在用户目录下的.desktop_pet文件夹
    config_dir = os.path.join(os.path.expanduser("~"), ".desktop_pet")
    os.makedirs(config_dir, exist_ok=True)
    return config_dir


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

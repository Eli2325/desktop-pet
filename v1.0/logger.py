"""
简单的日志系统
自动记录程序运行情况，方便调试
"""
import os
import sys
import logging
import traceback
from datetime import datetime


def get_log_dir():
    """返回当前环境下的日志目录（与 setup_logger 一致，供错误提示等使用）"""
    try:
        _ = sys._MEIPASS
        return os.path.join(os.path.expanduser("~"), ".desktop_pet", "logs")
    except AttributeError:
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")


def setup_logger():
    """设置日志系统"""
    log_dir = get_log_dir()
    os.makedirs(log_dir, exist_ok=True)
    
    # 清理旧日志（保留最近7天）
    _cleanup_old_logs(log_dir, days_to_keep=7)
    
    # 日志文件名：带日期，方便追踪
    log_file = os.path.join(log_dir, f"desktop_pet_{datetime.now().strftime('%Y%m%d')}.log")
    
    # 配置日志格式
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%H:%M:%S',
        handlers=[
            logging.FileHandler(log_file, encoding='utf-8'),
            logging.StreamHandler()  # 同时输出到控制台
        ]
    )
    
    logger = logging.getLogger('DesktopPet')
    logger.info("=" * 50)
    logger.info("桌面宠物启动")
    logger.info(f"日志文件: {log_file}")
    logger.info("=" * 50)
    
    return logger


def _cleanup_old_logs(log_dir, days_to_keep=7):
    """清理旧日志文件，只保留最近N天"""
    try:
        import time
        now = time.time()
        cutoff = now - (days_to_keep * 24 * 60 * 60)  # N天前的时间戳
        
        for filename in os.listdir(log_dir):
            if filename.endswith('.log'):
                filepath = os.path.join(log_dir, filename)
                # 检查文件修改时间
                if os.path.getmtime(filepath) < cutoff:
                    os.remove(filepath)
                    print(f"清理旧日志: {filename}")
    except Exception as e:
        print(f"清理日志失败: {e}")


def log_exception(logger, error_msg="发生异常"):
    """记录异常详细信息"""
    logger.error(f"{error_msg}")
    logger.error(traceback.format_exc())


# 全局日志对象
logger = setup_logger()

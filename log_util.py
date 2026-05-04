"""
日志模块：同时输出到控制台 (print) 和日志文件
"""
import os
from datetime import datetime

_log_file = None


def init_log(log_dir: str):
    """在每个脚本启动时调用，创建当天的日志文件"""
    os.makedirs(log_dir, exist_ok=True)
    global _log_file
    _log_file = os.path.join(log_dir, f"bot_{datetime.now().strftime('%Y-%m-%d')}.log")


def log(msg: str):
    """写入日志文件（print 由调用方负责，这里只写文件）"""
    if not _log_file:
        return
    try:
        with open(_log_file, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass

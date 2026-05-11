"""
日志模块：同时输出到控制台 (print) 和日志文件
"""
import os
from datetime import datetime

_log_file = None
_log_interceptor = None


def init_log(log_dir: str):
    """在每个脚本启动时调用，创建当天的日志文件"""
    os.makedirs(log_dir, exist_ok=True)
    global _log_file
    _log_file = os.path.join(log_dir, f"bot_{datetime.now().strftime('%Y-%m-%d')}.log")


def set_log_interceptor(cb):
    """注册回调 cb(ts: str, msg: str)，用于 GUI 实时日志显示"""
    global _log_interceptor
    _log_interceptor = cb


def log(msg: str):
    """写入日志文件（print 由调用方负责，这里只写文件）"""
    if _log_file:
        try:
            with open(_log_file, "a", encoding="utf-8") as f:
                f.write(msg + "\n")
        except Exception:
            pass
    if _log_interceptor:
        try:
            _log_interceptor(msg)
        except Exception:
            pass

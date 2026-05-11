"""
弹幕机器人配置 - 从 config.json 读取
"""
import os
import sys
from datetime import datetime


def ts() -> str:
    """返回当前时间戳字符串 [HH:MM:SS]，用于控制台日志"""
    return datetime.now().strftime("%H:%M:%S")


def _init():
    from config_manager import config as cfg
    c = cfg

    globals()["ROOM_ID"] = c.get("room", "room_id")
    globals()["SESSDATA"] = c.get("room", "sessdata")
    globals()["BILI_JCT"] = c.get("room", "bili_jct")
    globals()["BUVID3"] = c.get("room", "buvid3")

    globals()["BILI_HEADERS"] = {
        "Referer": "https://live.bilibili.com/",
        "Origin": "https://live.bilibili.com",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36 Edg/132.0.0.0",
    }
    globals()["BILI_COOKIES"] = {
        "SESSDATA": c.get("room", "sessdata"),
        "bili_jct": c.get("room", "bili_jct"),
        "buvid3": c.get("room", "buvid3"),
    }

    globals()["VISION_BASE_URL"] = c.get("vision", "base_url")
    globals()["VISION_API_KEY"] = c.get("vision", "api_key")
    globals()["VISION_MODEL"] = c.get("vision", "model")

    globals()["ANALYSIS_BASE_URL"] = c.get("analysis", "base_url")
    globals()["ANALYSIS_API_KEY"] = c.get("analysis", "api_key")
    globals()["ANALYSIS_MODEL"] = c.get("analysis", "model")
    globals()["EPISODE_MODEL"] = c.get("analysis", "episode_model")
    globals()["DUP_CHECK_MODEL"] = c.get("analysis", "dup_check_model")
    globals()["DUP_CHECK_WINDOW"] = c.get("analysis", "dup_check_window")

    globals()["REPLY_BASE_URL"] = c.get("reply", "base_url")
    globals()["REPLY_API_KEY"] = c.get("reply", "api_key")
    globals()["REPLY_MODEL"] = c.get("reply", "model")

    globals()["EMBEDDING_BASE_URL"] = c.get("embedding", "base_url")
    globals()["EMBEDDING_API_KEY"] = c.get("embedding", "api_key")
    globals()["EMBEDDING_MODEL"] = c.get("embedding", "model")

    globals()["GEMINI_FORMAT_MODELS"] = set()

    globals()["VISION_CLIP_SECONDS"] = c.get("intervals", "vision_clip_seconds")
    globals()["PLAY_URL_API"] = "https://api.live.bilibili.com/xlive/web-room/v2/index/getRoomPlayInfo"

    bot_data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot_data")
    globals()["BOT_DATA_DIR"] = bot_data_dir
    globals()["MEMORY_DB_PATH"] = os.path.join(bot_data_dir, "memory.db")
    globals()["LONG_TERM_DB_PATH"] = os.path.join(bot_data_dir, "long_term.db")
    globals()["EPISODE_INTERVAL_SECONDS"] = c.get("intervals", "episode_interval_seconds")

    globals()["SEND_DANMAKU_API"] = "https://api.live.bilibili.com/msg/send"
    globals()["DANMAKU_COOLDOWN"] = c.get("intervals", "danmaku_cooldown")
    globals()["GIFT_THANK_THRESHOLD"] = c.get("intervals", "gift_thank_threshold")

    # ffmpeg: bundled exe > local dir > system PATH
    import platform as _plat
    _exe_name = "ffmpeg.exe" if _plat.system() == "Windows" else "ffmpeg"
    _dirname = os.path.dirname(os.path.abspath(__file__))
    if getattr(sys, "frozen", False):
        _bundled = os.path.join(sys._MEIPASS, _exe_name)
        globals()["FFMPEG_PATH"] = _bundled if os.path.isfile(_bundled) else "ffmpeg"
    else:
        _local = os.path.join(_dirname, _exe_name)
        globals()["FFMPEG_PATH"] = _local if os.path.isfile(_local) else "ffmpeg"

    globals()["SHUTDOWN_FLAG_PATH"] = os.path.join(bot_data_dir, "clean_shutdown.flag")

    globals()["HEARTBEAT_INTERVAL"] = c.get("intervals", "heartbeat_interval")

    globals()["TIP_INTERVAL_SECONDS"] = c.get("intervals", "tip_interval_seconds")
    globals()["DRINK_WATER_INTERVAL"] = c.get("intervals", "drink_water_interval")
    globals()["BOT_EAGERNESS"] = c.get("bot", "eagerness")
    globals()["DEBUG_MODE"] = c.get("bot", "debug_mode")


_init()
del _init

from prompts import BOT_PERSONA, TIP_TEMPLATES

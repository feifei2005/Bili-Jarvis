"""
弹幕机器人配置
"""
import os
from datetime import datetime


def ts() -> str:
    """返回当前时间戳字符串 [HH:MM:SS]，用于控制台日志"""
    return datetime.now().strftime("%H:%M:%S")

# ========== 房间 ==========
ROOM_ID = 0  # 替换为你的直播间ID

# ========== B站凭据 ==========
SESSDATA = "your_sessdata_here"
BILI_JCT = "your_bili_jct_here"
BUVID3 = "your_buvid3_here"

BILI_HEADERS = {
    "Referer": "https://live.bilibili.com/",
    "Origin": "https://live.bilibili.com",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36 Edg/132.0.0.0",
}

BILI_COOKIES = {
    "SESSDATA": SESSDATA,
    "bili_jct": BILI_JCT,
    "buvid3": BUVID3,
}

# ========== AI 模型（一轮处理用 4 个模型）==========

# 1. 视觉模型：describe_video() 生成画面描述（多模态 → 文本）
VISION_BASE_URL = "https://your-vision-api.example.com/v1"
VISION_API_KEY = "sk-your-vision-api-key"
VISION_MODEL = "your-vision-model"

# 2. 分析模型：analyze() 分析直播局势（文本 → JSON）
ANALYSIS_BASE_URL = "https://your-analysis-api.example.com/v1"
ANALYSIS_API_KEY = "sk-your-analysis-api-key"
ANALYSIS_MODEL = "your-analysis-model"

# 短期总结模型（Episode 生成/深度整理用，轻量低延迟）
EPISODE_MODEL = "your-episode-model"

# 语义去重模型（复用 ANALYSIS 端点，可独立配置模型名）
DUP_CHECK_MODEL = "your-dupcheck-model"
DUP_CHECK_WINDOW = 60  # 去重窗口（秒）

# 3. 回复模型：respond() 生成弹幕回复（文本 → 文本）
REPLY_BASE_URL = "https://your-reply-api.example.com/v1"
REPLY_API_KEY = "sk-your-reply-api-key"
REPLY_MODEL = "your-reply-model"

# 4. 嵌入模型：get_embedding() 长期记忆向量化（文本 → 向量）
EMBEDDING_BASE_URL = "https://your-embedding-api.example.com/v1"
EMBEDDING_API_KEY = "sk-your-embedding-api-key"
EMBEDDING_MODEL = "your-embedding-model"

# 需要 Gemini 原生请求体格式的模型（填模型名即可，不限 Gemini 系列）
GEMINI_FORMAT_MODELS = set()

# ========== 视觉采样 ==========
VISION_CLIP_SECONDS = 20
# 获取流地址的 API（V2，参考 BililiveRecorder）
PLAY_URL_API = "https://api.live.bilibili.com/xlive/web-room/v2/index/getRoomPlayInfo"

# ========== 记忆 ==========
BOT_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot_data")
MEMORY_DB_PATH = os.path.join(BOT_DATA_DIR, "memory.db")
LONG_TERM_DB_PATH = os.path.join(BOT_DATA_DIR, "long_term.db")
# 每 10 分钟做一次 episode 摘要
EPISODE_INTERVAL_SECONDS = 600

# ========== 弹幕发送 ==========
SEND_DANMAKU_API = "https://api.live.bilibili.com/msg/send"
# 两次发送弹幕的最小间隔（秒）
DANMAKU_COOLDOWN = 5
# 礼物价值阈值（元），超过才自动感谢
GIFT_THANK_THRESHOLD = 0.1

# ========== ffmpeg ==========
FFMPEG_PATH = "ffmpeg"

# ========== 关闭标记 ==========
SHUTDOWN_FLAG_PATH = os.path.join(BOT_DATA_DIR, "clean_shutdown.flag")

# ========== WebSocket ==========
HEARTBEAT_INTERVAL = 30

from prompts import BOT_PERSONA, TIP_TEMPLATES

# ========== 人设 ==========
TIP_INTERVAL_SECONDS = 900   # 15分钟让LLM考虑一次贴士
DRINK_WATER_INTERVAL = 1800  # 30分钟喝水提醒
BOT_EAGERNESS = 0.3          # 积极度：0=极度沉默, 1=话唠
DEBUG_MODE = True            # 调试模式：开启后打印更多详细日志

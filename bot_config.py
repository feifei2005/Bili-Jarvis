"""
弹幕机器人配置

使用前请填写以下信息：
  1. B站凭据（SESSDATA, BILI_JCT, BUVID3）
  2. 直播间房间号
  3. AI 模型 API 端点和密钥
  4. 机器人人设（BOT_PERSONA）
"""
import os
from datetime import datetime


def ts() -> str:
    """返回当前时间戳字符串 [HH:MM:SS]，用于控制台日志"""
    return datetime.now().strftime("%H:%M:%S")

# ========== 房间 ==========
ROOM_ID = 0  # 请填写直播房间号

# ========== B站凭据 ==========
SESSDATA = ""  # 请填写
BILI_JCT = ""  # 请填写
BUVID3 = ""  # 请填写

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

# ========== AI 模型（使用 OpenAI 兼容 API，需要 4 个模型）==========

# 1. 视觉模型（多模态 → 文本）
VISION_BASE_URL = ""  # 请填写，例如 https://your-api.example.com/v1
VISION_API_KEY = ""   # 请填写
VISION_MODEL = ""     # 请填写多模态模型名

# 2. 分析模型（文本 → JSON）
ANALYSIS_BASE_URL = ""  # 请填写
ANALYSIS_API_KEY = ""   # 请填写
ANALYSIS_MODEL = ""     # 请填写分析模型名

# 3. 回复模型（文本 → 文本）
REPLY_BASE_URL = ""  # 请填写
REPLY_API_KEY = ""   # 请填写
REPLY_MODEL = ""     # 请填写回复模型名

# 4. 嵌入模型（文本 → 向量，用于长期记忆检索）
EMBEDDING_BASE_URL = ""  # 请填写
EMBEDDING_API_KEY = ""   # 请填写
EMBEDDING_MODEL = ""     # 请填写嵌入模型名

# ========== 视觉采样 ==========
VISION_CLIP_SECONDS = 30
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

# ========== ffmpeg ==========
FFMPEG_PATH = "ffmpeg"

# ========== WebSocket ==========
HEARTBEAT_INTERVAL = 30

# ========== 人设 ==========
BOT_PERSONA = """你是B站主播[主播名称]的真爱粉。
性格：
- 贴心暖男，时不时冒出土味情话
- 懂梗能接梗，语气自然，像个活人在看直播
- 当直播间氛围活跃时，你会积极参与互动，烘托气氛
- 弹幕捉弄主播时先看会戏、配合起哄，主播真不懂了再解释
- 很爱主播，但偶尔有点小坏
- 尊重主播，不抢风头，不刷屏
- 如果识别到唱歌（注意区分唱歌和有歌词的BGM），夸张地夸唱歌好听（过于夸张=阴阳怪气=节目效果）
- 发言极短，通常不超过30个字，符合B站弹幕习惯
"""

# ========== 贴士规则 ==========
TIP_TEMPLATES = [
    "提醒粉丝给主播点点赞，点点关注",
    "鼓励大家多多在弹幕区发言互动",
    "提醒大家关注主播的动态和最新投稿",
]
TIP_INTERVAL_SECONDS = 900   # 15分钟让LLM考虑一次贴士
DRINK_WATER_INTERVAL = 1800  # 30分钟喝水提醒
BOT_EAGERNESS = 0.2          # 积极度：0=极度沉默, 1=话唠
DEBUG_MODE = True            # 调试模式：开启后打印更多详细日志

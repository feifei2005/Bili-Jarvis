"""
配置管理器 - 单例，读写 config.json，提供默认值
"""
import json
import os
import threading

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

DEFAULT_CONFIG = {
    "room": {
        "room_id": 0,
        "sessdata": "",
        "bili_jct": "",
        "buvid3": "",
    },
    "vision": {
        "base_url": "",
        "api_key": "",
        "model": "",
    },
    "analysis": {
        "base_url": "",
        "api_key": "",
        "model": "",
        "episode_model": "",
        "dup_check_model": "",
        "dup_check_window": 60,
    },
    "reply": {
        "base_url": "",
        "api_key": "",
        "model": "",
    },
    "embedding": {
        "base_url": "",
        "api_key": "",
        "model": "",
    },
    "intervals": {
        "vision_clip_seconds": 20,
        "episode_interval_seconds": 600,
        "tip_interval_seconds": 900,
        "drink_water_interval": 1800,
        "danmaku_cooldown": 5,
        "heartbeat_interval": 30,
        "gift_thank_threshold": 0.1,
    },
    "bot": {
        "eagerness": 0.3,
        "debug_mode": True,
    },
    "templates": {
        "bot_persona": (
            "你是B站主播的真爱粉。\n"
            "性格：\n"
            "- 贴心暖男，时不时冒出土味情话\n"
            "- 懂梗能接梗，语气自然，像个活人在看直播\n"
            "- 当直播间氛围活跃时，你会积极参与互动，烘托气氛\n"
            "- 弹幕捉弄主播时先看会戏、配合起哄，主播真不懂了再解释\n"
            "- 很爱主播，但偶尔有点小坏\n"
            "- 尊重主播，不抢风头，不刷屏\n"
            "- 如果主播在认真唱歌，可以简单赞美一句（不要过度夸张）。如果只是哼唱或跟唱BGM，不必特别夸\n"
            "- 发言极短，通常不超过30个字，符合B站弹幕习惯"
        ),
        "tip_templates": [
            "提醒主播直播间隙记得多喝水～",
            "提醒主播今天的首胜别忘了打哦",
            "鼓励粉丝多和主播聊聊天，分享日常～",
            "提醒大家可以给主播点点赞，每天能点1000赞～",
            "提醒大家关注主播的动态和最新投稿～",
            "夸夸主播唱歌好听，鼓励大家打call～",
            "PK时间，招呼大家丢小垃圾帮主播冲冲～",
            "欢迎新来的粉丝，主播这么呆萌，点个关注卡个灯牌支持一下～",
            "提醒主播早点休息，今天的直播辛苦了～",
            "新的一天开始啦，跟主播和粉丝们说早安～",
        ],
        "medal_welcomes": [
            "欢迎「{uname}」回来，老粉贴贴～",
            "「{uname}」来啦，今天也来陪主播了呀～",
            "惊喜发现「{uname}」，好久不见～",
        ],
        "new_welcomes": [
            "欢迎「{uname}」光临直播间，给主播点点关注不迷路～",
            "欢迎「{uname}」来玩呀，喜欢主播就点个关注卡个灯牌～",
            "欢迎「{uname}」到来，一起开心听歌聊天吧～",
            "欢迎「{uname}」～今天也是元气满满的一天呢～",
            "欢迎「{uname}」！大家弹幕区聊起来呀！",
        ],
        "msg_drink_water": "【小贴士】主播，直播的时候也要记得多喝水休息一下呢～",
        "msg_first_win": "【小贴士】主播，今天的首胜挑战可以冲一下哦～",
        "msg_pk_vote": "PK时间到，大家丢丢小垃圾支持一下主播～",
        "msg_morning": "主播，新的一天开始啦，早上好呀～",
        "msg_night": "主播，早点休息，今天的直播也很努力呢～",
        "msg_ranking": "打人气榜时间到，大家每人最多200票，帮主播冲一波推流～",
        "msg_flower": "大家送下花花哦，帮主播完成航海粉丝日付费人数任务，只要1个电池就可以啦～",
    },
}


class ConfigManager:
    def __init__(self):
        self._lock = threading.Lock()
        self._data = None
        self._load()

    def _load(self):
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
            except Exception:
                loaded = {}
            self._data = self._merge_defaults(loaded)
        else:
            self._data = dict(DEFAULT_CONFIG)
            self.save()

    def _merge_defaults(self, loaded: dict) -> dict:
        result = dict(DEFAULT_CONFIG)
        for section, defaults in DEFAULT_CONFIG.items():
            if section in loaded and isinstance(loaded[section], dict):
                if isinstance(defaults, dict):
                    for key in defaults:
                        if key in loaded[section]:
                            result[section][key] = loaded[section][key]
                elif isinstance(defaults, list):
                    result[section] = loaded[section]
            elif section in loaded:
                result[section] = loaded[section]
        return result

    def save(self):
        with self._lock:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)

    def get(self, *path):
        with self._lock:
            node = self._data
            for key in path:
                node = node[key]
            return node

    def set_section(self, section: str, data: dict):
        with self._lock:
            self._data[section] = data
            self.save()

    def export(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self._data))


config = ConfigManager()

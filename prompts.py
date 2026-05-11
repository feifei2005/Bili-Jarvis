"""
所有提示词、贴士模板、欢迎词、规则引擎消息 - 从 config.json 读取
"""
import random

from config_manager import config as _config
_c = _config
_t = lambda k: _c.get("templates", k)

# ========== BOT_PERSONA（人设）==========
BOT_PERSONA = _t("bot_persona")

# ========== TIP_TEMPLATES（LLM 贴士灵感池）==========
TIP_TEMPLATES = _t("tip_templates")

# ========== 欢迎词 ==========
MEDAL_WELCOMES = _t("medal_welcomes")
NEW_WELCOMES = _t("new_welcomes")

# ========== 规则引擎消息 ==========
MSG_DRINK_WATER = _t("msg_drink_water")
MSG_FIRST_WIN = _t("msg_first_win")
MSG_PK_VOTE = _t("msg_pk_vote")
MSG_MORNING = _t("msg_morning")
MSG_NIGHT = _t("msg_night")
MSG_RANKING = _t("msg_ranking")
MSG_FLOWER = _t("msg_flower")

# ========== 发言准则（Eagerness Tiers）==========

def get_eagerness_text(eagerness: float) -> str:
    if eagerness < 0.2:
        return "你极度沉默。只在发生重大事件（首胜、高额礼物、主播直接点名叫你）时才开口。除此之外一律不说话。"
    elif eagerness < 0.3:
        return "你比较沉默。有值得接的有趣梗或弹幕氛围热烈时才发言，普通闲聊一般不回复。"
    elif eagerness < 0.4:
        return "你非常沉默寡言。只在出现真正值得开口的内容（精彩操作、有趣梗、主播直接互动提问、大礼物、弹幕区有热门话题值得接一句）时才发言。日常画面和平淡闲聊绝对不回复。"
    elif eagerness < 0.6:
        return "你偶尔参与互动。只在有明显新鲜事或弹幕氛围热烈需要烘托时才说一句，不抢话不水屏，可聊可不聊时选择沉默。"
    elif eagerness < 0.8:
        return "你适度参与互动。有好玩的内容可以接梗捧场，但仍然克制，避免连续发言和重复内容。"
    else:
        return "你比较活跃，愿意接大多数话题，但还是不发无意义的寒暄。"

# ========== analyze() Prompt 模板 ==========

ANALYZE_PROMPT_PREFIX = (
    "你是一个直播间分析员。根据以下信息分析当前直播状态。\n"
    "当前互动标准：{eagerness_text}\n"
    "返回 JSON，不要 markdown 包裹：\n"
    "{\n"
    '  "summary": "当前局势一句话概括",\n'
    '  "is_singing": false, // 仅主播在认真唱歌时才为 true（哼唱/跟唱BGM不算）。仅播放背景音乐不算\n'
    '  "is_pk": false,\n'
    '  "first_win": false,\n'
    '  "worth_responding": false, // 默认不回复。只有出现首胜/大礼物/主播直接提问/弹幕区有值得接的有趣梗/视听上的精彩瞬间等实质性事件才设为 true。普通日常画面、无新信息闲聊不触发\n'
    '  "memo_save": null,    // 听到/看到新信息才存。如果备忘录里已有类似内容，严禁重复存！\n'
    '  "memo_delete": null\n'
    "}\n\n"
    "{context}"
)

# ========== respond() 状态标签 ==========

SINGING_LABEL = "[状态] 主播正在唱歌！（若实际只是播放BGM，请忽略此状态，不要夸唱歌）"
PK_LABEL = "[状态] 正在PK！"
MEMORY_LABEL = "[你记得的往事（仅供参考，不要硬套历史话题）]"

# ========== respond() Prompt ==========

RESPOND_TIP_PROMPT = (
    "{context}\n\n"
    "规则：弹幕不超过40个字。\n"
    "你现在是贴士模式——根据[贴士灵感]和[当前状况]，主动生成一条有用/有趣/应景的弹幕。\n"
    "不需要等待特别的事件，你的职责就是帮直播间活跃气氛。\n"
    '返回 {"reply": true, "msg": "..."}\n'
    "只返回 JSON。"
)

RESPOND_NORMAL_PROMPT = (
    "{context}\n\n"
    "规则：弹幕不超过40个字。\n"
    "默认不说话。只有当[当前状况]中有真正值得评论或接话的内容时才开口——例如有趣的梗、主播明显的互动请求、值得赞叹的精彩瞬间、弹幕区有热门话题值得接一句。\n"
    "如果画面和平常一样、弹幕都是日常闲聊，不要为了说话而硬凑。\n"
    '如果要说，返回 {"reply": true, "msg": "..."}\n'
    '如果不需要说，返回 {"reply": false}\n'
    "只返回 JSON。"
)

# ========== DupCheck Prompt ==========

DUP_CHECK_PROMPT = (
    "你是一个去重检查器。判断「新话题」是否与「最近已发送弹幕的话题」语义重复。\n"
    "[最近已发送弹幕的话题]\n{recent}\n\n"
    "[新话题]\n{summary}\n\n"
    '如果新话题与最近话题雷同（都是夸唱歌/夸氛围/聊同一件事），返回 {"duplicate": true}\n'
    '如果话题不同，返回 {"duplicate": false}\n'
    "只返回 JSON。"
)

# ========== vision prompt ==========

VISION_PROMPT = (
    "你是一个直播画面观察员。直接输出描述内容，禁止任何开场白和结尾语。"
    "禁止使用'好的''没问题''以下是''为您描述''综上所述'等套话。第一句直接说主播在做什么。"
    "请简洁描述这段直播视频中发生了什么。"
    "重点关注：主播在做什么、游戏/活动内容、画面中的文字信息、"
    "主播的情绪反应、任何值得注意的事件。"
    "关于音乐/歌声：请区分「主播在认真唱歌（开麦演唱）」和「只是跟着BGM哼唱/跟唱」以及「纯播放BGM」。"
    "跟唱、哼唱不算唱歌，单独注明即可。"
    "如果只是播放BGM而主播没有跟唱，请明确注明'播放BGM中，主播未跟唱'。"
    "如果有多个视频片段，它们是连续的，请综合描述。"
    "用2-3句话描述即可。"
    "注意：不要猜测或提及主播的名字，一律用「主播」指代。"
)

# ========== 辅助函数 ==========

def get_welcome_msg(uname: str, has_medal: bool) -> str | None:
    """返回欢迎消息，用 {uname} 模板格式化"""
    if has_medal:
        return random.choice(MEDAL_WELCOMES).format(uname=uname)
    return random.choice(NEW_WELCOMES).format(uname=uname)

def get_random_tips(count: int = 3) -> list[str]:
    """返回随机贴士灵感"""
    return random.sample(TIP_TEMPLATES, min(count, len(TIP_TEMPLATES)))

"""
智能大脑 - 三级流水线的第二、第三级

第二级 analyze()：分析局势，输出状态标记 + 备忘操作 + 简报
第三级 respond()：根据简报 + 人设，决定是否回复 + 回复内容
"""
import asyncio
import json
import time
from typing import Optional

import aiohttp
import httpx

from bot_config import (
    ROOM_ID, BILI_JCT, BILI_HEADERS, BILI_COOKIES,
    ANALYSIS_BASE_URL, ANALYSIS_API_KEY, ANALYSIS_MODEL,
    REPLY_BASE_URL, REPLY_API_KEY, REPLY_MODEL,
    SEND_DANMAKU_API, DANMAKU_COOLDOWN,
    BOT_PERSONA, TIP_TEMPLATES, BOT_EAGERNESS, DEBUG_MODE, ts,
)
from memory_manager import (
    get_recent_danmaku, get_recent_vision, search_memories,
    get_active_memos, save_memo, delete_memo_by_keyword,
)
from log_util import log

_last_send_time = 0.0


async def send_danmaku(session: aiohttp.ClientSession, room_id: int, msg: str) -> bool:
    global _last_send_time
    now = time.time()
    if now - _last_send_time < DANMAKU_COOLDOWN:
        await asyncio.sleep(DANMAKU_COOLDOWN - (now - _last_send_time))
    msg = msg[:40]

    data = {
        "color": 16777215,
        "fontsize": 25,
        "mode": 1,
        "msg": msg,
        "rnd": int(time.time()),
        "roomid": room_id,
        "bubble": 0,
        "csrf_token": BILI_JCT,
        "csrf": BILI_JCT,
    }

    _last_send_time = time.time()
    try:
        async with session.post(
            SEND_DANMAKU_API,
            data=data,
            headers=BILI_HEADERS,
            cookies=BILI_COOKIES,
            ssl=False,
            timeout=10
        ) as resp:
            if resp.status == 200:
                result = await resp.json()
                if result.get("code") == 0:
                    print(f"[{ts()}] [Brain] 弹幕发送成功: {msg}")
                    return True
                else:
                    print(f"[{ts()}] [Brain] 弹幕发送失败: {result}")
            else:
                print(f"[{ts()}] [Brain] 弹幕请求失败, HTTP {resp.status}")
    except Exception as e:
        print(f"[{ts()}] [Brain] 弹幕异常: {e}")
    return False


def _call_model_sync(base_url: str, api_key: str, model: str, prompt: str, max_tokens: int = 16000) -> Optional[str]:
    """同步调用模型（通用）"""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            if attempt == 0:
                print(f"[{ts()}] [Brain] 请求 LLM: {model}")
            resp = httpx.post(
                f"{base_url}/chat/completions",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": max_tokens,
                },
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=60.0,
            )
            if resp.status_code == 200:
                content = resp.json()["choices"][0]["message"].get("content")
                return content
            else:
                resp_text = resp.text
                print(f"[{ts()}] [Brain] API 失败 (尝试 {attempt+1}/{max_retries}): {resp.status_code} {resp_text[:200]}")
                log(f"[ERROR] [Brain] API 失败 (尝试 {attempt+1}/{max_retries}) model={model}: {resp.status_code} {resp_text}")
        except Exception as e:
            print(f"[{ts()}] [Brain] 异常 (尝试 {attempt+1}/{max_retries}): {type(e).__name__}: {e}")
            log(f"[ERROR] [Brain] 异常 (尝试 {attempt+1}/{max_retries}) model={model}: {type(e).__name__}: {e}")
        if attempt < max_retries - 1:
            time.sleep(2 ** attempt)
    return None


async def _call_analysis(prompt: str, max_tokens: int = 16000) -> Optional[str]:
    """异步调用分析模型"""
    return await asyncio.to_thread(_call_model_sync, ANALYSIS_BASE_URL, ANALYSIS_API_KEY, ANALYSIS_MODEL, prompt, max_tokens)


async def _call_reply(prompt: str, max_tokens: int = 16000) -> Optional[str]:
    """异步调用回复模型"""
    return await asyncio.to_thread(_call_model_sync, REPLY_BASE_URL, REPLY_API_KEY, REPLY_MODEL, prompt, max_tokens)


def _parse_json(text: str) -> Optional[dict]:
    """从可能带 markdown 包裹的文本中提取 JSON"""
    if not text:
        return None
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass
    return None


def _get_eagerness_text(eagerness: float) -> str:
    if eagerness < 0.2:
        return "你极度沉默。只在发生重大事件（首胜、高额礼物、主播直接点名叫你）时才开口。除此之外一律不说话。"
    elif eagerness < 0.4:
        return "你非常沉默寡言。只在出现真正值得开口的内容（精彩操作、有趣梗、主播直接互动提问、大礼物、弹幕区有热门话题值得接一句）时才发言。日常画面和平淡闲聊绝对不回复。"
    elif eagerness < 0.6:
        return "你偶尔参与互动。只在有明显新鲜事或弹幕氛围热烈需要烘托时才说一句，不抢话不水屏，可聊可不聊时选择沉默。"
    elif eagerness < 0.8:
        return "你适度参与互动。有好玩的内容可以接梗捧场，但仍然克制，避免连续发言和重复内容。"
    else:
        return "你比较活跃，愿意接大多数话题，但还是不发无意义的寒暄。"


# ========== 第二级：分析 ==========

async def analyze(
    session_id: int,
    current_vision: Optional[str] = None,
    room_title: Optional[str] = None,
) -> Optional[dict]:
    """分析当前局势。"""
    recent_dm = get_recent_danmaku(session_id, limit=20)
    recent_vis = get_recent_vision(session_id, limit=3)
    memos = get_active_memos(limit=15)

    parts = []
    if room_title:
        parts.append(f"[直播间标题] {room_title}")

    if current_vision:
        parts.append(f"[最新画面+声音描述] {current_vision}")
    if recent_vis:
        for v in recent_vis[:-1]:
            parts.append(f"[历史画面] {v['description'][:100]}")

    if recent_dm:
        dm_lines = "\n".join(f"  {d['uname']}: {d['content']}" for d in recent_dm[-15:])
        parts.append(f"[最近弹幕]\n{dm_lines}")

    if memos:
        memo_lines = "\n".join(f"  [{m['category']}] {m['content']}" for m in memos)
        parts.append(f"[备忘录]\n{memo_lines}")

    context = "\n\n".join(parts)
    eagerness_text = _get_eagerness_text(BOT_EAGERNESS)

    prompt = (
        "你是一个直播间分析员。根据以下信息分析当前直播状态。\n"
        f"当前互动标准：{eagerness_text}\n"
        "返回 JSON，不要 markdown 包裹：\n"
        "{\n"
        '  "summary": "当前局势一句话概括",\n'
        '  "is_singing": false,\n'
        '  "is_pk": false,\n'
        '  "first_win": false,\n'
        '  "worth_responding": false, // 默认不回复。只有出现首胜/大礼物/主播直接提问/弹幕区有值得接的有趣梗/视听上的精彩瞬间等实质性事件才设为 true。普通日常画面、无新信息闲聊不触发\n'
        '  "memo_save": null,    // 听到/看到新信息才存。如果备忘录里已有类似内容，严禁重复存！\n'
        '  "memo_delete": null\n'
        "}\n\n"
        f"{context}"
    )

    if DEBUG_MODE:
        print(f"[DEBUG] Analyze Prompt:\n{prompt[:500]}...")

    content = await _call_analysis(prompt)
    if DEBUG_MODE:
        print(f"[DEBUG] Analyze Raw: {content}")

    result = _parse_json(content)

    if result:
        if result.get("memo_save"):
            ms = result["memo_save"]
            if isinstance(ms, dict) and ms.get("content"):
                save_memo(ms["content"], ms.get("category", "note"))
        if result.get("memo_delete"):
            delete_memo_by_keyword(str(result["memo_delete"]))
        print(f"[Analyst] {result.get('summary', '?')} | worth={result.get('worth_responding')}")
    return result


# ========== 第三级：回复 ==========

async def respond(
    session: aiohttp.ClientSession,
    room_id: int,
    analysis: dict,
    session_id: int,
    is_tip_mode: bool = False,
) -> Optional[str]:
    """决定回复内容。"""
    summary = analysis.get("summary", "直播中")
    memories = search_memories(summary, limit=2)

    parts = [BOT_PERSONA]
    parts.append(f"[当前状况] {summary}")
    
    eagerness_text = _get_eagerness_text(BOT_EAGERNESS)
    parts.append(f"[发言准则] {eagerness_text}")

    if analysis.get("is_singing"):
        parts.append("[状态] 主播正在唱歌！")
    if analysis.get("is_pk"):
        parts.append("[状态] 正在PK！")

    if memories:
        mem_text = "\n".join(f"  [{m['session_date']}] {m['title']}" for m in memories)
        parts.append(f"[你记得的往事]\n{mem_text}")

    if is_tip_mode:
        import random
        tips = random.sample(TIP_TEMPLATES, min(3, len(TIP_TEMPLATES)))
        parts.append(f"[贴士灵感] {', '.join(tips)}")

    recent_dm = get_recent_danmaku(session_id, limit=8)
    if recent_dm:
        dm_lines = "\n".join(f"  {d['uname']}: {d['content']}" for d in recent_dm[-5:])
        parts.append(f"[最近弹幕氛围]\n{dm_lines}")

    context = "\n\n".join(parts)
    prompt = (
        f"{context}\n\n"
        "规则：弹幕不超过40个字。\n"
        "默认不说话。只有当[当前状况]中有真正值得评论或接话的内容时才开口——例如有趣的梗、主播明显的互动请求、值得赞叹的精彩瞬间、弹幕区有热门话题值得接一句。\n"
        "如果画面和平常一样、弹幕都是日常闲聊，不要为了说话而硬凑。\n"
        '如果要说，返回 {"reply": true, "msg": "..."}\n'
        '如果不需要说，返回 {"reply": false}\n'
        "只返回 JSON。"
    )

    if DEBUG_MODE:
        print(f"[DEBUG] Respond Prompt:\n{prompt[:500]}...")

    content = await _call_reply(prompt)
    if DEBUG_MODE:
        print(f"[DEBUG] Respond Raw: {content}")

    result = _parse_json(content)

    if result and result.get("reply") and result.get("msg"):
        msg = result["msg"][:40]
        await send_danmaku(session, room_id, msg)
        return msg
    return None


# ========== 组合调用（给 bot.py 用）==========

async def think_and_reply(
    session: aiohttp.ClientSession,
    session_id: int,
    room_id: int,
    current_vision: Optional[str] = None,
    room_title: Optional[str] = None,
    is_tip_mode: bool = False,
) -> tuple[Optional[dict], Optional[str]]:
    analysis = await analyze(session_id, current_vision, room_title)
    if not analysis:
        return None, None
    reply = None
    if analysis.get("worth_responding") or is_tip_mode:
        reply = await respond(session, room_id, analysis, session_id, is_tip_mode)
    return analysis, reply

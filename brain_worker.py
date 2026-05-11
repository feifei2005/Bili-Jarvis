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
    BOT_PERSONA, BOT_EAGERNESS, DEBUG_MODE, ts,
    GEMINI_FORMAT_MODELS, DUP_CHECK_MODEL, DUP_CHECK_WINDOW,
)
from prompts import (
    get_eagerness_text,
    ANALYZE_PROMPT_PREFIX,
    SINGING_LABEL, PK_LABEL, MEMORY_LABEL,
    RESPOND_TIP_PROMPT, RESPOND_NORMAL_PROMPT,
    DUP_CHECK_PROMPT,
    get_random_tips,
)
from memory_manager import (
    get_recent_danmaku, get_recent_vision, search_memories,
    get_active_memos, save_memo, delete_memo_by_keyword,
)
from log_util import log
from bot_state import global_state

_last_send_time = 0.0
_last_bot_msg = ""
_llm_sends = []  # [(timestamp, summary)]  LLM 发言历史，用于去重
_send_queue = None  # asyncio.Queue，由 bot.py 初始化


def _init_send_queue():
    global _send_queue
    if _send_queue is None:
        _send_queue = asyncio.Queue()


async def _send_worker(session: aiohttp.ClientSession, room_id: int):
    """串行发送队列，每条间隔 5 秒"""
    global _last_bot_msg, _send_queue
    _init_send_queue()
    while True:
        msg = await _send_queue.get()
        msg = msg[:40]
        data = {
            "color": 16777215, "fontsize": 25, "mode": 1,
            "msg": msg, "rnd": int(time.time()), "roomid": room_id,
            "bubble": 0, "csrf_token": BILI_JCT, "csrf": BILI_JCT,
        }
        try:
            async with session.post(
                SEND_DANMAKU_API, data=data,
                headers=BILI_HEADERS, cookies=BILI_COOKIES, ssl=False, timeout=10
            ) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    if result.get("code") == 0:
                        _last_bot_msg = msg
                        if global_state:
                            global_state.add_send(msg)
                        print(f"[{ts()}] [Brain] 弹幕发送成功: {msg}")
                        log(f"[Send] {msg}")
                    else:
                        print(f"[{ts()}] [Brain] 弹幕发送失败: {result}")
                else:
                    print(f"[{ts()}] [Brain] 弹幕请求失败, HTTP {resp.status}")
        except Exception as e:
            print(f"[{ts()}] [Brain] 弹幕异常: {type(e).__name__}: {e}")
        await asyncio.sleep(5)


async def send_danmaku(session, room_id: int, msg: str):
    """发送弹幕（入队，兼容旧调用签名）"""
    global _send_queue
    if _send_queue is None:
        _init_send_queue()
    await _send_queue.put(msg)


def _call_model_sync(base_url: str, api_key: str, model: str, prompt: str, max_tokens: int = 16000) -> Optional[str]:
    """同步调用模型（通用）"""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            if attempt == 0:
                print(f"[{ts()}] [Brain] 请求 LLM: {model}")
            if model in GEMINI_FORMAT_MODELS:
                body = {
                    "model": model,
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"maxOutputTokens": max_tokens},
                }
            else:
                body = {
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": max_tokens,
                }
            resp = httpx.post(
                f"{base_url}/chat/completions",
                json=body,
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


def _should_skip_duplicate(summary: str) -> bool:
    """调 DUP_CHECK_MODEL 判断当前局势是否与最近发言语义重复"""
    global _llm_sends
    now = time.time()
    _llm_sends = [(t, s) for t, s in _llm_sends if now - t < DUP_CHECK_WINDOW]
    if not _llm_sends:
        return False  # 无历史，放行

    recent = "\n".join(f"- {s}" for _, s in _llm_sends[-5:])
    prompt = DUP_CHECK_PROMPT.replace("{recent}", recent).replace("{summary}", summary)

    content = _call_model_sync(ANALYSIS_BASE_URL, ANALYSIS_API_KEY, DUP_CHECK_MODEL, prompt, max_tokens=50)
    if not content:
        return False  # LLM 失败 → 放行
    result = _parse_json(content)
    if result and result.get("duplicate"):
        print(f"[{ts()}] [DupSkip] 语义重复，跳过: {summary[:40]}")
        log(f"[DupSkip] {summary[:80]}")
        return True
    return False


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
            parts.append(f"[参考-前几秒画面] {v['description']}")

    if recent_dm:
        filtered = [d for d in recent_dm if d['content'] != _last_bot_msg]
        dm_lines = "\n".join(f"  {d['uname']}: {d['content']}" for d in filtered[-15:])
        parts.append(f"[最近弹幕]\n{dm_lines}")

    if memos:
        memo_lines = "\n".join(f"  [{m['category']}] {m['content']}" for m in memos)
        parts.append(f"[备忘录]\n{memo_lines}")

    context = "\n\n".join(parts)
    eagerness_text = get_eagerness_text(BOT_EAGERNESS)

    prompt = ANALYZE_PROMPT_PREFIX.replace("{eagerness_text}", eagerness_text).replace("{context}", context)

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
        log(f"[Analyst] {result.get('summary', '?')} | worth={result.get('worth_responding')}")
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
    
    eagerness_text = get_eagerness_text(BOT_EAGERNESS)
    parts.append(f"[发言准则] {eagerness_text}")

    if analysis.get("is_singing"):
        parts.append(SINGING_LABEL)
    if analysis.get("is_pk"):
        parts.append(PK_LABEL)

    if memories:
        mem_text = "\n".join(f"  [{m['session_date']}] {m['title']}" for m in memories)
        parts.append(f"{MEMORY_LABEL}\n{mem_text}")

    if is_tip_mode:
        tips = get_random_tips(3)
        parts.append(f"[贴士灵感] {', '.join(tips)}")

    recent_dm = get_recent_danmaku(session_id, limit=8)
    if recent_dm:
        dm_lines = "\n".join(f"  {d['uname']}: {d['content']}" for d in recent_dm[-5:])
        parts.append(f"[最近弹幕氛围]\n{dm_lines}")

    context = "\n\n".join(parts)
    if is_tip_mode:
        prompt = RESPOND_TIP_PROMPT.replace("{context}", context)
    else:
        prompt = RESPOND_NORMAL_PROMPT.replace("{context}", context)

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
    global _llm_sends
    analysis = await analyze(session_id, current_vision, room_title)
    if not analysis:
        return None, None
    reply = None
    if analysis.get("worth_responding") or is_tip_mode:
        summary = analysis.get("summary", "")
        if _should_skip_duplicate(summary):
            return analysis, None
        _llm_sends.append((time.time(), summary))  # 占位，防止并发绕过
        reply = await respond(session, room_id, analysis, session_id, is_tip_mode)
    return analysis, reply

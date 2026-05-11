"""
记忆管理器

生命周期：
  直播中 → SQLite 记录视觉描述、弹幕、10分钟 Episode
  下播后 → LLM 深度整理 → 向量化存入长期库 → 清空本场原始数据
  互动时 → 从长期库检索相关记忆
"""
import sqlite3
import json
import time
import os
import base64
import struct
from datetime import datetime
from typing import Optional

import httpx

from bot_config import (
    BOT_DATA_DIR, MEMORY_DB_PATH, LONG_TERM_DB_PATH,
    ANALYSIS_BASE_URL, ANALYSIS_API_KEY, EPISODE_MODEL, 
    EMBEDDING_BASE_URL, EMBEDDING_API_KEY, EMBEDDING_MODEL,
    EPISODE_INTERVAL_SECONDS, ts,
)
from log_util import log


def _ensure_dir():
    os.makedirs(BOT_DATA_DIR, exist_ok=True)


# ========== 短期记忆（SQLite: 当场直播的原始数据）==========

def init_short_term_db():
    _ensure_dir()
    conn = sqlite3.connect(MEMORY_DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            start_time TEXT NOT NULL,
            end_time TEXT,
            status TEXT DEFAULT 'live'
        );
        CREATE TABLE IF NOT EXISTS vision_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            ts TEXT NOT NULL,
            description TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS danmaku_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            ts TEXT NOT NULL,
            uname TEXT NOT NULL,
            content TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS episodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            window_start TEXT NOT NULL,
            window_end TEXT NOT NULL,
            title TEXT NOT NULL,
            summary TEXT NOT NULL,
            keywords TEXT DEFAULT '[]',
            participants TEXT DEFAULT '[]'
        );
        CREATE TABLE IF NOT EXISTS memos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            content TEXT NOT NULL,
            category TEXT DEFAULT 'note',
            sticky INTEGER DEFAULT 0,
            expires_at TEXT
        );
        CREATE TABLE IF NOT EXISTS visitors (
            uid TEXT PRIMARY KEY,
            uname TEXT,
            first_seen TEXT NOT NULL,
            has_spoken INTEGER DEFAULT 0,
            visit_count INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS welcomed_blacklist (
            uid TEXT PRIMARY KEY,
            uname TEXT,
            added_at TEXT NOT NULL
        );
    """)
    conn.close()


def start_session() -> int:
    conn = sqlite3.connect(MEMORY_DB_PATH)
    cur = conn.execute(
        "INSERT INTO sessions (start_time) VALUES (?)",
        (datetime.now().isoformat(),)
    )
    session_id = cur.lastrowid
    conn.commit()
    conn.close()
    return session_id


def end_session(session_id: int):
    conn = sqlite3.connect(MEMORY_DB_PATH)
    conn.execute(
        "UPDATE sessions SET end_time=?, status='ended' WHERE id=?",
        (datetime.now().isoformat(), session_id)
    )
    conn.commit()
    conn.close()


def save_vision(session_id: int, description: str):
    conn = sqlite3.connect(MEMORY_DB_PATH)
    conn.execute(
        "INSERT INTO vision_logs (session_id, ts, description) VALUES (?, ?, ?)",
        (session_id, datetime.now().isoformat(), description)
    )
    conn.commit()
    conn.close()


def save_danmaku(session_id: int, uname: str, content: str):
    conn = sqlite3.connect(MEMORY_DB_PATH)
    conn.execute(
        "INSERT INTO danmaku_logs (session_id, ts, uname, content) VALUES (?, ?, ?, ?)",
        (session_id, datetime.now().isoformat(), uname, content)
    )
    conn.commit()
    conn.close()


def get_recent_danmaku(session_id: int, limit: int = 30) -> list[dict]:
    conn = sqlite3.connect(MEMORY_DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT ts, uname, content FROM danmaku_logs WHERE session_id=? ORDER BY id DESC LIMIT ?",
        (session_id, limit)
    ).fetchall()
    conn.close()
    return [dict(r) for r in reversed(rows)]


def get_recent_vision(session_id: int, limit: int = 3) -> list[dict]:
    conn = sqlite3.connect(MEMORY_DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT ts, description FROM vision_logs WHERE session_id=? ORDER BY id DESC LIMIT ?",
        (session_id, limit)
    ).fetchall()
    conn.close()
    return [dict(r) for r in reversed(rows)]


def get_window_data(session_id: int, window_start: str) -> dict:
    """获取某个时间窗口内的所有视觉和弹幕数据，用于 Episode 整理"""
    conn = sqlite3.connect(MEMORY_DB_PATH)
    conn.row_factory = sqlite3.Row
    visions = conn.execute(
        "SELECT ts, description FROM vision_logs WHERE session_id=? AND ts>=? ORDER BY ts",
        (session_id, window_start)
    ).fetchall()
    danmakus = conn.execute(
        "SELECT ts, uname, content FROM danmaku_logs WHERE session_id=? AND ts>=? ORDER BY ts",
        (session_id, window_start)
    ).fetchall()
    conn.close()
    return {
        "visions": [dict(r) for r in visions],
        "danmakus": [dict(r) for r in danmakus],
    }


def save_episode(session_id: int, window_start: str, window_end: str,
                 title: str, summary: str, keywords: list, participants: list):
    conn = sqlite3.connect(MEMORY_DB_PATH)
    conn.execute(
        "INSERT INTO episodes (session_id, window_start, window_end, title, summary, keywords, participants) VALUES (?,?,?,?,?,?,?)",
        (session_id, window_start, window_end, title, summary,
         json.dumps(keywords, ensure_ascii=False),
         json.dumps(participants, ensure_ascii=False))
    )
    conn.commit()
    conn.close()


def get_session_episodes(session_id: int) -> list[dict]:
    conn = sqlite3.connect(MEMORY_DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM episodes WHERE session_id=? ORDER BY window_start",
        (session_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def purge_session_raw_data(session_id: int):
    """清空某场直播的原始切片数据（视觉+弹幕），保留 episodes"""
    conn = sqlite3.connect(MEMORY_DB_PATH)
    conn.execute("DELETE FROM vision_logs WHERE session_id=?", (session_id,))
    conn.execute("DELETE FROM danmaku_logs WHERE session_id=?", (session_id,))
    conn.commit()
    conn.close()


# ========== 长期记忆（SQLite + 手动 Embedding 向量检索）==========

def init_long_term_db():
    _ensure_dir()
    conn = sqlite3.connect(LONG_TERM_DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            session_date TEXT NOT NULL,
            title TEXT NOT NULL,
            summary TEXT NOT NULL,
            keywords TEXT DEFAULT '[]',
            embedding BLOB
        );
    """)
    conn.close()


def _get_embedding(text: str) -> Optional[list[float]]:
    """调用 AI API 获取文本的 embedding 向量 (带指数退避)"""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            resp = httpx.post(
                f"{EMBEDDING_BASE_URL}/embeddings",
                json={"model": EMBEDDING_MODEL, "input": text},
                headers={"Authorization": f"Bearer {EMBEDDING_API_KEY}"},
                timeout=30.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                return data["data"][0]["embedding"]
            else:
                resp_text = resp.text
                print(f"[{ts()}] [Memory] Embedding 请求失败 (尝试 {attempt+1}/{max_retries}): {resp.status_code} {resp_text[:200]}")
                log(f"[ERROR] [Memory] Embedding 请求失败 (尝试 {attempt+1}/{max_retries}): {resp.status_code} {resp_text}")
        except Exception as e:
            print(f"[{ts()}] [Memory] Embedding 异常 (尝试 {attempt+1}/{max_retries}): {type(e).__name__}: {e}")
            log(f"[ERROR] [Memory] Embedding 异常 (尝试 {attempt+1}/{max_retries}): {type(e).__name__}: {e}")

        if attempt < max_retries - 1:
            time.sleep(2 ** attempt)

    return None


def _encode_vector(vec: list[float]) -> bytes:
    # 动态适应任何维度的嵌入向量（如 4096维、1024维、768维等）
    return struct.pack(f"<{len(vec)}f", *vec)


def _decode_vector(blob: bytes) -> list[float]:
    # 动态计算维度大小，4个字节为一个 float32
    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob))


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    # 注意：如果中途更换了不同维度的 Embedding 模型，
    # a 和 b 的维度长度会不一致，导致计算毫无意义甚至报错。
    # 更换模型后，请务必清空数据库重新开始，代码此处本身不需要修改。
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def save_long_term_memory(session_date: str, title: str, summary: str, keywords: list):
    embedding = _get_embedding(f"{title} {summary}")
    embedding_blob = _encode_vector(embedding) if embedding else None

    conn = sqlite3.connect(LONG_TERM_DB_PATH)
    conn.execute(
        "INSERT INTO memories (created_at, session_date, title, summary, keywords, embedding) VALUES (?,?,?,?,?,?)",
        (datetime.now().isoformat(), session_date, title, summary,
         json.dumps(keywords, ensure_ascii=False), embedding_blob)
    )
    conn.commit()
    conn.close()


def search_memories(query: str, limit: int = 5) -> list[dict]:
    """语义检索长期记忆"""
    query_vec = _get_embedding(query)
    if not query_vec:
        return []

    conn = sqlite3.connect(LONG_TERM_DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, session_date, title, summary, keywords, embedding FROM memories WHERE embedding IS NOT NULL"
    ).fetchall()
    conn.close()

    scored = []
    for row in rows:
        row_dict = dict(row)
        mem_vec = _decode_vector(row_dict.pop("embedding"))
        score = _cosine_similarity(query_vec, mem_vec)
        row_dict["score"] = score
        scored.append(row_dict)

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:limit]


# ========== Episode 生成（LLM 调用）==========

def generate_episode(window_data: dict) -> Optional[dict]:
    """
    用 LLM 将一个时间窗口的原始数据整理成 Episode。
    参考 MaiBot 的 episode_segmentation_service.py 的 prompt 结构。
    """
    lines = []
    for v in window_data["visions"]:
        lines.append(f"[{v['ts']}] [画面] {v['description']}")
    for d in window_data["danmakus"]:
        lines.append(f"[{d['ts']}] [弹幕] {d['uname']}: {d['content']}")
    lines.sort()

    if not lines:
        return None

    text_block = "\n".join(lines)

    prompt = (
        "你是一个直播记忆整理引擎。根据以下直播间的实时记录（包含画面描述和弹幕），"
        "提炼出这段时间内发生的核心事件。\n"
        "返回 JSON，不要 markdown 包裹，不要解释：\n"
        '{"title": "事件标题", "summary": "详细摘要（2-3句话）", '
        '"keywords": ["关键词1","关键词2"], "participants": ["参与者1"]}\n\n'
        f"直播记录：\n{text_block}"
    )

    max_retries = 3
    for attempt in range(max_retries):
        try:
            resp = httpx.post(
                f"{ANALYSIS_BASE_URL}/chat/completions",
                json={
                    "model": EPISODE_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 16000,
                },
                headers={"Authorization": f"Bearer {ANALYSIS_API_KEY}"},
                timeout=60.0,
            )
            if resp.status_code == 200:
                resp_json = resp.json()
                content = resp_json["choices"][0]["message"].get("content")
                if content is None:
                    print(f"[{ts()}] [Memory] Episode 返回内容为空 (可能被过滤或模型不支持): {resp_json}")
                    continue
                # 清理可能的 markdown 包裹
                content = content.strip()
                if content.startswith("```"):
                    content = content.split("```")[1]
                    if content.startswith("json"):
                        content = content[4:]
                start = content.find("{")
                end = content.rfind("}")
                if start >= 0 and end > start:
                    return json.loads(content[start:end + 1])
            else:
                resp_text = resp.text
                print(f"[{ts()}] [Memory] Episode 生成失败 (尝试 {attempt+1}/{max_retries}): {resp.status_code} {resp_text[:200]}")
                log(f"[ERROR] [Memory] Episode 生成失败 (尝试 {attempt+1}/{max_retries}): {resp.status_code} {resp_text}")
        except Exception as e:
            print(f"[{ts()}] [Memory] Episode 异常 (尝试 {attempt+1}/{max_retries}): {type(e).__name__}: {e}")
            log(f"[ERROR] [Memory] Episode 异常 (尝试 {attempt+1}/{max_retries}): {type(e).__name__}: {e}")

        if attempt < max_retries - 1:
            time.sleep(2 ** attempt)
    return None


def consolidate_session(session_id: int):
    """
    下播后的深度整理：
    1. 取所有 Episodes
    2. 让 LLM 做最终总结
    3. 向量化存入长期库
    4. 清空本场原始数据
    """
    episodes = get_session_episodes(session_id)
    if not episodes:
        return

    conn = sqlite3.connect(MEMORY_DB_PATH)
    conn.row_factory = sqlite3.Row
    session = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
    conn.close()

    if not session:
        return

    session_date = dict(session).get("start_time", "")[:10]

    episode_text = "\n".join(
        f"- [{e['window_start']}~{e['window_end']}] {e['title']}: {e['summary']}"
        for e in episodes
    )

    prompt = (
        "你是一个直播记忆整理引擎。以下是一场直播的所有片段摘要。\n"
        "请生成这场直播的最终总结。\n"
        "返回 JSON，不要 markdown 包裹：\n"
        '{"title": "整场直播标题", "summary": "完整总结（5-8句话，涵盖关键事件、氛围、梗）", '
        '"keywords": ["关键词1","关键词2","关键词3"]}\n\n'
        f"直播日期: {session_date}\n"
        f"片段列表:\n{episode_text}"
    )

    max_retries = 3
    for attempt in range(max_retries):
        try:
            resp = httpx.post(
                f"{ANALYSIS_BASE_URL}/chat/completions",
                json={
                    "model": EPISODE_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 16000,
                },
                headers={"Authorization": f"Bearer {ANALYSIS_API_KEY}"},
                timeout=60.0,
            )
            if resp.status_code == 200:
                resp_json = resp.json()
                content = resp_json["choices"][0]["message"].get("content")
                if content is None:
                    print(f"[{ts()}] [Memory] 深度整理返回内容为空: {resp_json}")
                    continue
                content = content.strip()
                if content.startswith("```"):
                    content = content.split("```")[1]
                    if content.startswith("json"):
                        content = content[4:]
                start = content.find("{")
                end = content.rfind("}")
                if start >= 0 and end > start:
                    result = json.loads(content[start:end + 1])
                    save_long_term_memory(
                        session_date=session_date,
                        title=result.get("title", "未知直播"),
                        summary=result.get("summary", ""),
                        keywords=result.get("keywords", []),
                    )
                break
            else:
                resp_text = resp.text
                print(f"[{ts()}] [Memory] 深度整理请求失败 (尝试 {attempt+1}/{max_retries}): {resp.status_code} {resp_text[:200]}")
                log(f"[ERROR] [Memory] 深度整理请求失败 (尝试 {attempt+1}/{max_retries}): {resp.status_code} {resp_text}")
        except Exception as e:
            print(f"[{ts()}] [Memory] 深度整理异常 (尝试 {attempt+1}/{max_retries}): {type(e).__name__}: {e}")
            log(f"[ERROR] [Memory] 深度整理异常 (尝试 {attempt+1}/{max_retries}): {type(e).__name__}: {e}")

        if attempt < max_retries - 1:
            time.sleep(2 ** attempt)

    # 清空原始数据
    purge_session_raw_data(session_id)


# ========== 备忘录 (Memo) ==========

_CATEGORY_TTL = {"health": 30, "event": 7, "schedule": 14, "note": 14}

def save_memo(content: str, category: str = "note", sticky: bool = False, ttl_days: int = None):
    from datetime import timedelta
    from difflib import SequenceMatcher
    if ttl_days is None:
        ttl_days = _CATEGORY_TTL.get(category, 14)
    expires_at = (datetime.now() + timedelta(days=ttl_days)).isoformat() if not sticky else None

    conn = sqlite3.connect(MEMORY_DB_PATH)
    recent = conn.execute(
        "SELECT id, content FROM memos WHERE created_at > ? ORDER BY created_at DESC LIMIT 20",
        ((datetime.now() - timedelta(minutes=10)).isoformat(),)
    ).fetchall()
    
    for _id, existing in recent:
        if existing == content or SequenceMatcher(None, content, existing).ratio() > 0.6:
            conn.close()
            return

    conn.execute(
        "INSERT INTO memos (created_at, content, category, sticky, expires_at) VALUES (?,?,?,?,?)",
        (datetime.now().isoformat(), content, category, int(sticky), expires_at)
    )
    conn.commit()
    conn.close()
    print(f"[{ts()}] [Memo] 已保存: [{category}] {content}")


def delete_memo_by_keyword(keyword: str) -> int:
    conn = sqlite3.connect(MEMORY_DB_PATH)
    cur = conn.execute("DELETE FROM memos WHERE content LIKE ?", (f"%{keyword}%",))
    deleted = cur.rowcount
    conn.commit()
    conn.close()
    if deleted:
        print(f"[{ts()}] [Memo] 已删除 {deleted} 条包含'{keyword}'的备忘")
    return deleted


def get_active_memos(limit: int = 15) -> list[dict]:
    conn = sqlite3.connect(MEMORY_DB_PATH)
    conn.row_factory = sqlite3.Row
    now = datetime.now().isoformat()
    rows = conn.execute(
        """SELECT id, content, category, sticky FROM memos
           WHERE expires_at IS NULL OR expires_at > ?
           ORDER BY sticky DESC,
                    CASE category WHEN 'health' THEN 1 WHEN 'event' THEN 2
                                  WHEN 'schedule' THEN 3 ELSE 4 END,
                    created_at DESC
           LIMIT ?""",
        (now, limit)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def cleanup_expired_memos() -> int:
    conn = sqlite3.connect(MEMORY_DB_PATH)
    now = datetime.now().isoformat()
    cur = conn.execute("DELETE FROM memos WHERE expires_at IS NOT NULL AND expires_at <= ?", (now,))
    deleted = cur.rowcount
    conn.commit()
    conn.close()
    if deleted:
        print(f"[{ts()}] [Memo] 清理了 {deleted} 条过期备忘")
    return deleted


# ========== 访客追踪 (Visitor) ==========

def on_visitor_enter(uid: str, uname: str) -> str:
    """
    访客进入直播间。返回:
    - 'welcome': 首次来访，应该欢迎
    - 'returning': 说过话的老观众回来了
    - 'bot': 没说过话又来了，疑似人机
    """
    conn = sqlite3.connect(MEMORY_DB_PATH)
    row = conn.execute("SELECT has_spoken, visit_count FROM visitors WHERE uid=?", (uid,)).fetchone()

    if row is None:
        conn.execute(
            "INSERT INTO visitors (uid, uname, first_seen) VALUES (?,?,?)",
            (uid, uname, datetime.now().isoformat())
        )
    conn.commit()
    conn.close()


def is_blacklisted(uid: str) -> bool:
    """检查是否在永久黑名单中"""
    conn = sqlite3.connect(MEMORY_DB_PATH)
    row = conn.execute("SELECT uid FROM welcomed_blacklist WHERE uid=?", (uid,)).fetchone()
    conn.close()
    return row is not None


def add_to_blacklist(uid: str, uname: str):
    """加入永久黑名单（被欢迎过的无粉团路人）"""
    conn = sqlite3.connect(MEMORY_DB_PATH)
    conn.execute(
        "INSERT OR IGNORE INTO welcomed_blacklist (uid, uname, added_at) VALUES (?,?,?)",
        (uid, uname, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()


def on_visitor_speak(uid: str, uname: str):
    """访客发了弹幕，标记为真人"""
    conn = sqlite3.connect(MEMORY_DB_PATH)
    row = conn.execute("SELECT uid FROM visitors WHERE uid=?", (uid,)).fetchone()
    if row:
        conn.execute("UPDATE visitors SET has_spoken=1, uname=? WHERE uid=?", (uname, uid))
    else:
        conn.execute(
            "INSERT INTO visitors (uid, uname, first_seen, has_spoken) VALUES (?,?,?,1)",
            (uid, uname, datetime.now().isoformat())
        )
    conn.commit()
    conn.close()


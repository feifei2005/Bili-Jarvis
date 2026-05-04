"""
弹幕机器人主入口

架构：
  WebSocket 监听弹幕/礼物/开播下播
  + Sampler 每 30 秒截取片段，立即发起独立处理流程
  + ProcessClip 单片段串行：视觉描述 → LLM分析 → LLM回复
  + Memory Manager 10分钟 Episode + 下播深度整理
"""
import asyncio
import sys
import json
import struct
import time
import os
from datetime import datetime
from functools import reduce
from hashlib import md5
import urllib.parse
import platform

# 强制控制台输出 UTF-8，解决 Windows 环境 Emoji 报错
if platform.system() == "Windows" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

import aiohttp
import brotli

from bot_config import (
    ROOM_ID, SESSDATA, BILI_JCT, BUVID3,
    BILI_HEADERS, BILI_COOKIES,
    HEARTBEAT_INTERVAL, EPISODE_INTERVAL_SECONDS,
    VISION_CLIP_SECONDS, TIP_INTERVAL_SECONDS, DANMAKU_COOLDOWN,
    DEBUG_MODE, ts, BOT_DATA_DIR,
)
from memory_manager import (
    init_short_term_db, init_long_term_db,
    start_session, end_session,
    save_vision, save_danmaku, get_window_data,
    save_episode, generate_episode, consolidate_session,
    on_visitor_enter, on_visitor_speak, cleanup_expired_memos,
)
from vision_worker import get_stream_url, capture_clip, describe_video
from brain_worker import think_and_reply, send_danmaku
from tip_scheduler import TipScheduler
from log_util import init_log, log

# ========== 复用 live_monitor.py 的二进制协议 ==========
_MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52
]

PROTO_HEARTBEAT = 1
PROTO_BROTLI = 3
DP_HEARTBEAT = 2
DP_HEARTBEAT_RESP = 3
DP_NOTICE = 5
DP_VERIFY = 7
DP_VERIFY_RESP = 8


def _get_mixin_key(orig: str) -> str:
    return reduce(lambda s, i: s + orig[i], _MIXIN_KEY_ENC_TAB, '')[:32]

def _enc_wbi(params: dict, img_key: str, sub_key: str) -> dict:
    mixin_key = _get_mixin_key(img_key + sub_key)
    params['wts'] = round(time.time())
    params = dict(sorted(params.items()))
    params = {k: ''.join(filter(lambda c: c not in "!'()*", str(v))) for k, v in params.items()}
    query = urllib.parse.urlencode(params)
    params['w_rid'] = md5((query + mixin_key).encode()).hexdigest()
    return params

def _pack(data: bytes, proto: int, dp_type: int) -> bytes:
    buf = bytearray()
    buf += struct.pack(">H", 16)
    buf += struct.pack(">H", proto)
    buf += struct.pack(">I", dp_type)
    buf += struct.pack(">I", 1)
    buf += data
    return struct.pack(">I", len(buf) + 4) + bytes(buf)

def _unpack(data: bytes) -> list:
    ret = []
    header = struct.unpack(">IHHII", data[:16])
    if header[2] == PROTO_BROTLI:
        real_data = brotli.decompress(data[16:])
    else:
        real_data = data
    if header[2] == PROTO_HEARTBEAT and header[3] == DP_HEARTBEAT_RESP:
        real_data = real_data[16:]
        ret.append({"datapack_type": header[3], "data": {"view": struct.unpack('>I', real_data[0:4])[0]}})
        return ret
    offset = 0
    _pkt_stats = {}  # 包类型计数
    while offset < len(real_data):
        h = struct.unpack(">IHHII", real_data[offset:offset + 16])
        length = h[0]
        chunk = real_data[(offset + 16):(offset + length)]
        pkt = {"datapack_type": h[3], "data": None}
        try:
            if h[2] in (0, 2):
                pkt["data"] = json.loads(chunk.decode("utf-8", errors="ignore"))
            elif h[2] == 1:
                if h[3] == DP_HEARTBEAT_RESP:
                    pkt["data"] = {"view": struct.unpack(">I", chunk)[0]}
                elif h[3] == DP_VERIFY_RESP:
                    pkt["data"] = json.loads(chunk.decode("utf-8", errors="ignore"))
            ret.append(pkt)
        except Exception as e:
            _code = h[3]
            if _code not in _pkt_stats:
                _pkt_stats[_code] = {"total": 0, "errors": 0}
            _pkt_stats[_code]["total"] += 1
            _pkt_stats[_code]["errors"] += 1
            print(f"[{ts()}] [DEBUG] [Unpack] 解包错误 type={h[3]}, proto={h[2]}, len={length}: {e}")
            log(f"[ERROR] [Unpack] type={h[3]} proto={h[2]} len={length}: {type(e).__name__}: {e}")
        offset += length
    return ret


async def _api_get(session, url, params=None):
    async with session.get(url, params=params, headers=BILI_HEADERS, cookies=BILI_COOKIES, ssl=False) as resp:
        if resp.status != 200:
            raise Exception(f"HTTP Error {resp.status} for URL {url}")
        result = await resp.json()
        if result.get("code") != 0:
            raise Exception(f"API error {result.get('code')}: {result.get('message', '')}")
        return result.get("data")

async def _get_wbi_keys(session):
    result = await _api_get(session, "https://api.bilibili.com/x/web-interface/nav")
    img_url = result['wbi_img']['img_url']
    sub_url = result['wbi_img']['sub_url']
    return img_url.rsplit('/', 1)[1].split('.')[0], sub_url.rsplit('/', 1)[1].split('.')[0]

async def _get_danmu_info(session, room_id):
    img_key, sub_key = await _get_wbi_keys(session)
    params = _enc_wbi({"id": room_id}, img_key, sub_key)
    return await _api_get(session,
        "https://api.live.bilibili.com/xlive/web-room/v1/index/getDanmuInfo", params)

async def _get_room_play_info(session, room_id):
    return await _api_get(session,
        "https://api.live.bilibili.com/xlive/web-room/v1/index/getRoomPlayInfo",
        {"room_id": room_id})

async def _get_room_info(session, room_id):
    return await _api_get(session,
        "https://api.live.bilibili.com/xlive/web-room/v1/index/getInfoByRoom",
        {"room_id": room_id})


# ========== 后台任务 ==========

async def _heartbeat_loop(ws):
    hb = _pack(b'[object Object]', PROTO_HEARTBEAT, DP_HEARTBEAT)
    while True:
        await asyncio.sleep(HEARTBEAT_INTERVAL)
        try:
            await ws.send_bytes(hb)
        except Exception:
            break


async def _sampler_loop(session: aiohttp.ClientSession, state: dict):
    """独立的采样循环：每 30 秒截取一个片段，立即发起处理流程"""
    while state["running"]:
        start_time = time.time()
        if not state.get("is_live"):
            await asyncio.sleep(5)
            continue

        try:
            stream_url = await get_stream_url(session, ROOM_ID)
            if stream_url:
                clip_path = await capture_clip(stream_url)
                if clip_path:
                    print(f"[{ts()}] [Sampler] 视频片段已截取: {os.path.basename(clip_path)}")
                    # 绑定当前弹幕池并保存到文件
                    dm_pool = list(state["danmaku_pool"])
                    state["danmaku_pool"].clear()
                    
                    dm_path = clip_path.replace(".mp4", ".dm.json")
                    try:
                        with open(dm_path, "w", encoding="utf-8") as f:
                            json.dump({
                                "clip": os.path.basename(clip_path),
                                "danmaku": dm_pool
                            }, f, ensure_ascii=False, indent=2)
                    except Exception as e:
                        print(f"[{ts()}] [Sampler] 保存弹幕文件失败: {e}")
                        log(f"[ERROR] [Sampler] 保存弹幕文件失败: {type(e).__name__}: {e}")
                    
                    # 立即起一个独立处理流程，不等结果
                    asyncio.create_task(_process_clip(session, state, clip_path))
        except Exception as e:
            print(f"[{ts()}] [Sampler] 异常: {e}")
            log(f"[ERROR] [Sampler] 异常: {type(e).__name__}: {e}")

        elapsed = time.time() - start_time
        await asyncio.sleep(max(1, VISION_CLIP_SECONDS - elapsed))


async def _process_clip(session: aiohttp.ClientSession, state: dict, clip_path: str):
    """处理单个 30 秒视频片段：视觉描述 → LLM分析 → LLM回复"""
    try:
        desc = await describe_video([clip_path])
        if desc:
            state["current_vision"] = desc
            if state.get("session_id"):
                save_vision(state["session_id"], desc)
            print(f"[{ts()}] [Vision] {desc[:80]}...")
        
        # 清理片段文件
        try:
            os.remove(clip_path)
        except Exception:
            pass
        
        # 分析 + 回复
        if desc and state.get("session_id"):
            analysis, reply = await think_and_reply(
                session, state["session_id"], ROOM_ID,
                current_vision=desc,
                room_title=state.get("room_title"),
            )
            if analysis:
                scheduler: TipScheduler = state["scheduler"]
                scheduler.set_singing(analysis.get("is_singing", False))
                if analysis.get("first_win"):
                    scheduler.on_first_win()
    except Exception as e:
        print(f"[{ts()}] [ProcessClip] 异常: {e}")
        log(f"[ERROR] [ProcessClip] 异常: {type(e).__name__}: {e}")
        try:
            os.remove(clip_path)
        except Exception:
            pass


async def _episode_loop(state: dict):
    """每 10 分钟整理一次 Episode"""
    while state["running"]:
        await asyncio.sleep(EPISODE_INTERVAL_SECONDS)

        if not state.get("is_live") or not state.get("session_id"):
            continue

        window_end = datetime.now().isoformat()
        window_start = state.get("last_episode_time", state.get("live_start_time", window_end))
        state["last_episode_time"] = window_end

        try:
            window_data = get_window_data(state["session_id"], window_start)
            episode = generate_episode(window_data)
            if episode:
                save_episode(
                    state["session_id"], window_start, window_end,
                    episode.get("title", "未知"),
                    episode.get("summary", ""),
                    episode.get("keywords", []),
                    episode.get("participants", []),
                )
                print(f"[{ts()}] [Memory] Episode 已保存: {episode.get('title', '')}")
        except Exception as e:
            print(f"[{ts()}] [Memory] Episode 生成失败: {e}")
            log(f"[ERROR] [Memory] Episode 生成失败: {type(e).__name__}: {e}")


async def _maintenance_loop(state: dict):
    """维护循环：非直播时间读取弹幕文件并进行长期记忆沉淀"""
    while state["running"]:
        await asyncio.sleep(600) # 每 10 分钟检查一次

        if state.get("is_live"):
            continue
        
        # 这里预留给长期记忆处理逻辑
        # 例如：扫描 bot_data/*.dm.json，汇总成日报或注入长效知识库
        pass


# ========== 消息处理 ==========

async def _handle_message(data: bytes, session: aiohttp.ClientSession, state: dict):
    try:
        packets = _unpack(data)
    except Exception as e:
        print(f"[{ts()}] [Bot] 解包错误: {e}")
        log(f"[ERROR] [Bot] 解包错误: {type(e).__name__}: {e}")
        return

    for pkt in packets:
        dp_type = pkt["datapack_type"]
        d = pkt["data"]

        if dp_type == DP_VERIFY_RESP:
            if d.get("code") == 0:
                print(f"[{ts()}] [Bot] 认证成功")
            else:
                print(f"[{ts()}] [Bot] 认证失败: {d}")

        elif dp_type == DP_NOTICE:
            cmd = d.get("cmd", "")

            # 诊断：记录未处理的 B站通知类型
            if DEBUG_MODE and cmd and not any(k in cmd for k in (
                "DANMU_MSG", "INTERACT_WORD", "LIVE", "PREPARING",
                "ONLINE_RANK", "WATCHED_CHANGE", "ROOM_REAL_TIME",
                "SEND_GIFT", "PK_BATTLE_START_NEW", "PK_BATTLE_SETTLE_NEW",
                "PK_BATTLE_PROCESS_NEW", "PK_BATTLE_PRE_NEW", "PK_BATTLE_PUNISH_END",
                "PK_INFO", "PK_WIDGET",
                "LIKE_INFO_V3_UPDATE", "LIKE_INFO_V3_CLICK",
                "ENTRY_EFFECT", "DM_INTERACTION", "NOTICE_MSG",
                "COMBO_SEND", "COMMON_NOTICE_DANMAKU",
                "UNIVERSAL_EVENT_GIFT_V2", "UNIVERSAL_EVENT_GIFT",
                "WIDGET_GIFT_STAR_PROCESS_V2", "WIDGET_GIFT_STAR_PROCESS",
                "WIDGET_BANNER",
                "MESSAGEBOX_USER_MEDAL_CHANGE",
            )):
                print(f"[{ts()}] [DEBUG] [Notice] 未处理: {cmd[:80]}")

            if "DANMU_MSG" in cmd:
                info = d.get("info", [])
                if len(info) >= 3:
                    content = info[1]
                    uid = str(info[2][0]) if info[2] else "0"
                    uname = info[2][1] if len(info[2]) > 1 else "?"
                    if DEBUG_MODE:
                        print(f"[{ts()}] [DEBUG] [Danmaku] {uname}: {content}")
                    
                    # 记录到池中，等待采样时绑定
                    state["danmaku_pool"].append({
                        "ts": time.time(),
                        "u": uname,
                        "m": content
                    })
                    
                    save_danmaku(state["session_id"], uname, content)
                    on_visitor_speak(uid, uname)

            elif "SEND_GIFT" in cmd:
                gd = d.get("data", {})
                if gd.get("coin_type") == "gold":
                    rmb = gd.get("total_coin", 0) / 1000
                    gift_name = gd.get("giftName", "礼物")
                    uname = gd.get("uname", "")
                    num = gd.get("num", 1)
                    state["danmaku_pool"].append({
                        "ts": time.time(),
                        "u": uname,
                        "m": f"[礼物] 送了{num}个{gift_name} (¥{rmb:.2f})"
                    })
                    if DEBUG_MODE:
                        print(f"[{ts()}] [Gift] {uname}: {num}x{gift_name} ¥{rmb:.2f}")

            elif "PK_BATTLE_START_NEW" in cmd:
                pk_data = d.get("data", {})
                opponent = pk_data.get("uname", "")
                scheduler: TipScheduler = state["scheduler"]
                scheduler.on_pk_start()
                print(f"[{ts()}] [Bot] PK 开始，对手: {opponent}")

            elif "PK_BATTLE_SETTLE_NEW" in cmd:
                scheduler: TipScheduler = state["scheduler"]
                scheduler.on_pk_end()

            elif "INTERACT_WORD" in cmd:
                try:
                    idata = d.get("data", {})
                    uid = str(idata.get("uid", ""))
                    uname = idata.get("uname", "")
                    medal = idata.get("fans_medal", {})
                    has_medal = medal.get("medal_level", 0) > 0 if medal else False

                    scheduler: TipScheduler = state["scheduler"]
                    welcome_msg = scheduler.on_new_fan(uid, uname, has_medal)
                    if welcome_msg:
                        if state.get("session_id"):
                            async def _delayed_welcome(msg):
                                try:
                                    await asyncio.sleep(15)
                                    await send_danmaku(session, ROOM_ID, msg)
                                except Exception as e:
                                    print(f"[{ts()}] [Welcome] 发送欢迎语失败: {e}")
                                    log(f"[ERROR] [Welcome] 发送欢迎语失败: {type(e).__name__}: {e}")
                            asyncio.create_task(_delayed_welcome(welcome_msg))
                        else:
                            print(f"[{ts()}] [Welcome] session_id 为空，丢弃欢迎语: {uname}")
                except Exception as e:
                    raw_uname = d.get("data", {}).get("uname", "?")
                    print(f"[{ts()}] [Welcome] 欢迎逻辑异常 (uname={raw_uname}): {e}")
                    log(f"[ERROR] [Welcome] 欢迎逻辑异常 (uname={raw_uname}): {type(e).__name__}: {e}")

            elif cmd == "LIVE":
                state["is_live"] = True
                state["live_start_time"] = datetime.now().isoformat()
                state["last_episode_time"] = state["live_start_time"]
                state["session_id"] = start_session()
                print(f"[{ts()}] [Bot] 🟢 开播 (session={state['session_id']})")

            elif cmd == "PREPARING":
                if state.get("is_live"):
                    print(f"[{ts()}] [Bot] 🔴 下播，开始深度整理...")
                    end_session(state["session_id"])

                    # 先做最后一次 episode
                    window_start = state.get("last_episode_time", state.get("live_start_time", ""))
                    window_end = datetime.now().isoformat()
                    try:
                        window_data = get_window_data(state["session_id"], window_start)
                        episode = generate_episode(window_data)
                        if episode:
                            save_episode(
                                state["session_id"], window_start, window_end,
                                episode.get("title", ""), episode.get("summary", ""),
                                episode.get("keywords", []), episode.get("participants", []),
                            )
                    except Exception:
                        pass

                    # 深度整理 + 清理原始数据
                    try:
                        consolidate_session(state["session_id"])
                        print(f"[{ts()}] [Bot] 深度整理完成，原始数据已清理")
                    except Exception as e:
                        print(f"[{ts()}] [Bot] 深度整理失败: {e}")

                    state["is_live"] = False
                    state["current_vision"] = None


# ========== 贴士循环 ==========

async def _tip_loop(session: aiohttp.ClientSession, state: dict):
    """规则引擎 + LLM贴士的定时触发"""
    scheduler: TipScheduler = state["scheduler"]
    last_tip_time = time.time()

    while state["running"]:
        await asyncio.sleep(1)

        if not state.get("is_live") or not state.get("session_id"):
            continue

        # 规则引擎的定时贴士（不走LLM）
        tips = scheduler.check_and_get_tips()
        for tip in tips:
            await send_danmaku(session, ROOM_ID, tip)
            print(f"[Tip] {tip}")

        # LLM贴士（每 TIP_INTERVAL_SECONDS 触发一次）
        if time.time() - last_tip_time >= TIP_INTERVAL_SECONDS:
            last_tip_time = time.time()
            try:
                _, reply = await think_and_reply(
                    session, state["session_id"], ROOM_ID,
                    current_vision=state.get("current_vision"),
                    room_title=state.get("room_title"),
                    is_tip_mode=True,
                )
                if reply:
                    print(f"[{ts()}] [Tip/LLM] {reply}")
            except Exception as e:
                print(f"[{ts()}] [Tip/LLM] 异常: {e}")
                log(f"[ERROR] [Tip/LLM] 异常: {type(e).__name__}: {e}")


# ========== 主循环 ==========

async def main():
    init_short_term_db()
    init_long_term_db()
    cleanup_expired_memos()
    init_log(BOT_DATA_DIR)

    scheduler = TipScheduler()

    state = {
        "running": True,
        "is_live": False,
        "session_id": None,
        "current_vision": None,
        "live_start_time": None,
        "last_episode_time": None,
        "room_title": None,
        "scheduler": scheduler,
        "danmaku_pool": [],      # 存放当前采样周期的弹幕
    }

    print(f"[{ts()}] [Bot] 启动，监控房间 {ROOM_ID}")

    async with aiohttp.ClientSession(cookies=BILI_COOKIES) as session:
        # 启动后台任务
        sampler_task = asyncio.create_task(_sampler_loop(session, state))
        episode_task = asyncio.create_task(_episode_loop(state))
        tip_task = asyncio.create_task(_tip_loop(session, state))
        maint_task = asyncio.create_task(_maintenance_loop(state))

        while state["running"]:
            try:
                info = await _get_room_play_info(session, ROOM_ID)
                real_id = info["room_id"]

                try:
                    room_info = await _get_room_info(session, ROOM_ID)
                    state["room_title"] = room_info.get("title", "")
                except Exception as e:
                    print(f"[{ts()}] [Bot] 获取标题失败: {e}")
                    log(f"[ERROR] [Bot] 获取标题失败: {type(e).__name__}: {e}")

                # 每次重连都检查直播状态
                if info.get("live_status") == 1:
                    if not state["is_live"]:
                        state["is_live"] = True
                        state["live_start_time"] = datetime.now().isoformat()
                        state["last_episode_time"] = state["live_start_time"]
                        state["session_id"] = start_session()
                        scheduler.first_win_done = False
                        print(f"[{ts()}] [Bot] 检测到正在直播 (session={state['session_id']})")
                else:
                    # 没在播，等30秒再查
                    if state["is_live"]:
                        print(f"[{ts()}] [Bot] 检测到未开播，切换为待机")
                        state["is_live"] = False
                        state["current_vision"] = None
                    await asyncio.sleep(30)
                    continue

                my_uid = 0
                try:
                    my_info = await _api_get(session, "https://api.bilibili.com/x/web-interface/nav")
                    my_uid = my_info.get("mid", 0)
                except Exception:
                    pass

                danmu = await _get_danmu_info(session, ROOM_ID)
                token = danmu["token"]

                for host_info in danmu["host_list"]:
                    host = host_info["host"]
                    port = host_info["wss_port"]
                    uri = f"wss://{host}:{port}/sub"

                    try:
                        async with session.ws_connect(uri, headers=BILI_HEADERS, ssl=False, timeout=10) as ws:
                            verify = json.dumps({
                                "uid": my_uid,
                                "roomid": real_id,
                                "protover": 3,
                                "buvid": BUVID3,
                                "platform": "web",
                                "type": 2,
                                "key": token,
                            }).encode("utf-8")
                            await ws.send_bytes(_pack(verify, 1, DP_VERIFY))

                            hb_task = asyncio.create_task(_heartbeat_loop(ws))
                            try:
                                async for msg in ws:
                                    if msg.type == aiohttp.WSMsgType.BINARY:
                                        await _handle_message(msg.data, session, state)
                                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                        break
                            finally:
                                hb_task.cancel()

                        break
                    except Exception as e:
                        print(f"[{ts()}] [Bot] Host {host} 连接失败: {e}")
                        log(f"[ERROR] [Bot] Host {host} 连接失败: {type(e).__name__}: {e}")
                        continue

            except Exception as e:
                print(f"[{ts()}] [Bot] 连接异常: {e}")
                log(f"[ERROR] [Bot] 连接异常: {type(e).__name__}: {e}")
            except asyncio.CancelledError:
                print(f"[{ts()}] [Bot] 收到退出信号")
                state["running"] = False
                break

            if not state["running"]:
                break

            print(f"[{ts()}] [Bot] 5秒后重连...")
            await asyncio.sleep(5)

        sampler_task.cancel()
        episode_task.cancel()
        tip_task.cancel()
        maint_task.cancel()
        await asyncio.gather(sampler_task, episode_task, tip_task, maint_task, return_exceptions=True)
        print(f"[{ts()}] [Bot] 已退出")


if __name__ == "__main__":
    asyncio.run(main())

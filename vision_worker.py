"""
视觉中枢

职责：
  1. 获取直播流 URL（V2 API + Wbi 签名，参考 BililiveRecorder）
  2. 用 ffmpeg 每 30 秒截取一段视频
  3. 将视频 base64 发给多模态模型，返回画面描述
"""
import asyncio
import base64
import json
import os
import tempfile
import time
from datetime import datetime
from functools import reduce
from hashlib import md5
from typing import Optional
import urllib.parse

import aiohttp
import httpx

from bot_config import (
    ROOM_ID, SESSDATA, BILI_JCT, BUVID3,
    BILI_HEADERS, BILI_COOKIES,
    PLAY_URL_API, FFMPEG_PATH,
    VISION_BASE_URL, VISION_API_KEY, VISION_MODEL,
    VISION_CLIP_SECONDS, BOT_DATA_DIR, ts,
)
from log_util import log

# ========== Wbi 签名（复用 live_monitor.py）==========
_MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52
]

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


async def _get_wbi_keys(session: aiohttp.ClientSession) -> tuple[str, str]:
    async with session.get(
        "https://api.bilibili.com/x/web-interface/nav",
        headers=BILI_HEADERS, cookies=BILI_COOKIES, ssl=False
    ) as resp:
        data = await resp.json()
        img_url = data['data']['wbi_img']['img_url']
        sub_url = data['data']['wbi_img']['sub_url']
        return (
            img_url.rsplit('/', 1)[1].split('.')[0],
            sub_url.rsplit('/', 1)[1].split('.')[0],
        )


async def get_stream_url(session: aiohttp.ClientSession, room_id: int) -> Optional[str]:
    """
    获取直播流地址。参考 BililiveRecorder 的 V2 参数组合。
    返回第一个可用的 HLS (.m3u8) 或 FLV 直链，未开播返回 None。
    """
    img_key, sub_key = await _get_wbi_keys(session)

    params = _enc_wbi({
        "room_id": str(room_id),
        "no_playurl": "0",
        "mask": "1",
        "qn": "10000",
        "platform": "web",
        "protocol": "0,1",
        "format": "0,1,2",
        "codec": "0,1",
        "dolby": "5",
        "panorama": "1",
        "hdr_type": "0",
    }, img_key, sub_key)

    async with session.get(
        PLAY_URL_API, params=params,
        headers=BILI_HEADERS, cookies=BILI_COOKIES, ssl=False
    ) as resp:
        data = await resp.json()

    play_data = data.get("data", {})
    if play_data.get("live_status") != 1:
        return None

    playurl_info = play_data.get("playurl_info", {})
    playurl = playurl_info.get("playurl", {})
    streams = playurl.get("stream", [])

    # 优先 HLS (protocol_name=http_hls)，其次 http_stream
    for stream in streams:
        for fmt in stream.get("format", []):
            for codec in fmt.get("codec", []):
                url_info = codec.get("url_info", [])
                base_url = codec.get("base_url", "")
                if url_info and base_url:
                    host_info = url_info[0]
                    full_url = host_info["host"] + base_url + host_info["extra"]
                    return full_url

    return None

async def capture_clip(stream_url: str) -> Optional[str]:
    """
    用 ffmpeg 从直播流截取视频。
    返回临时文件路径，失败返回 None。
    """
    os.makedirs(BOT_DATA_DIR, exist_ok=True)
    output_path = os.path.join(BOT_DATA_DIR, f"clip_{int(time.time())}.mp4")

    cmd = [
        FFMPEG_PATH,
        "-y",
        "-headers", "Referer: https://live.bilibili.com/\r\nUser-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36 Edg/132.0.0.0\r\n",
        "-i", stream_url,
        "-t", str(VISION_CLIP_SECONDS),
        "-c", "copy",
        "-movflags", "+faststart",
        output_path,
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        await asyncio.wait_for(proc.communicate(), timeout=VISION_CLIP_SECONDS + 10)
    except asyncio.TimeoutError:
        proc.terminate()
        await proc.wait()

    if not os.path.exists(output_path) or os.path.getsize(output_path) < 1024:
        if os.path.exists(output_path):
            os.remove(output_path)
        return None

    return output_path


async def describe_video(video_paths: list[str]) -> Optional[str]:
    """
    将最多3个视频文件发给多模态模型，返回画面描述文本。
    包含指数退避重试机制 (最大重试3次)。
    """
    if not video_paths:
        return None

    content_parts = [
        {
            "type": "text",
            "text": (
                "你是一个直播画面观察员。请简洁描述这段直播视频中发生了什么。"
                "重点关注：主播在做什么、游戏/活动内容、画面中的文字信息、"
                "主播的情绪反应、任何值得注意的事件。"
                "如果有多个视频片段，它们是连续的，请综合描述。"
                "用2-3句话描述即可。"
                "注意：不要猜测或提及主播的名字，一律用「主播」指代。"
            ),
        }
    ]

    for path in video_paths:
        try:
            with open(path, "rb") as f:
                video_b64 = base64.b64encode(f.read()).decode("utf-8")
                content_parts.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:video/mp4;base64,{video_b64}"
                    },
                })
        except Exception as e:
            print(f"[{ts()}] [Vision] 读取视频失败 {path}: {e}")

    payload = {
        "model": VISION_MODEL,
        "messages": [
            {
                "role": "user",
                "content": content_parts,
            }
        ],
        "max_tokens": 300,
    }

    max_retries = 3
    for attempt in range(max_retries):
        try:
            if attempt == 0:
                print(f"[{ts()}] [Vision] 请求 LLM: {VISION_MODEL}")
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(
                    f"{VISION_BASE_URL}/chat/completions",
                    json=payload,
                    headers={"Authorization": f"Bearer {VISION_API_KEY}"},
                )

            if resp.status_code == 200:
                return resp.json()["choices"][0]["message"]["content"]
            else:
                resp_text = resp.text
                print(f"[{ts()}] [Vision] AI 请求失败 (尝试 {attempt+1}/{max_retries}): {resp.status_code} {resp_text[:200]}")
                log(f"[ERROR] [Vision] AI 请求失败 (尝试 {attempt+1}/{max_retries}): {resp.status_code} {resp_text}")
        except Exception as e:
            print(f"[{ts()}] [Vision] 网络请求异常 (尝试 {attempt+1}/{max_retries}): {type(e).__name__}: {e}")
            log(f"[ERROR] [Vision] 网络请求异常 (尝试 {attempt+1}/{max_retries}): {type(e).__name__}: {e}")

        if attempt < max_retries - 1:
            wait_time = 2 ** attempt
            print(f"[{ts()}] [Vision] 等待 {wait_time} 秒后重试...")
            await asyncio.sleep(wait_time)

    return None


# 移除 vision_cycle，将其逻辑放入 bot.py 中实现队列缓存

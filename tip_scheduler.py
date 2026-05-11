"""
规则引擎 - 定时/事件驱动的贴士系统

不调用 LLM，纯 Python 逻辑。
负责：喝水提醒、首胜提醒、欢迎新粉、PK投票提醒、活动提醒（从memo读取）。
"""
import time
from datetime import datetime
from typing import Optional

from bot_config import DRINK_WATER_INTERVAL, ts
from memory_manager import get_active_memos, on_visitor_enter
from prompts import MSG_DRINK_WATER, MSG_FIRST_WIN, MSG_PK_VOTE, MSG_MORNING, MSG_NIGHT, MSG_RANKING, MSG_FLOWER, get_welcome_msg

FLOWER_INTERVAL_SECONDS = 600


class TipScheduler:
    def __init__(self):
        now = time.time()
        # 初始化时标记所有规则为"刚发过"，避免启动后立刻触发
        self.last_sent: dict[str, float] = {
            "drink_water": now,
            "first_win": now,
            "event_memo": now,
            "pk_vote": now,
            "morning": now,
            "night": now,
            "ranking": now,
            "raffle_remind": now,
            "flower": now,
        }
        self.deferred: list[str] = []  # 忙碌时暂存
        self.first_win_done: bool = False
        self.is_pk: bool = False
        self.is_singing: bool = False
        self.is_raffle: bool = False
        self.raffle_start_time: float = 0.0
        self._singing_count: int = 0
        self._not_singing_count: int = 0

    def _since(self, rule_id: str) -> float:
        return time.time() - self.last_sent.get(rule_id, 0)

    def _mark(self, rule_id: str):
        self.last_sent[rule_id] = time.time()

    def _is_busy(self) -> bool:
        return self.is_pk or self.is_singing

    def _try_send(self, rule_id: str, msg: str, is_periodic: bool = False) -> Optional[str]:
        if self._is_busy():
            # 周期性规则（如喝水）在忙碌时直接跳过，不要塞进 deferred 导致解除忙碌后爆发
            # 同时也要标记已发送，避免结束忙碌后立刻补发
            if is_periodic:
                self._mark(rule_id)
                return None
            
            # 非周期性规则（如欢迎语）可以暂存，但限制队列长度
            if msg not in self.deferred:
                self.deferred.append(msg)
                if len(self.deferred) > 10:
                    self.deferred.pop(0)
            return None
        
        self._mark(rule_id)
        return msg

    # ========== 事件接口 ==========

    def on_pk_start(self):
        self.is_pk = True
        print("[Tip] PK 开始")

    def on_pk_end(self):
        self.is_pk = False
        print("[Tip] PK 结束")

    def on_first_win(self):
        self.first_win_done = True
        print("[Tip] 首胜已完成")

    def set_singing(self, singing: bool):
        """滞后判断：连续 3 次同方向才切换状态，避免视觉分析横跳"""
        if singing:
            self._singing_count += 1
            self._not_singing_count = 0
            if self._singing_count >= 3 and not self.is_singing:
                self.is_singing = True
                print("[Tip] 唱歌开始")
        else:
            self._not_singing_count += 1
            self._singing_count = 0
            if self._not_singing_count >= 3 and self.is_singing:
                self.is_singing = False
                print("[Tip] 唱歌结束")

    def on_new_fan(self, uid: str, uname: str, has_medal: bool) -> Optional[str]:
        """新粉丝进入，返回欢迎语或 None"""
        if not uname or not uname.strip():
            return None

        # 有牌子 → 老粉回归，欢迎
        if has_medal:
            on_visitor_enter(uid, uname)
            return get_welcome_msg(uname, has_medal)

        result = on_visitor_enter(uid, uname)
        # 没牌子 + 第一次来 → 新粉欢迎
        if result == "welcome":
            return get_welcome_msg(uname, has_medal)
        # 没牌子 + 回来过 → 不欢迎
        return None

    # ========== 定时检查 ==========

    def check_and_get_tips(self) -> list[str]:
        """主循环每秒调用一次，返回要发的消息列表"""
        tips = []
        hour = datetime.now().hour
        minute = datetime.now().minute

        # 喝水 - 每30分钟
        if self._since("drink_water") >= DRINK_WATER_INTERVAL:
            msg = self._try_send("drink_water", MSG_DRINK_WATER, is_periodic=True)
            if msg:
                tips.append(msg)

        # 送花 - 每10分钟
        if self._since("flower") >= FLOWER_INTERVAL_SECONDS:
            msg = self._try_send("flower", MSG_FLOWER, is_periodic=True)
            if msg:
                tips.append(msg)

        # 首胜提醒 - 晚播 22:00 前后 / 早播 10:00 前后，整场只提醒一次
        if not self.first_win_done and self._since("first_win") >= 1800:
            should_remind = (hour == 22 and minute < 5) or (hour == 10 and minute < 5)
            if should_remind:
                msg = self._try_send("first_win", MSG_FIRST_WIN, is_periodic=True)
                if msg:
                    tips.append(msg)

        # PK期间 - 投票提醒，每5分钟
        if self.is_pk and self._since("pk_vote") >= 300:
            msg = self._try_send("pk_vote", MSG_PK_VOTE, is_periodic=True)
            if msg:
                tips.append(msg)

        # 早安 - 6-10点，每4小时检查一次
        if 6 <= hour < 10 and self._since("morning") >= 3600 * 4:
            msg = self._try_send("morning", MSG_MORNING, is_periodic=True)
            if msg:
                tips.append(msg)

        # 晚安 - 23点至凌晨2点，整场一次
        if (hour >= 23 or hour < 2) and self._since("night") >= 3600 * 6:
            msg = self._try_send("night", MSG_NIGHT, is_periodic=True)
            if msg:
                tips.append(msg)

        # 人气榜 - 每晚 20:00-20:05，每天一次
        if hour == 20 and minute < 5 and self._since("ranking") >= 3600 * 22:
            msg = self._try_send("ranking", MSG_RANKING, is_periodic=True)
            if msg:
                tips.append(msg)

        # 天选超时 - 5分钟未收到结束消息则自动复位
        if self.is_raffle and self.raffle_start_time > 0:
            if time.time() - self.raffle_start_time >= 300:
                self.is_raffle = False
                self.raffle_start_time = 0.0
                print(f"[{ts()}] [Tip] 天选超时自动结束")
        if self.is_raffle and self._since("raffle_remind") >= 180:
            msg = self._try_send("raffle_remind", "天选进行中，大家快去点一点抢天选呀，万一中了呢～", is_periodic=True)
            if msg:
                tips.append(msg)

        # 活动提醒 - 从memo读取event类，每15分钟
            memos = get_active_memos(limit=15)
            event_memos = [m for m in memos if m["category"] == "event"]
            if event_memos:
                msg = self._try_send("event_memo", event_memos[0]["content"], is_periodic=True)
                if msg:
                    tips.append(msg)

        # 补发：忙碌状态刚结束（去重）
        if not self._is_busy() and self.deferred:
            seen = set()
            for d in self.deferred:
                if d not in seen:
                    tips.append(d)
                    seen.add(d)
            self.deferred.clear()

        return tips

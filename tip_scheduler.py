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


class TipScheduler:
    def __init__(self):
        now = time.time()
        # 初始化时标记所有规则为"刚发过"，避免启动后立刻触发
        self.last_sent: dict[str, float] = {
            "drink_water": now,
            "first_win": now,
            "event_memo": now,
            "pk_vote": now,
        }
        self.deferred: list[str] = []  # 忙碌时暂存
        self.first_win_done: bool = False
        self.is_pk: bool = False
        self.is_singing: bool = False
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
        if has_medal:
            return None

        if not uname or not uname.strip():
            return None

        result = on_visitor_enter(uid, uname)
        # [3flash-fix] 修改欢迎逻辑：同时欢迎纯新人(welcome)和发过言的老观众(returning)，仅无视沉默进出的疑似人机(bot)
        if result in ("welcome", "returning"):
            return f'欢迎「{uname}」光临直播间，给主播点点关注不迷路～'
        return None

    # ========== 定时检查 ==========

    def check_and_get_tips(self) -> list[str]:
        """主循环每秒调用一次，返回要发的消息列表"""
        tips = []
        hour = datetime.now().hour
        minute = datetime.now().minute

        # 喝水 - 每30分钟
        if self._since("drink_water") >= DRINK_WATER_INTERVAL:
            msg = self._try_send("drink_water", "【小贴士】主播，直播的时候也要记得多喝水休息一下呢～", is_periodic=True)
            if msg:
                tips.append(msg)

        # 首胜提醒 - 晚播 22:00 前后 / 早播 10:00 前后，整场只提醒一次
        if not self.first_win_done and self._since("first_win") >= 1800:
            should_remind = (hour == 22 and minute < 5) or (hour == 10 and minute < 5)
            if should_remind:
                msg = self._try_send("first_win", "【小贴士】主播，今天的首胜挑战可以冲一下哦～", is_periodic=True)
                if msg:
                    tips.append(msg)

        # PK期间 - 投票提醒，每5分钟
        if self.is_pk and self._since("pk_vote") >= 300:
            msg = self._try_send("pk_vote", "PK时间到，大家丢丢小垃圾支持一下阿只～", is_periodic=True)
            if msg:
                tips.append(msg)

        # 活动提醒 - 从memo读取event类，每15分钟
        if self._since("event_memo") >= 900:
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

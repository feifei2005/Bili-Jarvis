"""
规则引擎 - 定时/事件驱动的贴士系统

不调用 LLM，纯 Python 逻辑。
负责：定时贴士、首胜提醒、PK投票提醒、活动提醒（从memo读取）。
规则条目从 config.json 的 templates.rules 列表读取，可在 UI 中自由增删。
"""
import time
from datetime import datetime
from typing import Optional

from bot_config import ts
from memory_manager import get_active_memos, on_visitor_enter
from prompts import RULES, get_welcome_msg


class TipScheduler:
    def __init__(self):
        now = time.time()
        self.last_sent: dict[str, float] = {
            "event_memo": now,
            "raffle_remind": now,
        }
        for rule in RULES:
            self.last_sent[rule["id"]] = now
        self.deferred: list[str] = []
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
            if is_periodic:
                self._mark(rule_id)
                return None
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

    def on_pk_end(self):
        self.is_pk = False

    def on_first_win(self):
        self.first_win_done = True

    def set_singing(self, singing: bool):
        if singing:
            self._singing_count += 1
            self._not_singing_count = 0
            if self._singing_count >= 3 and not self.is_singing:
                self.is_singing = True
        else:
            self._not_singing_count += 1
            self._singing_count = 0
            if self._not_singing_count >= 3 and self.is_singing:
                self.is_singing = False

    def on_new_fan(self, uid: str, uname: str, has_medal: bool) -> Optional[str]:
        if not uname or not uname.strip():
            return None
        if has_medal:
            on_visitor_enter(uid, uname)
            return get_welcome_msg(uname, has_medal)
        result = on_visitor_enter(uid, uname)
        if result == "welcome":
            return get_welcome_msg(uname, has_medal)
        return None

    # ========== 定时检查 ==========

    def check_and_get_tips(self) -> list[str]:
        tips = []
        hour = datetime.now().hour
        minute = datetime.now().minute

        for rule in RULES:
            rid = rule["id"]
            trigger = rule.get("trigger", "interval")
            interval = rule.get("interval", 600)
            msg = rule.get("msg", "")

            if trigger == "interval":
                if self._since(rid) >= interval:
                    r = self._try_send(rid, msg, is_periodic=True)
                    if r: tips.append(r)

            elif trigger == "pk":
                if self.is_pk and self._since(rid) >= interval:
                    r = self._try_send(rid, msg, is_periodic=True)
                    if r: tips.append(r)

            elif trigger == "first_win":
                if not self.first_win_done and self._since(rid) >= 1800:
                    ok = (hour == 22 and minute < 5) or (hour == 10 and minute < 5)
                    if ok:
                        r = self._try_send(rid, msg, is_periodic=True)
                        if r: tips.append(r)

            elif trigger == "morning":
                if 6 <= hour < 10 and self._since(rid) >= 3600 * 4:
                    r = self._try_send(rid, msg, is_periodic=True)
                    if r: tips.append(r)

            elif trigger == "night":
                if (hour >= 23 or hour < 2) and self._since(rid) >= 3600 * 6:
                    r = self._try_send(rid, msg, is_periodic=True)
                    if r: tips.append(r)

            elif trigger == "ranking":
                if hour == 20 and minute < 5 and self._since(rid) >= 3600 * 22:
                    r = self._try_send(rid, msg, is_periodic=True)
                    if r: tips.append(r)

        # 天选超时
        if self.is_raffle and self.raffle_start_time > 0:
            if time.time() - self.raffle_start_time >= 300:
                self.is_raffle = False
                self.raffle_start_time = 0.0
        if self.is_raffle and self._since("raffle_remind") >= 180:
            r = self._try_send("raffle_remind", "天选进行中，大家快去点一点抢天选呀，万一中了呢～", is_periodic=True)
            if r: tips.append(r)

        # 活动提醒 - 从memo读取event类，每15分钟
        if self._since("event_memo") >= 900:
            memos = get_active_memos(limit=15)
            event_memos = [m for m in memos if m["category"] == "event"]
            if event_memos:
                r = self._try_send("event_memo", event_memos[0]["content"], is_periodic=True)
                if r: tips.append(r)

        # 补发：忙碌状态刚结束（去重）
        if not self._is_busy() and self.deferred:
            seen = set()
            for d in self.deferred:
                if d not in seen:
                    tips.append(d)
                    seen.add(d)
            self.deferred.clear()

        return tips

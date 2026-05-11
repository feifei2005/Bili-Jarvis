"""
Bot 与 GUI 之间的共享状态
"""
import threading
from datetime import datetime


class BotState:
    def __init__(self):
        self._lock = threading.Lock()
        self.is_live = False
        self.session_id = None
        self.room_title = ""
        self.current_vision = ""
        self.room_id = 0
        self.latest_sends = []
        self.start_time = datetime.now()
        self._log_entries = []
        self.restart_requested = False

    def update(self, **kwargs):
        with self._lock:
            for k, v in kwargs.items():
                if hasattr(self, k):
                    setattr(self, k, v)

    def add_send(self, msg: str):
        with self._lock:
            ts = datetime.now().strftime("%H:%M:%S")
            self.latest_sends.append((ts, msg))
            if len(self.latest_sends) > 20:
                self.latest_sends = self.latest_sends[-20:]

    def add_log(self, msg: str):
        with self._lock:
            ts = datetime.now().strftime("%H:%M:%S")
            self._log_entries.append((ts, msg))
            if len(self._log_entries) > 500:
                self._log_entries = self._log_entries[-200:]

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "is_live": self.is_live,
                "session_id": self.session_id,
                "room_title": self.room_title,
                "current_vision": self.current_vision,
                "room_id": self.room_id,
                "latest_sends": list(self.latest_sends),
                "uptime_seconds": (datetime.now() - self.start_time).total_seconds(),
            }

    def get_logs(self, after_line: int) -> list:
        with self._lock:
            if after_line < len(self._log_entries):
                return list(self._log_entries[after_line:])
            return []


# 全局单例，gui.py 初始化后赋值
global_state: "BotState | None" = None


def set_state(st: BotState):
    global global_state
    global_state = st

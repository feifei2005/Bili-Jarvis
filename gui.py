"""
GUI 入口 - pywebview + 后台 Bot 线程
"""
import asyncio
import json
import os
import sys
import threading
import traceback
import webview

from config_manager import config, CONFIG_PATH as _cfg_path
from bot_state import BotState, set_state as set_global_state
from log_util import set_log_interceptor


def _run_bot(state: BotState):
    state.add_log("GUI 启动，正在初始化...")
    from bot import main as bot_main
    from bot_config import BOT_DATA_DIR, ROOM_ID
    from log_util import init_log
    init_log(BOT_DATA_DIR)
    set_log_interceptor(lambda msg: state.add_log(msg))
    state.add_log(f"Bot 线程就绪，监控房间 {ROOM_ID}")
    asyncio.run(bot_main(bot_state=state))


class GuiApi:
    def __init__(self, state: BotState):
        self.state = state

    def get_config(self):
        c = config.export()
        c["_path"] = _cfg_path
        return c

    def save_settings(self, data: dict):
        try:
            for section, values in data.items():
                config.set_section(section, values)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def save_templates(self, data: dict):
        try:
            config.set_section("templates", data)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def get_status(self):
        return self.state.snapshot()

    def get_logs(self, after_line: int = 0):
        return self.state.get_logs(after_line)

    def restart_bot(self):
        import subprocess
        self.state.restart_requested = True
        try:
            exe = sys.executable
            cwd = os.path.dirname(exe)
            subprocess.Popen([exe] + [os.path.abspath(a) for a in sys.argv],
                           cwd=cwd, close_fds=True)
        except Exception:
            pass
        webview.windows[0].destroy()


def run():
    state = BotState()
    set_global_state(state)

    api = GuiApi(state)
    if getattr(sys, "frozen", False):
        html_path = os.path.join(sys._MEIPASS, "static", "index.html")
    else:
        html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "index.html")

    window = webview.create_window(
        "Bili-Jarvis",
        html_path,
        js_api=api,
        width=960,
        height=680,
        min_size=(800, 500),
    )

    bot_thread = threading.Thread(target=_run_bot, args=(state,), daemon=True)
    bot_thread.start()

    webview.start(debug=False)

    state.restart_requested = True
    bot_thread.join(timeout=10)


if __name__ == "__main__":
    run()

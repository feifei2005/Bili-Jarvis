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

from config_manager import config
from bot_state import BotState, set_state as set_global_state
from log_util import set_log_interceptor


def _run_bot(state: BotState):
    from bot import main as bot_main
    from bot_config import BOT_DATA_DIR
    from log_util import init_log
    init_log(BOT_DATA_DIR)
    set_log_interceptor(lambda msg: state.add_log(msg))
    asyncio.run(bot_main(bot_state=state))


class GuiApi:
    def __init__(self, state: BotState):
        self.state = state

    def get_config(self):
        return config.export()

    def save_settings(self, data: dict):
        try:
            for section, values in data.items():
                current = config._data.get(section, {})
                if isinstance(current, dict):
                    config.set_section(section, values)
                else:
                    config._data[section] = values
                    config.save()
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
            subprocess.Popen([sys.executable] + sys.argv, close_fds=True)
        except Exception:
            pass
        webview.windows[0].destroy()


def run():
    state = BotState()
    set_global_state(state)

    api = GuiApi(state)
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

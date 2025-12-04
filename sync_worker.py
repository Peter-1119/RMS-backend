# sync_worker.py
import os
import time
from multiprocessing import current_process

from app import create_app
from modules.docs import sync_eip


def sync_loop(interval_seconds: int = 1200):
    """
    背景同步 loop：
    每 interval_seconds 秒呼叫一次 sync_eip()。

    注意：這個函式會在「自己的 process」裡建立 app & app_context。
    """
    app = create_app()

    with app.app_context():
        print(
            f"[sync_eip worker] started (pid={os.getpid()}, "
            f"interval={interval_seconds}s, name={current_process().name})"
        )

        while True:
            try:
                resp = sync_eip()   # 這裡直接呼叫你的 view function
                try:
                    data = resp.get_json()
                except Exception:
                    # 避免非 JSON 時卡住
                    data = getattr(resp, "data", None)
                    if isinstance(data, (bytes, bytearray)):
                        data = data.decode("utf-8", errors="ignore")
                print("[sync_eip worker] result:", data)
            except Exception as e:
                print("[sync_eip worker] ERROR:", e)

            time.sleep(interval_seconds)


if __name__ == "__main__":
    # 單獨跑這個檔案，用來手動測試 worker 用
    sync_loop(1200)

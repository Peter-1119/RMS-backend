# __main__.py
import os
from multiprocessing import Process

from app import create_app
from sync_worker import sync_loop

app = create_app()

if __name__ == "__main__":
    # 🔥 避免 Flask debug reloader 啟兩次 worker：
    # 在 debug=True 時，只有 WERKZEUG_RUN_MAIN == "true" 的那個 process 才啟 worker
    should_start_worker = False

    # 如果你永遠都用 debug=True，可以直接判斷 WERKZEUG_RUN_MAIN
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        should_start_worker = True

    # 若之後換成 production server（不走 werkzeug reloader），也可以改成：
    # if not app.debug:
    #     should_start_worker = True

    if should_start_worker:
        worker = Process(
            target=sync_loop,
            kwargs={"interval_seconds": 1200},  # 20 分鐘 = 1200 秒
        )
        worker.daemon = True  # 👈 daemon: 主程式結束時自動跟著關掉
        worker.start()
        print(f"[main] sync_eip worker started (pid={worker.pid})")

    # 啟動 Flask dev server
    app.run("127.0.0.1", 5000, debug=True)

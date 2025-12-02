# sync_worker.py
import time
from app import create_app
from modules.docs import sync_eip  # 直接 import 你現有的函式

app = create_app()
app.app_context().push()  # 建立 Flask app context，讓 db() 等可以用

# def run_sync_loop(interval_seconds=60):
#     while True:
#         try:
#             with app.app_context():
#                 resp = sync_eip()
#                 # 可以視情況 print log
#                 print("[sync_eip worker]", resp.get_json())
#         except Exception as e:
#             print("[sync_eip worker] ERROR:", e)
#         time.sleep(interval_seconds)

if __name__ == "__main__":
    # run_sync_loop(60)  # 每 60 秒跑一次

    try:
        with app.app_context():
            resp = sync_eip()
            # 可以視情況 print log
            print("[sync_eip worker]", resp.get_json())
    except Exception as e:
        print("[sync_eip worker] ERROR:", e)
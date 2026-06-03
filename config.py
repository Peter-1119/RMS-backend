import os
import platform


def _load_dotenv(path):
    """極簡 .env 載入（純標準庫，無外部相依）：把 KEY=VALUE 寫進 os.environ。
    用 setdefault → 已存在的真實環境變數（如正式機注入）優先，不被 .env 覆蓋。"""
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


_load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

# secrets & environment（真實值放 .env，不入庫；範本見 .env.example）
SECRET_KEY = os.getenv("SECRET_KEY", "default_unsafe_key_change_this_in_production")

# db（連線資訊一律由 .env / 環境變數提供；此處預設為安全佔位，絕不放真實密碼）
DB = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", "3306")),
    "user": os.getenv("DB_USER", ""),
    "password": os.getenv("DB_PASSWORD", ""),
    "name": os.getenv("DB_NAME", ""),
    "charset": "utf8mb4",
}

# uploads
DRAWIO_CLI_PATH = os.getenv("DRAWIO_CLI_PATH", r"..\drawio-windows\draw.io.exe") if platform.system() == "Windows" else os.getenv("DRAWIO_CLI_PATH", "drawio") 
UPLOAD_FOLDER_NAME = "uploads"
BASE_DIR = os.getcwd()
UPLOAD_ROOT_DIR = os.path.join(BASE_DIR, UPLOAD_FOLDER_NAME)
TEMP_ROOT_DIR = os.path.join(UPLOAD_ROOT_DIR, "temp")
ALLOWED_EXTS = {"png","jpg","jpeg","gif","webp","bmp","svg"}

# step types (backend meaning)
STEP = {
    "PROCESS_FLOW": 0,
    "MGMT_CONDITION": 1,
    "INSTR_PARAM": 2,
    "INSTR_EXCEPTION": 3,
    "SPEC_RULE": 4,
    "SPEC_PARAM": 5,
    "SPEC_QUALITY": 6,
    "SPEC_OTHER": 7,
}
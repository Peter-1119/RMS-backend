import os

# secrets & environment
SECRET_KEY = os.getenv("SECRET_KEY", "default_unsafe_key_change_this_in_production")

# db
DB = {
    "host": os.getenv("DB_HOST", "10.1.5.185"),
    "port": int(os.getenv("DB_PORT", "3306")),
    "user": os.getenv("DB_USER", "sfuser"),
    "password": os.getenv("DB_PASSWORD", "sfuser6269"),
    "name": os.getenv("DB_NAME", "sfdb"),
    "charset": "utf8mb4",
}

# uploads
DRAWIO_CLI_PATH = os.getenv("DRAWIO_CLI_PATH", r"..\drawio-windows\draw.io.exe")
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

# CSV paths
CSV_DIR = os.path.join(BASE_DIR, "csvFiles")
CSV_MACHINES = os.path.join(CSV_DIR, "filtered-machines-information.csv")
CSV_PROJECTS = os.path.join(CSV_DIR, "project-specific-list.csv")

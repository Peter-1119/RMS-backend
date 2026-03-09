# oracle_db.py
import sys
import os
import oracledb
from contextlib import contextmanager

# --- CONFIG ---
CONFIG_DIR = os.getenv("ORACLE_TNS_DIR", r"C:\\oracle\\client_64\\network\\admin")   # folder with tnsnames.ora

if sys.platform == 'win32':
    LIB_DIR= os.getenv("ORACLE_IC_LIBDIR", r"C:\\oracle\\instantclient_23_9")       # Instant Client folder for windows
else:
    LIB_DIR= os.getenv("ORACLE_IC_LIBDIR", r"/home/kw60user/instantclient_19_25")       # Instant Client folder for wsl

DB_CONFIGS = {
    "default": {
        "dsn": "10.1.1.178:1521/PRDIDB",
        "user": "SFUSER",
        "password": "SFUSER#qazWSX"
    },
    "machine_db": {
        "dsn": "10.1.1.97:1521/SFISDB",
        "user": "SFUser",
        "password": "Flex@FSuser"
    },
    "item_db": {
        "dsn": "10.1.1.178:1521/PRDIDB",
        "user": "ezflex",
        "password": "flex111"
    },
    # ... 第三個資料庫
}

_pools = {}

def _init_client_once():
    # Thick mode 只需要初始化一次
    try:
        oracledb.init_oracle_client(lib_dir=LIB_DIR)
    except oracledb.ProgrammingError:
        pass # 避免重複初始化報錯

def get_pool(db_alias="default"):
    """根據 alias 取得對應的 Connection Pool"""
    global _pools
    
    if db_alias not in DB_CONFIGS:
        raise ValueError(f"Unknown DB alias: {db_alias}")

    if db_alias not in _pools:
        _init_client_once()
        conf = DB_CONFIGS[db_alias]
        # 建立該資料庫的 Pool
        _pools[db_alias] = oracledb.create_pool(
            user=conf["user"],
            password=conf["password"],
            dsn=conf["dsn"],
            min=1, max=8, increment=1, homogeneous=True, timeout=60, stmtcachesize=200
        )
    return _pools[db_alias]

@contextmanager
def ora_conn(db_alias="default"):
    pool = get_pool(db_alias)
    with pool.acquire() as conn:
        yield conn

@contextmanager
def ora_cursor(db_alias="default"):
    """
    現在您可以指定要連哪一個資料庫：
    with ora_cursor("second_db") as cur: ...
    """
    with ora_conn(db_alias) as conn:
        with conn.cursor() as cur:
            yield cur

if __name__ == "__main__":
    with ora_cursor() as cur:
        cur.execute("SELECT * FROM IDBUSER.RMS_DCC2EIP")
        cur.fetchall()
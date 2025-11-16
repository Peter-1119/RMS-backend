# oracle_db.py
import os
import pathlib
import oracledb
from contextlib import contextmanager

# --- CONFIG ---
# CONFIG_DIR = os.getenv("ORACLE_TNS_DIR", r"C:\\oracle\\client_64\\network\\admin")   # folder with tnsnames.ora
LIB_DIR    = os.getenv("ORACLE_IC_LIBDIR", r"C:\\oracle\\instantclient_23_9")       # Instant Client folder
DSN_ALIAS  = os.getenv("ORACLE_DSN", "10.1.1.186:1521/QASIDB")  # or "PRDIDB"
USER       = os.getenv("ORACLE_USER", "SFUSER")
PASSWORD   = os.getenv("ORACLE_PASSWORD", "SFUSER#qazWSX")

_pool = None

def _init_client_once():
    # Thick mode (required if your DB server is older than Thin supports)
    oracledb.init_oracle_client(lib_dir=LIB_DIR)

def get_pool():
    global _pool
    if _pool is None:
        _init_client_once()
        # build pool
        _pool = oracledb.create_pool(
            user=USER, password=PASSWORD, dsn=DSN_ALIAS,
            min=1, max=8, increment=1, homogeneous=True, timeout=60, stmtcachesize=200
        )
    return _pool

@contextmanager
def ora_conn():
    pool = get_pool()
    with pool.acquire() as conn:
        yield conn

@contextmanager
def ora_cursor():
    with ora_conn() as conn:
        with conn.cursor() as cur:
            yield cur

def diag():
    print("Mode:", "Thick" if not oracledb.is_thin_mode() else "Thin")
    print("DSN alias:", DSN_ALIAS)

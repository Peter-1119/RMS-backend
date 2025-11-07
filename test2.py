import oracledb, os, pathlib

# --- Point to your tnsnames.ora directory ---
CONFIG_DIR = r"C:\\oracle\\client_64\\network\\admin"

# --- Point to your Instant Client directory ---
LIB_DIR = r"C:\\oracle\\instantclient_23_9"   # adjust to your actual folder

# Initialize THICK mode (required for older DB servers)
oracledb.init_oracle_client(lib_dir=LIB_DIR, config_dir=CONFIG_DIR)

# Optional diagnostics
print("Mode:", "Thick" if not oracledb.is_thin_mode() else "Thin")
print("tnsnames.ora exists:", pathlib.Path(CONFIG_DIR, "tnsnames.ora").exists())

# Build a session pool against your TNS alias (QASIDB or PRDIDB)
pool = oracledb.create_pool(
    user="SFUSER",
    password="SFUSER#qazWSX",
    dsn="QASIDB",                      # or "PRDIDB"
    min=2, max=10, increment=1,
    timeout=60, homogeneous=True,
    stmtcachesize=100
)

with pool.acquire() as conn:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
              sys_context('USERENV','SESSION_USER') AS session_user,
              banner
            FROM v$version
            WHERE banner LIKE 'Oracle%'
        """)  # <-- no semicolon
        print(cur.fetchone())

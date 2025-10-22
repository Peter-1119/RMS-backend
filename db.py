import MySQLdb
import MySQLdb.cursors  # <-- add this line
from contextlib import contextmanager
from config import DB

def _connect(dict_cursor=False):
    kwargs = {
        "host": DB["host"], "port": DB["port"], "user": DB["user"],
        "passwd": DB["password"], "db": DB["name"], "charset": DB["charset"]
    }
    if dict_cursor:
        kwargs["cursorclass"] = MySQLdb.cursors.DictCursor
    return MySQLdb.connect(**kwargs)

@contextmanager
def db(dict_cursor=False):
    conn = _connect(dict_cursor=dict_cursor)
    cur = conn.cursor()
    try:
        yield conn, cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        try: cur.close(); conn.close()
        except: pass

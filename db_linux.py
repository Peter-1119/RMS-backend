# db.py (PyMySQL direct)
import pymysql
from pymysql.cursors import DictCursor as PyDictCursor
from contextlib import contextmanager
from config import DB

def _connect(dict_cursor=False):
    kwargs = {
        "host": DB["host"],
        "port": DB["port"],
        "user": DB["user"],
        "password": DB["password"],   # note: PyMySQL uses 'password'
        "database": DB["name"],
        "charset": DB["charset"],
        "autocommit": False
    }
    if dict_cursor:
        kwargs["cursorclass"] = PyDictCursor
    return pymysql.connect(**kwargs)

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

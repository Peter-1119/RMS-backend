import hashlib, inspect
import hmac
import os

# 確保 SECRET_KEY 在所有檔案中都保持一致
# 建議從環境變數讀取
SECRET_KEY = os.getenv("SECRET_KEY", "default_unsafe_key_change_this_in_production")
SECRET_KEY = SECRET_KEY.encode('utf-8')

def generate_signature(data_string):
    """
    使用 HMAC-SHA256 產生資料簽名。

    Args:
        data_string (str): 要簽名的資料字串。

    Returns:
        str: 產生的簽名字串 (十六進位格式)。
    """
    # 確保資料為 bytes
    data_bytes = data_string.encode('utf-8')
    # 使用 SECRET_KEY 和 SHA256 演算法產生簽名
    signature = hmac.new(SECRET_KEY, data_bytes, hashlib.sha256).hexdigest()
    return signature

def verify_signature(data_string, signature):
    """
    驗證資料簽名是否有效。

    Args:
        data_string (str): 原始資料字串。
        signature (str): 接收到的簽名字串。

    Returns:
        bool: 如果簽名匹配，返回 True；否則返回 False。
    """
    expected_signature = generate_signature(data_string)
    # 使用 hmac.compare_digest() 來進行安全的字串比對，防止時序攻擊
    return hmac.compare_digest(expected_signature, signature)

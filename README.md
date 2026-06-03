# RMS-backend

製造條件**指示書 / 式樣書**（RMS, Recipe/Document Management System）的後端服務。
以 **Flask + MySQL** 為核心，負責文件草稿、階層化內容區塊、Word 文件產生、版本快照，以及與 EIP / Oracle 的整合同步。

---

## 技術棧

| 類別 | 內容 |
|---|---|
| 語言 / 框架 | Python 3.10+、Flask、Flask-CORS |
| 主資料庫 | MySQL（`mysqlclient` / `MySQLdb`） |
| 外部整合 | Oracle（`oracledb`，EIP 建檔 / sync）、requests |
| 文件產生 | `python-docx`、Pillow（圖片）、xlsxwriter / xmltodict |
| 設定 | `.env`（連線資訊；見下方） |

> 目前尚無 `requirements.txt`。主要第三方套件：
> `Flask flask-cors mysqlclient oracledb python-docx Pillow requests xlsxwriter xmltodict`

---

## 快速開始

```bash
# 1. 安裝相依（建議用 venv / conda）
pip install Flask flask-cors mysqlclient oracledb python-docx Pillow requests xlsxwriter xmltodict

# 2. 建立 .env（複製範本後填入真實連線資訊）
copy .env.example .env        # Windows
# cp .env.example .env        # macOS / Linux

# 3. 啟動（Flask dev server + sync 背景 worker）
python __main__.py
```

- 服務埠：**2150**（`__main__.py` 裡 `app.run("0.0.0.0", 2150, debug=True)`）。
- 啟動時會同時拉起 `sync_worker.sync_loop`（每 1200 秒跑一次 EIP 同步）。

### 設定（.env）

連線資訊**一律由 `.env` / 環境變數**提供，`config.py` 內不再放真實密碼。

```
SECRET_KEY=...
DB_HOST=...
DB_PORT=3306
DB_USER=...
DB_PASSWORD=...
DB_NAME=...
```

- `config.py` 內建極簡 `.env` 載入器（純標準庫，不需裝 `python-dotenv`）；正式機注入的環境變數會優先於 `.env`。
- ⚠️ **`.env` 已被 `.gitignore` 忽略，請勿提交**；新成員 / 部署機請依 `.env.example` 自行建立。

---

## 目錄結構

```
app.py                      # create_app()：註冊各 blueprint
__main__.py                 # 進入點：啟 Flask + sync worker（port 2150）
config.py                   # 設定（DB 從 .env 讀）
db.py / oracle_db.py        # MySQL / Oracle 連線
utils.py                    # 共用工具
sync_worker.py              # EIP 同步背景迴圈（sync-eip）
loginFunctions/             # 登入 / 簽章相關

modules/
  auth_bp.py                # 認證（/api）
  docs.py                   # 文件主流程：草稿存讀、Word 產生、快照、變版（/docs）
  block_tree.py             # ★ 內容區塊樹模型 + 舊→新資料轉換器
  media.py                  # 圖片上傳 / 服務（/uploads）
  mes.py conditions.py item.py parameters.py dcc.py department.py

DocxDefinition_.py                 # ★ Word 產生（有外框版，新階層樹）
DocxDefinitionNoFramework_.py      # ★ Word 產生（無外框版，新階層樹）
DocxDefinition.py / ...NoFramework.py   # 舊版（保留）

SQLScripts/                 # 建表 / 資料維護 SQL
migrate_sfdb4070_to_sfdb.py # 舊庫→新庫一次性遷移（gitignore）
```

> 帶結尾底線的 `*_` 檔（`DocxDefinition_.py`、`block_tree` 等）是**新階層樹**版本，為目前主線；不帶底線的是舊版，逐步淘汰。

---

## 核心概念

- **文件類型 `document_type`**：`0` = 指示書（製造條件指示書），`1` = 式樣書（製造式樣書）。
- **章節 `step_type`**：指示書 `0~3`（製造流程 / 管理條件 / 製造參數 / 異常），式樣書 `4~7`（製作條件 / 製造參數 / 品質 / 其他）。
- **內容區塊樹（block tree v2）**：`rms_block_content` 改採鄰接串列 `parent_id / sort_order / depth`（L2~L8），取代舊的 `tier_no / sub_no` 平面座標。
  - `content_type`：`0` 純標題、`1` 文字、`2` 表格、`3` 插入文件、`4` 參數表（step 2/5 leaf）。
  - 編號規則：L2 `2.1`、L3 `2.1.1`、L4~L8 `(1)/(A)/(a)/(I)/(i)`。
- **基本生產條件（PMS）**：指示書 step1 的第一個區塊（`3.1`）固定為「基本生產條件」（`metadata.source=management`）。
- **form_attributes**：`目的 / 文件名 / 適用工程` 的 tiptap 樣式（`style_json`）存於 `rms_document_form_attributes`，Word 產生時用來上色（指示書三欄、式樣書只有「目的」）。
- **版本快照**：簽核 / 下載時凍結整份文件到 `rms_document_snapshots` + `rms_document_snapshot_payloads`（含 `form_attributes`），供簽核預覽與變版回溯。
- **EIP / Oracle 同步**：`sync_worker` 定期把簽核快照寫回主庫並建 Oracle 檔（sync-eip）。

---

## 資料庫

- **新庫 `sfdb`**：目前主庫（建表 SQL 見 `SQLScripts/`）。
- **舊庫 `sfdb4070`**：歷史資料來源，`migrate_sfdb4070_to_sfdb.py` 做一次性遷移（含舊平面座標→樹、內容分類、step5 program-code 前綴、定值項目欄、缺 PMS 補空基本生產條件等）。

---

## 目前狀態 / 近期重點

- ✅ 內容區塊由舊平面座標（tier/sub）改為**階層樹**（parent_id/sort_order/depth），前後端與 Word 產生皆走 `*_` 新版。
- ✅ 舊庫 `sfdb4070` → 新庫 `sfdb` 遷移腳本完成（草稿可改、簽核檔內容保持一致）。
- ✅ Word 產生支援 `form_attributes` 彩色渲染（目的 / 文件名 / 適用工程）。
- ✅ DB 連線資訊改由 `.env` 提供，`config.py` 不再內嵌密碼。
- ⏳ 待辦：補 `requirements.txt`；正式機改用 production WSGI server（目前為 Flask dev server）。

---

## 注意事項

- **絕不提交 `.env`**；密鑰 / 連線資訊只放 `.env` 或環境變數。
- 若 DB 密碼曾外洩，務必到 DB 端**更換密碼**後再更新 `.env`（清掉 git 歷史無法取代換密碼）。
- 啟動為 `debug=True` 的開發模式，**勿直接用於正式環境**。

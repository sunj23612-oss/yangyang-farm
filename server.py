#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
洋洋农场 - 联网版后端服务
============================
纯 Python 标准库实现（零第三方依赖）：http.server + sqlite3 + hashlib + secrets。

功能：
  - 注册 / 登录 / 会话 token（一号一档：每账号独立存档）
  - 云端存档读写（PUT /api/save 整个游戏状态 G）
  - 好友：添加好友、好友列表、好友农场只读视图
  - 偷菜：好友成熟作物可偷（每日每好友限额 STEAL_LIMIT_PER_FRIEND），
    被偷者收到通知，偷者获得作物进背包
  - 静态托管 web/ 目录下的前端页面

启动：
  python server.py [port]        # 默认 8000，浏览器访问 http://127.0.0.1:8000/
部署：
  可直接运行于任意有 Python 3 的服务器（公网 IP / 云主机 / NAS 均可）；
  如需 HTTPS / 域名，建议用 nginx/caddy 反代到本服务端口。

数据文件：
  farm.db  （SQLite，自动创建于本文件同目录）
"""

import hashlib
import hmac
import json
import mimetypes
import os
import random
import re
import secrets
import sqlite3
import string
import sys
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    from libsql_db import TursoDB       # 云端 libsql（Turso）适配层
except ImportError:                     # 只拷 server.py 时降级为本地 sqlite3
    TursoDB = None

# ---------------------------------------------------------------- 配置
# 云端平台（Koyeb/Render）以环境变量 PORT 注入监听端口；未注入时回退命令行参数 / 8000
PORT = int(os.environ.get("PORT") or (sys.argv[1] if len(sys.argv) > 1 else 8000))
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(BASE_DIR, "web")
DB_PATH = os.path.join(BASE_DIR, "farm.db")

USERNAME_RE = re.compile(r"^[A-Za-z0-9_\u4e00-\u9fa5]{2,20}$")
PASSWORD_RE = re.compile(r"^[\x21-\x7e]{4,32}$")   # 可见 ASCII 4~32 位
TOKEN_BYTES = 32
STEAL_LIMIT_PER_FRIEND = 3          # 每天最多偷同一位好友 3 次
STEAL_DAY_SECONDS = 86400
GUARD_STEAL_BLOCK = 0.5             # 对方农场有守夜狗时，每次偷菜被赶跑的概率
MAX_SAVE_BYTES = 1024 * 1024        # 存档上限 1MB
FRIEND_LIMIT = 50                   # 好友上限
FIELD_CELLS = 24                    # 菜地格子数
GARDEN_CELLS = 12                   # 花园格子数

# ---------------------------------------------------------------- 数据库
_conn = None
_cloud_db = None

# 建表脚本：本地 sqlite3 与云端 libsql 共用同一套 schema（幂等）
_SCHEMA_SQL = """
        CREATE TABLE IF NOT EXISTS users (
          username TEXT PRIMARY KEY,
          salt     TEXT NOT NULL,
          passhash TEXT NOT NULL,
          created  INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS saves (
          username   TEXT PRIMARY KEY,
          save_json  TEXT NOT NULL,
          updated_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
          token     TEXT PRIMARY KEY,
          username  TEXT NOT NULL,
          created   INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS friends (
          user_a TEXT NOT NULL,
          user_b TEXT NOT NULL,
          t      INTEGER NOT NULL,
          PRIMARY KEY (user_a, user_b)
        );
        CREATE TABLE IF NOT EXISTS steals (
          id       INTEGER PRIMARY KEY AUTOINCREMENT,
          thief    TEXT NOT NULL,
          victim   TEXT NOT NULL,
          kind     TEXT NOT NULL,      -- field | garden
          idx      INTEGER NOT NULL,
          crop     TEXT NOT NULL,
          day      TEXT NOT NULL,      -- 服务器日期（UTC 当日字符串）
          created  INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_steals_thief_day ON steals(thief, day);
        CREATE INDEX IF NOT EXISTS idx_steals_victim ON steals(victim);
        """


def db():
    """返回数据库连接。

    云端模式：设置了 TURSO_URL + TURSO_TOKEN 时连 Turso/libsql（存档保存在云端，
    适配 Koyeb/Render 等临时盘 PaaS 的免费层）；未设置时回退本机 farm.db。
    """
    global _conn, _cloud_db
    url = (os.environ.get("TURSO_URL") or "").strip()
    token = (os.environ.get("TURSO_TOKEN") or "").strip()
    if url and token:
        if _cloud_db is None:
            if TursoDB is None:
                raise RuntimeError("云端模式需要 libsql_db.py（本工程已提供）")
            _cloud_db = TursoDB(url, token)
            _cloud_db.executescript(_SCHEMA_SQL)
        return _cloud_db
    if _conn is None:
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL;")
        _conn.executescript(_SCHEMA_SQL)
        _conn.commit()
    return _conn


# ---------------------------------------------------------------- 密码 & 会话
def hash_password(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                               salt.encode("utf-8"), 120_000).hex()


def new_user(username, password):
    salt = secrets.token_hex(16)
    c = db()
    try:
        c.execute("INSERT INTO users(username, salt, passhash, created) VALUES(?,?,?,?)",
                  (username, salt, hash_password(password, salt), int(time.time())))
        c.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def check_login(username, password):
    c = db()
    row = c.execute("SELECT salt, passhash FROM users WHERE username=?", (username,)).fetchone()
    if not row:
        return False
    return hmac.compare_digest(hash_password(password, row["salt"]), row["passhash"])


def issue_token(username):
    tok = secrets.token_hex(TOKEN_BYTES)
    db().execute("INSERT INTO sessions(token, username, created) VALUES(?,?,?)",
                 (tok, username, int(time.time())))
    db().commit()
    return tok


def user_by_token(token):
    row = db().execute("SELECT username FROM sessions WHERE token=?", (token,)).fetchone()
    return row["username"] if row else None


def logout(token):
    db().execute("DELETE FROM sessions WHERE token=?", (token,))
    db().commit()


# ---------------------------------------------------------------- 存档
MAX_HEROES = 2                       # 人物上限
MAX_PETS = 2                         # 宠物上限
MAX_FAV = 100                        # 好感度上限
PET_MODEL_IDS = ("husky", "cat_shep", "dog_shep", "cat_orange", "dog_shepherd")


def _norm_hero(h):
    if not isinstance(h, dict):
        h = {}
    return {"id": str(h.get("id") or "xu")[:20],
            "name": str(h.get("name") or "洋洋")[:12],
            "gender": "female" if h.get("gender") == "female" else "male",
            "skin": str(h.get("skin") or "xu")[:12]}


def _norm_pet(p):
    if not isinstance(p, dict):
        p = {}
    kind = "dog" if p.get("kind") == "dog" else "cat"
    model = p.get("model")
    if model not in PET_MODEL_IDS:
        model = "dog_shepherd" if kind == "dog" else "cat_shep"
    try:
        fav = max(0, min(MAX_FAV, int(round(float(p.get("fav") or 0)))))
    except (TypeError, ValueError):
        fav = 0
    try:
        fed = max(0, int(round(float(p.get("fed") or 0))))
    except (TypeError, ValueError):
        fed = 0
    return {"id": str(p.get("id") or "pet")[:20],
            "name": str(p.get("name") or p.get("id") or "宠物")[:12],
            "kind": kind, "model": model, "fav": fav,
            "guard": bool(p.get("guard")) and kind == "dog", "fed": fed}


def normalize_save(d):
    """前后端存档契约归一化：人物 1-2 人(含性别)、宠物 0-2 只(猫/狗+5模型)、
    装备槽(hoe/water/seed)、好感度(0-100)/守夜/投喂统计；旧档 cats 自动迁移为 pets。"""
    if not isinstance(d, dict):
        return d
    out = dict(d)
    hs = out.get("heroes")
    if not isinstance(hs, list) or not hs:
        out["heroes"] = [{"id": "xu", "name": "洋洋", "gender": "male", "skin": "xu"}]
    else:
        out["heroes"] = [_norm_hero(h) for h in hs[:MAX_HEROES]]
    ps = out.get("pets")
    if not isinstance(ps, list):
        ps = []
        old = out.get("cats") or {}
        if isinstance(old, dict):
            for i, (k, v) in enumerate(old.items()):
                fav = int(v.get("fav") or 0) if isinstance(v, dict) else 0
                ps.append({"id": str(k), "name": str(k),
                           "kind": "cat" if i % 2 == 0 else "dog",
                           "model": "cat_orange" if i % 2 == 0 else "dog_shep",
                           "fav": max(0, min(MAX_FAV, fav)), "guard": False, "fed": 0})
    out["pets"] = [_norm_pet(p) for p in (ps[:MAX_PETS] if isinstance(ps, list) else [])]
    h = out.get("hand")
    out["hand"] = {"hoe": bool(h.get("hoe")) if isinstance(h, dict) else False,
                   "water": bool(h.get("water")) if isinstance(h, dict) else False,
                   "seed": (h.get("seed") or None) if isinstance(h, dict) else None}
    return out


def load_save(username):
    row = db().execute("SELECT save_json FROM saves WHERE username=?", (username,)).fetchone()
    if not row:
        return None
    try:
        return normalize_save(json.loads(row["save_json"]))
    except Exception:
        return None


def store_save(username, data):
    db().execute(
        "INSERT INTO saves(username, save_json, updated_at) VALUES(?,?,?) "
        "ON CONFLICT(username) DO UPDATE SET save_json=excluded.save_json, updated_at=excluded.updated_at",
        (username, json.dumps(normalize_save(data), ensure_ascii=False), int(time.time())))
    db().commit()


# ---------------------------------------------------------------- 好友
def today_key():
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() + STEAL_DAY_SECONDS // 2))


def friends_of(username):
    rows = db().execute("SELECT user_b AS f FROM friends WHERE user_a=? "
                        "UNION SELECT user_a AS f FROM friends WHERE user_b=? "
                        "ORDER BY f", (username, username)).fetchall()
    return [r["f"] for r in rows]


def add_friend(a, b):
    if a == b:
        return "self"
    if len(friends_of(a)) >= FRIEND_LIMIT:
        return "limit"
    if db().execute("SELECT 1 FROM users WHERE username=?", (b,)).fetchone() is None:
        return "notfound"
    try:
        db().execute("INSERT OR IGNORE INTO friends(user_a, user_b, t) VALUES(?,?,?)",
                     (a, b, int(time.time())))
        db().commit()
        return "ok"
    except sqlite3.Error:
        return "err"


def remove_friend(a, b):
    db().execute("DELETE FROM friends WHERE (user_a=? AND user_b=?) OR (user_a=? AND user_b=?)",
                 (a, b, b, a))
    db().commit()


def are_friends(a, b):
    return db().execute("SELECT 1 FROM friends WHERE (user_a=? AND user_b=?) OR (user_a=? AND user_b=?)",
                        (a, b, b, a)).fetchone() is not None


# ---------------------------------------------------------------- 偷菜
def steal_log_for(username):
    rows = db().execute(
        "SELECT * FROM steals WHERE victim=? ORDER BY id DESC LIMIT 30", (username,)).fetchall()
    return [dict(r) for r in rows]


def steal_from_victim(thief, victim, kind, idx, save):
    """在事务外调用：已校验好友 + 当日额度 + 目标成熟。返回偷到的作物 id 或 None。"""
    arr = save.get(kind) if isinstance(save, dict) else None
    if not isinstance(arr, list):
        return None
    if idx < 0 or idx >= len(arr):
        return None
    cell = arr[idx]
    if not isinstance(cell, dict):
        return None
    key = "crop" if kind == "field" else "flower"
    if key not in cell or cell.get("stage", 0) < 3:
        return None
    crop = cell[key]
    if kind == "field":
        arr[idx] = {"tilled": True}
    else:
        arr[idx] = None
    day = today_key()
    db().execute(
        "INSERT INTO steals(thief, victim, kind, idx, crop, day, created) VALUES(?,?,?,?,?,?,?)",
        (thief, victim, kind, idx, crop, day, int(time.time())))
    db().commit()
    return crop


def steal_count_today(thief, victim):
    row = db().execute(
        "SELECT COUNT(*) AS n FROM steals WHERE thief=? AND victim=? AND day=?",
        (thief, victim, today_key())).fetchone()
    return row["n"] if row else 0


def public_farm(save):
    """好友视角的农场公开信息：只暴露格子类型与成熟度，不暴露背包/金币/存档细节。
    额外提供对方农场当前小时（hour）与宠物摘要（含守夜狗），供前端做昼夜与守夜提示。"""
    if not isinstance(save, dict):
        save = {}
    field = []
    src_field = save.get("field")
    if not isinstance(src_field, list):
        src_field = []
    for i in range(FIELD_CELLS):
        cell = src_field[i] if i < len(src_field) else None
        field.append(_pub_cell(cell, "crop"))
    garden = []
    src_garden = save.get("garden")
    if not isinstance(src_garden, list):
        src_garden = []
    for i in range(GARDEN_CELLS):
        cell = src_garden[i] if i < len(src_garden) else None
        garden.append(_pub_cell(cell, "flower"))
    pets = []
    for p in (save.get("pets") or []):
        if not isinstance(p, dict):
            continue
        pets.append({"id": str(p.get("id") or "")[:20],
                     "name": str(p.get("name") or "宠物")[:12],
                     "kind": "dog" if p.get("kind") == "dog" else "cat",
                     "guard": bool(p.get("guard"))})
    h = save.get("hour")
    hour = int(h) if isinstance(h, (int, float)) and not isinstance(h, bool) else 6
    return {"field": field, "garden": garden, "day": save.get("day", 1),
            "hour": hour, "pets": pets}


def _pub_cell(cell, key):
    if not isinstance(cell, dict):
        return None
    return {"kind": key, "id": cell.get(key), "stage": cell.get("stage", 0),
            "wet": bool(cell.get("wet")), "fert": cell.get("fert", 0)}


# ---------------------------------------------------------------- 请求处理
def send_json(handler, code, obj):
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def send_static(handler, path):
    # 路径归一：浏览器会 percent-encode，curl 直发 UTF-8（BaseHTTPRequestHandler 按 latin-1 解码）
    path = urllib.parse.unquote(path)
    try:
        path = path.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    # 防目录穿越
    rel = os.path.normpath(path.lstrip("/"))
    if rel.startswith("..") or os.path.isabs(rel):
        handler.send_error(403)
        return
    full = os.path.join(WEB_DIR, rel)
    if os.path.isdir(full):
        # 目录请求：优先 index.html，其次默认游戏页
        cand = os.path.join(full, "index.html")
        if not os.path.isfile(cand):
            cand = os.path.join(full, "洋洋农场.html")
        full = cand
    if not os.path.isfile(full):
        handler.send_error(404)
        return
    ctype, _ = mimetypes.guess_type(full)
    with open(full, "rb") as f:
        body = f.read()
    handler.send_response(200)
    handler.send_header("Content-Type", ctype or "application/octet-stream")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def read_json_body(handler):
    try:
        n = int(handler.headers.get("Content-Length", "0"))
    except ValueError:
        n = 0
    if n <= 0 or n > 2 * 1024 * 1024:
        return None
    raw = handler.rfile.read(n)
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return None


class Handler(BaseHTTPRequestHandler):
    server_version = "YangyangFarm/1.0"

    def log_message(self, format, *args):
        sys.stderr.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), format % args))

    def _route_api(self, method):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)
        body = read_json_body(self) if method == "POST" else None
        body = body if isinstance(body, dict) else {}

        def auth():
            tok = (body or {}).get("token") or qs.get("token", [""])[0]
            if not tok:
                ah = self.headers.get("Authorization") or ""
                if ah.lower().startswith("bearer "):
                    tok = ah[7:].strip()
            return user_by_token(tok) if tok else None

        # ---------------- GET ----------------
        if method == "GET":
            if path in ("/", "/index.html") or path.startswith("/web/") or not path.startswith("/api/"):
                send_static(self, path.replace("/web/", "/", 1) if path.startswith("/web/") else path)
                return
            if path == "/api/me":
                u = auth()
                if not u:
                    return send_json(self, 401, {"ok": False, "err": "未登录"})
                save = load_save(u)
                return send_json(self, 200, {"ok": True, "username": u,
                                             "save": save, "friends": friends_of(u),
                                             "steal_log": steal_log_for(u)})
            if path == "/api/friends":
                u = auth()
                if not u:
                    return send_json(self, 401, {"ok": False, "err": "未登录"})
                out = []
                for f in friends_of(u):
                    out.append({"username": f, "farm": public_farm(load_save(f) or {})})
                return send_json(self, 200, {"ok": True, "friends": out})
            if path == "/api/steal_log":
                u = auth()
                if not u:
                    return send_json(self, 401, {"ok": False, "err": "未登录"})
                return send_json(self, 200, {"ok": True, "log": steal_log_for(u)})
            send_json(self, 404, {"ok": False, "err": "接口不存在"})
            return

        # ---------------- POST ----------------
        if method == "POST":
            if path == "/api/register":
                name = str(body.get("username", ""))
                pw = str(body.get("password", ""))
                if not USERNAME_RE.match(name):
                    return send_json(self, 400, {"ok": False, "err": "用户名需 2~20 位（中英文/数字/下划线）"})
                if not PASSWORD_RE.match(pw):
                    return send_json(self, 400, {"ok": False, "err": "密码需 4~32 位可见字符"})
                if new_user(name, pw):
                    return send_json(self, 200, {"ok": True, "token": issue_token(name), "username": name})
                return send_json(self, 409, {"ok": False, "err": "用户名已被注册"})

            if path == "/api/login":
                name = str(body.get("username", ""))
                pw = str(body.get("password", ""))
                if check_login(name, pw):
                    return send_json(self, 200, {"ok": True, "token": issue_token(name), "username": name})
                return send_json(self, 401, {"ok": False, "err": "用户名或密码错误"})

            if path == "/api/logout":
                tok = str(body.get("token", ""))
                if tok:
                    logout(tok)
                return send_json(self, 200, {"ok": True})

            if path == "/api/save":
                u = auth()
                if not u:
                    return send_json(self, 401, {"ok": False, "err": "未登录"})
                data = body.get("save")
                if not isinstance(data, dict):
                    return send_json(self, 400, {"ok": False, "err": "存档必须是对象"})
                raw = json.dumps(data, ensure_ascii=False)
                if len(raw.encode("utf-8")) > MAX_SAVE_BYTES:
                    return send_json(self, 413, {"ok": False, "err": "存档过大"})
                store_save(u, data)
                return send_json(self, 200, {"ok": True, "updated_at": int(time.time())})

            if path == "/api/friends/add":
                u = auth()
                if not u:
                    return send_json(self, 401, {"ok": False, "err": "未登录"})
                b = str(body.get("username", "")).strip()
                res = add_friend(u, b)
                if res == "ok":
                    return send_json(self, 200, {"ok": True, "friends": friends_of(u)})
                if res == "notfound":
                    return send_json(self, 404, {"ok": False, "err": "找不到该用户"})
                if res == "self":
                    return send_json(self, 400, {"ok": False, "err": "不能添加自己为好友"})
                return send_json(self, 400, {"ok": False, "err": "好友已达上限或已是好友"})

            if path == "/api/friends/remove":
                u = auth()
                if not u:
                    return send_json(self, 401, {"ok": False, "err": "未登录"})
                b = str(body.get("username", "")).strip()
                remove_friend(u, b)
                return send_json(self, 200, {"ok": True, "friends": friends_of(u)})

            if path == "/api/steal":
                u = auth()
                if not u:
                    return send_json(self, 401, {"ok": False, "err": "未登录"})
                victim = str(body.get("friend", "")).strip()
                kind = str(body.get("kind", ""))
                idx = body.get("idx")
                if kind not in ("field", "garden") or not isinstance(idx, int) or idx < 0 \
                        or idx >= (FIELD_CELLS if kind == "field" else GARDEN_CELLS):
                    return send_json(self, 400, {"ok": False, "err": "参数不合法"})
                if not are_friends(u, victim):
                    return send_json(self, 403, {"ok": False, "err": "只能偷好友的菜"})
                if steal_count_today(u, victim) >= STEAL_LIMIT_PER_FRIEND:
                    return send_json(self, 429, {"ok": False, "err": "今天已偷过这位好友太多次啦（每日限 %d 次）" % STEAL_LIMIT_PER_FRIEND})
                save = load_save(victim)
                if save is None:
                    return send_json(self, 404, {"ok": False, "err": "好友还没建立农场存档"})
                # 好友偷菜仅限夜间（以对方农场当前小时计：晚上 19:00 - 次日 6:00）
                h = save.get("hour")
                victim_hour = int(h) if isinstance(h, (int, float)) and not isinstance(h, bool) else 6
                if not (victim_hour >= 19 or victim_hour < 6):
                    return send_json(self, 403, {"ok": False,
                                                 "err": "☀️ 现在是白天（好友农场 %d 点），晚上 19 点后再来偷菜吧！" % victim_hour})
                # 守夜狗概率拦截：好友农场有守夜狗时，偷菜有概率被赶跑（被拦同样消耗今日次数）
                guard_dogs = [p for p in (save.get("pets") or [])
                              if isinstance(p, dict) and p.get("guard") and p.get("kind") == "dog"]
                if guard_dogs and random.random() < GUARD_STEAL_BLOCK:
                    gd = guard_dogs[0]
                    # 记一条「被守夜狗拦下」的记录占今日次数（作物不动）
                    db().execute(
                        "INSERT INTO steals(thief, victim, kind, idx, crop, day, created) VALUES(?,?,?,?,?,?,?)",
                        (u, victim, kind, idx, "blocked", today_key(), int(time.time())))
                    db().commit()
                    left = STEAL_LIMIT_PER_FRIEND - steal_count_today(u, victim)
                    return send_json(self, 409, {"ok": False,
                                                 "err": "🐕 " + str(gd.get("name") or "守夜狗")
                                                        + " 汪汪扑来把你赶跑了，偷菜失败（已扣 1 次今日次数，今日剩余 %d 次）！" % left})
                crop = steal_from_victim(u, victim, kind, idx, save)
                if crop is None:
                    return send_json(self, 409, {"ok": False, "err": "这格没有成熟的作物可以偷"})
                store_save(victim, save)
                return send_json(self, 200, {"ok": True, "crop": crop,
                                             "left": STEAL_LIMIT_PER_FRIEND - steal_count_today(u, victim)})
            send_json(self, 404, {"ok": False, "err": "接口不存在"})
            return

        send_json(self, 405, {"ok": False, "err": "方法不支持"})

    do_GET = lambda self: self._route_api("GET")
    do_POST = lambda self: self._route_api("POST")


def main():
    if not os.path.isdir(WEB_DIR):
        print("未找到 web/ 目录，请确认与 server.py 同级。")
        sys.exit(1)
    db()  # 初始化建表
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print("==============================================")
    print("  洋洋农场 · 联网版服务已启动")
    print("  本机访问:  http://127.0.0.1:%d/" % PORT)
    print("  局域网:    http://<本机IP>:%d/  （需放行防火墙）" % PORT)
    if os.environ.get("TURSO_URL") and os.environ.get("TURSO_TOKEN"):
        print("  数据存储:  云端 libsql（%s）" % os.environ.get("TURSO_URL"))
    else:
        print("  数据文件:  %s" % DB_PATH)
    print("  按 Ctrl+C 停止")
    print("==============================================")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止")
        server.shutdown()


if __name__ == "__main__":
    main()
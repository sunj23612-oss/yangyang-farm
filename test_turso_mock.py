#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_turso_mock.py - libsql_db 适配层的本地契约测试（无需真实 Turso 账号）
============================================================================
起一个本地 mock HTTP 服务模拟 Turso 的 POST /v2/pipeline，验证：
  1. executescript 多条 DDL 拆分逐条执行
  2. execute 参数绑定 + 行解析（integer/text/null 类型转换）
  3. fetchone / fetchall 游标语义
  4. PRAGMA 语句被本地跳过（不发请求）
  5. 唯一约束冲突 -> sqlite3.IntegrityError（HTTP 400 与 pipeline error 两种路径）
  6. 其他错误 -> sqlite3.Error

用法：python test_turso_mock.py
"""

import json
import os
import sqlite3
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from libsql_db import TursoDB  # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print("  PASS  %s" % name)
    else:
        print("  FAIL  %s  %s" % (name, detail))
        FAILED.append(name)


class MockLibsql(BaseHTTPRequestHandler):
    received_sqls = []
    server_version = "MockLibsql/1.0"

    def log_message(self, format, *args):
        pass

    def do_POST(self):
        # 必须走 /v2/pipeline
        if self.path != "/v2/pipeline":
            self.send_response(404)
            self.end_headers()
            return
        n = int(self.headers.get("Content-Length", "0") or 0)
        body = json.loads(self.rfile.read(n).decode("utf-8"))
        reqs = body.get("requests") or []
        results = []
        for r in reqs:
            if r.get("type") == "close":
                results.append({"type": "ok", "response": {"type": "close"}})
                continue
            stmt = r.get("stmt") or {}
            sql = (stmt.get("sql") or "").strip()
            args = stmt.get("args") or []
            MockLibsql.received_sqls.append((sql, args))
            # 场景 A：HTTP 400 + 错误体
            if "HTTPERR" in sql:
                payload = json.dumps(
                    {"error": {"message": "UNIQUE constraint failed: users.username"}}
                ).encode("utf-8")
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            # 场景 B：pipeline 内 error（HTTP 200）
            if "DUP_INSERT" in sql or (args and args[0].get("value") == "dup"):
                results.append({
                    "type": "error",
                    "response": {"type": "execute",
                                 "error": {"message": "UNIQUE constraint failed: users.username"}},
                })
                continue
            # 场景 C：pipeline 内通用错误（HTTP 200，非唯一约束）
            if "GENERALERR" in sql:
                results.append({
                    "type": "error",
                    "response": {"type": "execute",
                                 "error": {"message": "near \"THIS\": syntax error"}},
                })
                continue
            if sql.upper().startswith("SELECT") and "multi" in sql:
                results.append({
                    "type": "ok",
                    "response": {"type": "execute", "result": {
                        "cols": [{"name": "id", "decltype": "INTEGER"},
                                 {"name": "name", "decltype": "TEXT"},
                                 {"name": "note", "decltype": "TEXT"}],
                        "rows": [
                            [{"type": "integer", "value": "1"},
                             {"type": "text", "value": "alice"},
                             {"type": "null", "value": None}],
                            [{"type": "integer", "value": "2"},
                             {"type": "text", "value": "bob"},
                             {"type": "text", "value": "hi"}],
                        ],
                        "affected_row_count": 0,
                        "last_insert_rowid": None,
                    }},
                })
                continue
            if sql.upper().startswith("SELECT"):
                results.append({
                    "type": "ok",
                    "response": {"type": "execute", "result": {
                        "cols": [{"name": "c", "decltype": "TEXT"}],
                        "rows": [],
                        "affected_row_count": 0,
                        "last_insert_rowid": None,
                    }},
                })
                continue
            # INSERT / CREATE 等写操作
            results.append({
                "type": "ok",
                "response": {"type": "execute", "result": {
                    "cols": [],
                    "rows": [],
                    "affected_row_count": 1,
                    "last_insert_rowid": None,
                }},
            })
        payload = json.dumps({"baton": None, "base_url": None,
                              "results": results}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), MockLibsql)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    db = TursoDB("http://127.0.0.1:%d" % port, "test-token", timeout=10)

    print("[1] executescript 拆分 DDL")
    schema = """
        CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS saves (username TEXT PRIMARY KEY);
        CREATE INDEX IF NOT EXISTS idx_x ON saves(username);
    """
    db.executescript(schema)
    got = [s for s, _ in MockLibsql.received_sqls]
    check("3 条语句逐条发出", len(got) == 3, "got=%r" % got)
    check("语句无尾部残留分号", all(not s.endswith(";") for s in got), "got=%r" % got)

    print("[2] 参数绑定 + 行解析 + 类型转换")
    MockLibsql.received_sqls.clear()
    cur = db.execute("SELECT * FROM multi_row WHERE id = ? AND name = ?", (7, "洋洋"))
    check("参数被类型化传输", len(MockLibsql.received_sqls) == 1, "no request")
    sent = MockLibsql.received_sqls[0][1]
    check("int 绑定为 integer 值", sent[0] == {"type": "integer", "value": "7"},
          "sent=%r" % sent)
    check("str 绑定为 text 值", sent[1] == {"type": "text", "value": "洋洋"},
          "sent=%r" % sent)
    rows = cur.fetchall()
    check("fetchall 两行", len(rows) == 2, "n=%d" % len(rows))
    check("int 列为 int", rows[0]["id"] == 1 and isinstance(rows[0]["id"], int))
    check("text 列正确", rows[1]["name"] == "bob")
    check("null 列为 None", rows[0]["note"] is None)
    check("行支持 row[\"col\"]", rows[0]["name"] == "alice")

    print("[3] fetchone 游标语义")
    cur2 = db.execute("SELECT * FROM multi_row")
    first = cur2.fetchone()
    assert first is not None
    one = first
    check("第一次 fetchone", one["id"] == 1)
    second = cur2.fetchone()
    assert second is not None
    check("第二次 fetchone", second["id"] == 2)
    check("第三次 fetchone 为 None", cur2.fetchone() is None)

    print("[4] PRAGMA 本地跳过（不发请求）")
    MockLibsql.received_sqls.clear()
    cur3 = db.execute("PRAGMA journal_mode=WAL;")
    check("PRAGMA 返回空游标", cur3.fetchall() == [], "rows=%r" % cur3.fetchall())
    check("PRAGMA 未发 HTTP", len(MockLibsql.received_sqls) == 0, "sent=%r" % MockLibsql.received_sqls)

    print("[5] 唯一约束冲突映射 IntegrityError（pipeline error 路径）")
    try:
        db.execute("INSERT INTO users(username) VALUES(?)", ("dup",))
        check("dup 应抛异常", False, "未抛异常")
    except sqlite3.IntegrityError:
        check("dup 抛 sqlite3.IntegrityError", True)
    except Exception as e:
        check("dup 抛 sqlite3.IntegrityError", False, "实际 %r" % e)

    print("[6] HTTP 400 错误体 -> 错误映射")
    try:
        db.execute("SELECT * FROM t WHERE 1 = ?", (1,))  # 正常
        db.execute("HTTPERR SELECT")
        check("HTTPERR 应抛异常", False, "未抛异常")
    except sqlite3.IntegrityError:
        check("HTTPERR 抛 sqlite3.IntegrityError", True)
    except Exception as e:
        check("HTTPERR 抛 sqlite3.IntegrityError", False, "实际 %r" % e)

    print("[7] 通用错误 -> sqlite3.Error")
    try:
        db.execute("GENERALERR THIS IS NOT SQL 语句")
        check("非 SQL 应抛 sqlite3.Error", False, "未抛异常")
    except sqlite3.Error:
        check("非 SQL 抛 sqlite3.Error", True)
    except Exception as e:
        check("非 SQL 抛 sqlite3.Error", False, "实际 %r" % e)

    server.shutdown()
    print()
    if FAILED:
        print("契约测试失败项: %s" % ", ".join(FAILED))
        sys.exit(1)
    print("全部契约测试通过 PASS")
    sys.exit(0)


if __name__ == "__main__":
    main()
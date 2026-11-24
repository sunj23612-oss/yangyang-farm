#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
libsql_db.py - 纯标准库的 Turso/libSQL SQL-over-HTTP 适配层
=============================================================
给 server.py 提供与 sqlite3.Connection 兼容的最小接口
（execute / executescript / commit / close），底层走 Turso 云库
的 POST /v2/pipeline（HTTP + Bearer Token），零第三方依赖。

启用方式：设置环境变量 TURSO_URL（形如 https://xxx-org.turso.io）
与 TURSO_TOKEN 后，server.py 自动切到云端 SQLite；未设置时 server.py
仍使用本地 farm.db（sqlite3），保持原行为不变。

支持的错误映射：
  - 唯一性约束冲突 -> sqlite3.IntegrityError（供业务层捕获判重）
  - 其余数据库错误 -> sqlite3.Error
"""

import base64
import json
import sqlite3
import urllib.error
import urllib.request


# ------------------------------------------------------------------ 值转换
def _py_to_libsql(v):
    """Python 值 -> libsql HTTP 的类型化 cell。"""
    if v is None:
        return {"type": "null", "value": None}
    if isinstance(v, bool):
        return {"type": "integer", "value": str(int(v))}
    if isinstance(v, int):
        return {"type": "integer", "value": str(v)}
    if isinstance(v, float):
        return {"type": "float", "value": str(v)}
    if isinstance(v, bytes):
        return {"type": "blob", "base64": base64.b64encode(v).decode("ascii")}
    return {"type": "text", "value": str(v)}


def _libsql_to_py(cell):
    """libsql HTTP 的类型化 cell -> Python 值。"""
    if not isinstance(cell, dict):
        return cell
    t = cell.get("type")
    if t == "null" or cell.get("value") is None:
        return None
    if t == "integer":
        return int(cell["value"])
    if t == "float":
        return float(cell["value"])
    if t == "blob":
        return base64.b64decode(cell.get("base64") or "")
    return cell.get("value")  # text 及其他


# ------------------------------------------------------------------ 行与游标
class _Row(dict):
    """dict 子类，支持 row["col"]（sqlite3.Row 的用法子集）。"""


class _Cursor(object):
    """模拟 sqlite3.Cursor 的 fetchone/fetchall。"""

    def __init__(self, rows=()):
        self._rows = list(rows)
        self._i = 0
        self.affected_row_count = 0
        self.last_insert_rowid = None


    def fetchone(self):
        if self._i >= len(self._rows):
            return None
        r = self._rows[self._i]
        self._i += 1
        return r


    def fetchall(self):
        out = self._rows[self._i:]
        self._i = len(self._rows)
        return out


# ------------------------------------------------------------------ 适配层
class TursoDB(object):
    """对 Turso / libsql 的 SQL-over-HTTP（POST /v2/pipeline）封装。

    只实现 server.py 用到的 sqlite3 接口子集：
      execute(sql[, params]) -> _Cursor（行是 dict，支持 row["col"]）
      executescript(script)  -> 拆分多条语句逐条执行（幂等）
      commit() / close()     -> 空操作（HTTP 端每次 execute 自动提交）
    """

    def __init__(self, url, token, timeout=30):
        self._url = url.rstrip("/") + "/v2/pipeline"
        self._token = token
        self._timeout = timeout
        self.row_factory = None  # 兼容 sqlite3.Connection 的属性访问


    # ---------------- HTTP ----------------
    def _post(self, requests):
        payload = json.dumps({"requests": requests}).encode("utf-8")
        req = urllib.request.Request(self._url, data=payload, method="POST")
        req.add_header("Authorization", "Bearer " + self._token)
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise self._err_from_http(e)


    def _err_from_http(self, e):
        msg = ""
        try:
            doc = json.loads(e.read().decode("utf-8", "replace"))
        except Exception:
            doc = None
        if isinstance(doc, dict):
            err = doc.get("error")
            if isinstance(err, dict):
                msg = err.get("message") or ""
            elif isinstance(err, str):
                msg = err
        return self._map_error(e.code, msg)


    def _parse_results(self, doc):
        """解析 v2/pipeline 响应，返回首个 execute 结果的 _Cursor。"""
        results = doc.get("results") or []
        for r in results:
            if r.get("type") == "error":
                resp = r.get("response") or {}
                err = resp.get("error") or {}
                msg = err.get("message") if isinstance(err, dict) else str(resp)
                raise self._map_error(400, msg)
            if r.get("type") != "ok":
                continue
            resp = r.get("response") or {}
            if resp.get("type") != "execute":
                continue
            result = resp.get("result") or {}
            cols = [str(c.get("name")) for c in (result.get("cols") or [])]
            rows = []
            for raw in result.get("rows") or []:
                row = _Row()
                for i, cell in enumerate(raw):
                    row[cols[i] if i < len(cols) else str(i)] = _libsql_to_py(cell)
                rows.append(row)
            cur = _Cursor(rows)
            cur.affected_row_count = result.get("affected_row_count") or 0
            cur.last_insert_rowid = result.get("last_insert_rowid")
            return cur
        raise sqlite3.Error("libsql: 无有效响应: %r" % (doc,))


    @staticmethod
    def _map_error(code, msg):
        m = (msg or "").lower()
        if any(k in m for k in ("unique constraint", "constraint failed",
                                "primary key", "is not unique")):
            return sqlite3.IntegrityError(msg or "UNIQUE constraint failed")
        return sqlite3.Error("libsql error [%s]: %s" % (code, msg or ""))


    # ---------------- sqlite3.Connection 兼容接口 ----------------
    def execute(self, sql, params=None):
        sql = (sql or "").strip()
        if not sql:
            return _Cursor()
        if sql.upper().startswith("PRAGMA"):
            # 云端跳过本地连接级 PRAGMA（如 journal_mode=WAL），返回空结果
            return _Cursor()
        stmt = {"sql": sql}
        if params:
            stmt["args"] = [_py_to_libsql(v) for v in params]
        doc = self._post([{"type": "execute", "stmt": stmt}, {"type": "close"}])
        return self._parse_results(doc)


    def executescript(self, script):
        for stmt in _split_statements(script):
            if stmt:
                self.execute(stmt)


    def commit(self):
        pass


    def close(self):
        pass


def _split_statements(script):
    """按顶层分号切分 SQL 脚本。

    本项目 schema 为纯 DDL、无引号内分号，逐行合并 + 行尾分号切片足够；
    空语句被剔除，语句尾部残留分号会去掉。
    """
    out = []
    cur = []
    for line in (script or "").splitlines():
        line = line.strip()
        if not line:
            continue
        cur.append(line)
        if line.endswith(";"):
            out.append(" ".join(cur))
            cur = []
    if cur:
        out.append(" ".join(cur))
    stmts = []
    for s in out:
        s = s.strip()
        if not s:
            continue
        if s.endswith(";"):
            s = s[:-1]
        if s:
            stmts.append(s)
    return stmts
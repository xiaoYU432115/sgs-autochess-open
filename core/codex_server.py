# -*- coding: utf-8 -*-
"""v4.105★ 本地『阵容手册』(codex)服务 —— 把助手自己的数据用一个网页端出来。

为什么自己开, 而不是跳上游那个 Django
====================================
上游 TavernChessCodex 是个 Django 原型(要装 Django、跑 manage.py, 还得有它自己的
assets/card 图片目录)。我们本地 data/ 里其实更全:
  · tavern_chess_s<季>.json : 本季卡池(默认 S11), 带势力/星级/攻防/费用/技能说明
  · lineups_s<季>.json      : 本季四势力组合的后期转阵思路(按赛季出表, 缺则回退 S10)
  · tavern_odds.json      : 各级营帐的刷新概率与找卡期望次数
所以直接用标准库 http.server 端出 web/codex.html, 点一下按钮就在浏览器里打开 ——
不用装任何东西, 也不联网(纯 localhost)。

只做三件事: 发静态页、回三个 json 接口、随手能关。
"""
import json
import os
import socket
import threading
import webbrowser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_DIR = os.path.join(ROOT, "web")
DATA_DIR = os.path.join(ROOT, "data")

PORT = 8733                     # 本地手册端口(与助手自己的通信端口不在一个段上)

_MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png", ".jpg": "image/jpeg", ".svg": "image/svg+xml",
}


def _read_json(name):
    try:
        with open(os.path.join(DATA_DIR, name), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print("[阵容手册] 读 %s 失败: %s" % (name, e))
        return None


def _current_season():
    try:
        with open(os.path.join(DATA_DIR, "season", "season.json"), "r", encoding="utf-8") as f:
            return str((json.load(f) or {}).get("season") or "").strip().lstrip("sS") or ""
    except Exception:
        return ""


def _season_pool():
    """本季卡池(赛季感知), 回退到 s10。返回 (dict, source_name)。"""
    season = _current_season()
    want = "tavern_chess_s%s.json" % season if season else ""
    for nm in ([want] if want else []) + ["tavern_chess_s10.json"]:
        d = _read_json(nm)
        if isinstance(d, dict) and d:
            return d, nm
    return {}, "tavern_chess_s10.json(empty)"


# v4.107★ 卡图: 官方图片地址(探测) + 本地缓存 —— 手册页"有图看"就靠它。
#   模块起不来也不能连累手册, 那只是"没图", 查卡/摆盘/算账照常。
try:
    from core import card_image as CI
except Exception as e:
    CI = None
    print("[阵容手册] 卡图模块没起来(不影响其它功能): %s" % e)

# 批量缓存卡图的进度(后台线程写, 页面轮询读)
_CACHE_PROG = {"running": False, "done": 0, "total": 0, "ok": 0, "last": ""}


def _cards_payload():
    """卡池: 只要页面用得上的那几列, 顺便按势力/星级排好序。

    v4.160★ 赛季感知: 吃本季卡池(tavern_chess_s<季>.json), 没有才回退 s10。
    顺手把 pool_source 带上, 页面能直接标"这是哪季的数据"(回退时变黄提示)。
    """
    raw, src = _season_pool()
    out = []
    for cid, v in raw.items():
        if not isinstance(v, dict):
            continue
        out.append({
            "id": str(cid),
            "name": v.get("name") or v.get("n") or "",
            "faction": v.get("faction") or "",
            "rank": v.get("rank") or 0,
            "atk": v.get("atk") or 0,
            "def": v.get("def") or 0,
            "cost": v.get("cost") or 0,
            "effect": v.get("effect") or "",
            "season_source": src,
            # v4.107★ 卡图: 本地缓存优先(离线也能看), 其次探到过的官方直链, 都没有就空
            #   (这里【不】现探 —— 打开手册不能被 372 次 HEAD 拖成十几秒)
            "img": (CI.img_field(str(cid), v.get("name") or v.get("n") or "") if CI else ""),
        })
    out.sort(key=lambda c: (str(c["faction"]), -(c["rank"] or 0), c["name"]))
    return out


class _Handler(object):
    """真正的 handler 在 start() 里现造(要带上 WEB_DIR), 这里只是占位说明。"""


def _make_handler():
    from http.server import BaseHTTPRequestHandler

    class CodexHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):      # 别把每条请求都刷到控制台
            pass

        def _send(self, code, body, mime):
            self.send_response(code)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except Exception as e:
                print("[阵容手册] 写响应失败(浏览器多半已经关了): %s" % e)

        def _q(self, key):
            """取 URL 上的 ?a=1&b=2 参数(BaseHTTPRequestHandler 没自带)。"""
            import urllib.parse
            qs = self.path.split("?", 1)[1] if "?" in self.path else ""
            for kv in qs.split("&"):
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    if k == key:
                        try:
                            return urllib.parse.unquote_plus(v)
                        except Exception:
                            return v
            return ""

        def _send_json(self, obj):
            self._send(200, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/api/cards":
                self._send_json(_cards_payload())
                return
            if path == "/api/lineups":
                self._send_json(_read_json("s10_lineups.json") or {})
                return
            if path == "/api/odds":
                self._send_json(_read_json("tavern_odds.json") or {})
                return
            # ---- v4.107★ 卡图 ----
            if path == "/api/card_img":
                # 单张按需探测(摆盘里那 7 张用 —— 372 张全探太慢, 只探真要看的)
                cid = (self._q("id") or "").strip()
                if not (cid and CI):
                    self._send_json({"img": ""})
                    return
                self._send_json({"img": CI.resolve(cid, self._q("name") or "")})
                return
            if path == "/api/cache_imgs":
                # 批量把图下到本地(data/card_img) —— 下完以后断网也能看, 而且秒开
                if not CI:
                    self._send_json({"started": False, "why": "卡图模块没起来"})
                    return
                if _CACHE_PROG["running"]:
                    self._send_json({"started": False, "why": "上一次还在跑"})
                    return
                try:
                    limit = int(self._q("limit") or 0) or None
                except Exception:
                    limit = None
                cards = _cards_payload()

                def _work():
                    import threading
                    try:
                        r = CI.cache_all(cards, limit=limit, on_progress=lambda d, t, o, m: (
                            _CACHE_PROG.update({"done": d, "total": t, "ok": o, "last": m})))
                        _CACHE_PROG["last"] = "完成: %d/%d 张已存本地" % (r["ok"], r["total"])
                    except Exception as e:
                        _CACHE_PROG["last"] = "出错: %s" % e
                    finally:
                        _CACHE_PROG["running"] = False

                _CACHE_PROG.update({"running": True, "done": 0, "total": len(cards),
                                    "ok": 0, "last": "开始…"})
                import threading
                threading.Thread(target=_work, daemon=True).start()
                self._send_json({"started": True, "total": len(cards)})
                return
            if path == "/api/probe_imgs":
                # 只问地址不下图(372 次 HEAD, 不占本地空间) —— 想看图但不想下 70MB 就点这个
                if not CI:
                    self._send_json({"started": False, "why": "卡图模块没起来"})
                    return
                if _CACHE_PROG["running"]:
                    self._send_json({"started": False, "why": "上一次还在跑"})
                    return
                cards = _cards_payload()

                def _work2():
                    n = 0
                    for i, c in enumerate(cards, 1):
                        try:
                            u = CI.resolve(c.get("id"), c.get("name") or "")
                        except Exception:
                            u = ""
                        if u:
                            n += 1
                        _CACHE_PROG.update({"done": i, "total": len(cards), "ok": n,
                                            "last": "%s: %s" % (c.get("name") or c.get("id"),
                                                                u.rsplit("/", 1)[-1] if u else "没有")})
                    _CACHE_PROG["last"] = "完成: %d/%d 张探到官方图" % (n, len(cards))

                _CACHE_PROG.update({"running": True, "done": 0, "total": len(cards),
                                    "ok": 0, "last": "开始探…"})
                import threading
                threading.Thread(target=_work2, daemon=True).start()
                self._send_json({"started": True, "total": len(cards)})
                return
            if path == "/api/cache_imgs_status":
                self._send_json(dict(_CACHE_PROG))
                return
            if path.startswith("/img/"):
                # 本地缓存的图(只认纯数字卡 ID, 防目录穿越)
                # 注意去掉 .png 后缀再判数字(带点就不是数字了, 会被当成穿越拦掉)
                cid = os.path.splitext(os.path.basename(path[len("/img/"):]))[0]
                if not (cid.isdigit() and CI):
                    self._send(404, b"not found", "text/plain; charset=utf-8")
                    return
                p = CI.cached_path(cid)
                if not p:
                    self._send(404, b"not found", "text/plain; charset=utf-8")
                    return
                try:
                    with open(p, "rb") as f:
                        body = f.read()
                except Exception:
                    self._send(404, b"not found", "text/plain; charset=utf-8")
                    return
                self._send(200, body, "image/png")
                return
            if path in ("", "/"):
                path = "/codex.html"
            # 只允许 web/ 目录内的文件(防目录穿越)
            rel = path.lstrip("/")
            full = os.path.normpath(os.path.join(WEB_DIR, rel))
            if not full.startswith(os.path.normpath(WEB_DIR)) or not os.path.isfile(full):
                self._send(404, b"not found", "text/plain; charset=utf-8")
                return
            try:
                with open(full, "rb") as f:
                    body = f.read()
            except Exception:
                self._send(404, b"not found", "text/plain; charset=utf-8")
                return
            self._send(200, body, _MIME.get(os.path.splitext(full)[1].lower(),
                                            "application/octet-stream"))

    return CodexHandler


class CodexServer(object):
    """起/停本地手册服务。重复点按钮不会起第二个(端口占用就直接复用)。"""

    def __init__(self, port=PORT):
        self.port = port
        self._srv = None
        self._thread = None

    def running(self):
        if self._srv:
            return True
        s = socket.socket()
        try:
            s.connect(("127.0.0.1", self.port))
            return True
        except OSError:
            return False                      # 连不上 = 没在跑
        finally:
            try:
                s.close()
            except OSError as e:
                print("[阵容手册] 关探测 socket 失败: %s" % e)

    def url(self):
        return "http://localhost:%d/codex.html" % self.port

    def start(self, open_browser=True):
        if self._srv is not None:
            if open_browser:
                webbrowser.open(self.url())
            return self.url()
        if self.running():                       # 端口已被占(多半是上次没关) → 直接复用
            if open_browser:
                webbrowser.open(self.url())
            return self.url()
        from http.server import ThreadingHTTPServer
        try:
            self._srv = ThreadingHTTPServer(("127.0.0.1", self.port), _make_handler())
        except Exception as e:
            print("[阵容手册] 起服务失败: %s" % e)
            return None
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)
        self._thread.start()
        if open_browser:
            webbrowser.open(self.url())
        return self.url()

    def stop(self):
        try:
            if self._srv:
                self._srv.shutdown()
                self._srv.server_close()
        except Exception as e:
            print("[阵容手册] 关服务失败: %s" % e)
        self._srv = None
        self._thread = None


_SERVER = None


def get_server():
    global _SERVER
    if _SERVER is None:
        _SERVER = CodexServer()
    return _SERVER


if __name__ == "__main__":
    srv = get_server()
    print("阵容手册:", srv.start(open_browser=False))
    try:
        input("回车退出\n")
    except EOFError:                          # 没接 stdin(被 IDE / 计划任务拉起时)
        print("(没有 stdin, 直接退出)")
    srv.stop()

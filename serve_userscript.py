# -*- coding: utf-8 -*-
"""把油猴脚本挂到 http://127.0.0.1:8766/ 供 Tampermonkey 一键安装/更新。

用法:
  · 命令行单独跑:  python serve_userscript.py
  · 助手 UI 里的【⚡一键注入】: 导入本模块 → ensure() 起服务 → url(port) 打开浏览器
浏览器打开 http://127.0.0.1:8766/sgs_bridge.user.js 即弹出安装/更新页。

v4.33★ 三个坑:
  ⓪ 端口必须记住: 8766 被别的东西占住时 ensure() 会退到 8767/8768/…, 调用方一律用返回的
     port 去拼地址。服务的 httpd/端口状态就存在本模块里, UI **不要再存第二份副本** ——
     两处各存一份, 就会出现"第二次点【⚡一键注入】又拿 8766 拼地址, 打开别人的端口/死链接"。
  ① 原来响应头带了 Content-Disposition: attachment —— 浏览器会把它当【下载】,
     Tampermonkey 拦不到这次导航, 一键注入直接变成"下个 .user.js 文件"。
     去掉它, 让 TM 的 userscript 拦截生效(这正是"打开链接就弹安装"的关键)。
  ② 端口被占时原来直接 sys.exit(0) 且不区分"是不是我们的服务在跑":
     现在给出 probe(), UI 可以判断"已经在跑就别重复起, 起不来就换端口"。
"""
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8766
DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bridge")
SCRIPT = "sgs_bridge.user.js"
COMPANION = "sgs_bridge_xhr.user.js"   # v4.153★ PNA 绕过的传输层配套, 必须和主脚本一起装

INDEX_HTML = """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<title>自走棋助手 · 脚本安装</title>
<body style="background:#2a2a2a;color:#eee;font:14px/1.7 'Microsoft YaHei UI',sans-serif;padding:28px">
<h2 style="color:#e0b060">三国杀自走棋助手 · 注入页面脚本</h2>
<ol>
  <li>确认装了 <b>Tampermonkey(篡改猴)</b> 扩展</li>
  <li style="margin:6px 0">分别点下面<b>两个</b>链接 → Tampermonkey 会各弹一次安装/更新页</li>
  <li><b>两个都要装</b>(主脚本 + 传输层配套), 装完回游戏页按 <b>F5 刷新</b></li>
</ol>
<p><a style="display:inline-block;background:#0055aa;color:#fff;padding:10px 18px;border-radius:6px;text-decoration:none"
      href="/{script}">① 安装 / 更新 {script}</a></p>
<p><a style="display:inline-block;background:#0077aa;color:#fff;padding:10px 18px;border-radius:6px;text-decoration:none"
      href="/{script_xhr}">② 安装 / 更新 {script_xhr}（PNA 绕过的传输层，必装）</a></p>
<p style="color:#999">主脚本装好只会显示版本; <b>配套脚本也装好</b>才会真正连上本机助手(否则 Edge/QQ/360 会被 Private Network Access 拦)。</p>
</body></html>"""


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception as e:
            # 浏览器中途断开(用户取消/刷新)很常见, 不影响功能; 但不静默, 留一行便于排查
            sys.stderr.write("[serve_userscript] 写响应失败: %s\n" % e)

    def do_HEAD(self):
        self.do_GET(head=True)

    def do_GET(self, head=False):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            self._send(200, INDEX_HTML.replace("{script}", SCRIPT).replace("{script_xhr}", COMPANION),
                      "text/html; charset=utf-8")
            return
        name = os.path.basename(path)
        p = os.path.join(DIR, name)
        if not name or not os.path.isfile(p):
            self._send(404, "not found", "text/plain; charset=utf-8")
            return
        with open(p, "rb") as f:
            body = f.read()
        if head:
            self._send(200, b"", "text/javascript; charset=utf-8")
            return
        # 关键: 不要 Content-Disposition: attachment (那会变成下载, TM 拦不到)
        self._send(200, body, "text/javascript; charset=utf-8")


def make_server(port=PORT):
    return ThreadingHTTPServer(("127.0.0.1", port), H)


def serve_in_thread(port=PORT):
    """后台线程起服务, 返回 httpd(daemon 线程, 助手关掉就一起结束)。"""
    httpd = make_server(port)

    def _serve():
        try:
            httpd.serve_forever()
        except OSError as e:
            # 收尾时 server_close() 会先把 socket 关掉, 此时 select() 抛 WinError 10038 —— 正常关闭
            #   路径, 不值得在控制台打一坨 traceback(实测会打); 其它 OSError 仍然报出来。
            if getattr(e, "winerror", None) != 10038:
                sys.stderr.write("[serve_userscript] 服务线程退出: %s\n" % e)
        except Exception as e:
            sys.stderr.write("[serve_userscript] 服务线程异常: %s\n" % e)

    t = threading.Thread(target=_serve, name="userscript-http", daemon=True)
    t.start()
    httpd._sgs_thread = t   # 留句柄: shutdown() 要先确认线程还活着, 否则会死等
    return httpd


def probe(port=PORT, timeout=1.0):
    """这个端口上是不是我们的脚本服务(能取到 ==UserScript== 就是)。"""
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:%d/%s" % (port, SCRIPT), timeout=timeout) as r:
            head = r.read(200).decode("utf-8", "replace")
        return "==UserScript==" in head
    except Exception:
        return False


def url(port=PORT):
    return "http://127.0.0.1:%d/%s" % (port, SCRIPT)


def url_for(name, port=PORT):
    """同机脚本服务上任意脚本文件的安装地址(用于配套传输层等)。"""
    return "http://127.0.0.1:%d/%s" % (port, name)


def companion_url(port=PORT):
    """v4.153★ PNA 绕过的传输层配套脚本安装地址。"""
    return url_for(COMPANION, port)


# ---------------------------------------------------------------- 会话内单例
# v4.33★ 【⚡一键注入】的状态归本模块管, UI 只调 ensure()/shutdown():
#   · 端口由本模块决定(可能不是 8766), 调用方一律用返回值拼地址;
#   · 重复点击/重复起服务都被这里挡住, 不会越开越多。
_httpd = None
_port = None
_lock = threading.Lock()


def ensure(ports=None):
    """确保脚本服务在跑 → (port, notes); 全起不来 → (None, notes)。

    ① 本进程已起过 → 用记住的端口(先探活, 防止 httpd 对象还在、服务线程已挂)
    ② 否则逐个候选端口: 先探一次 —— 已有服务在跑就直接复用(上一个助手窗口没关的情况),
       再尝试自己起; 端口被别的程序占了就换下一个
    ③ 都起不来就把每个端口失败的原因写进 notes, 不静默
    """
    global _httpd, _port
    ports = ports or (PORT, PORT + 1, PORT + 2, PORT + 4, PORT + 24)
    notes = []
    with _lock:
        if _httpd is not None and _port:
            if probe(_port):
                return _port, notes
            try:
                _httpd.server_close()
            except Exception as e:
                sys.stderr.write("[serve_userscript] 关闭失效服务失败: %s\n" % e)
            notes.append("端口 %d 的服务已失效, 重新启动" % _port)
            _httpd = _port = None
        for p in ports:
            if probe(p):
                _port = p
                notes.append("脚本服务已在运行(端口 %d)" % p)
                return p, notes
            try:
                _httpd = serve_in_thread(p)
            except OSError as e:
                notes.append("端口 %d 起不来(%s)" % (p, e))
                continue
            _port = p
            notes.append("已启动脚本服务(端口 %d)" % p)
            return p, notes
        return None, notes


def shutdown():
    """关掉本进程起的脚本服务并立刻释放端口(助手退出时调); 没起过就是空操作。

    注意顺序与存活判断: BaseServer.shutdown() 会等 serve_forever 退出, 如果那个线程已经
    crash/退出了, 这个 wait 会**永久阻塞**(助手会卡在退出上) —— 所以先看线程还在不在。
    """
    global _httpd, _port
    with _lock:
        h, _httpd, _port = _httpd, None, None
    if h is None:
        return
    t = getattr(h, "_sgs_thread", None)
    if t is not None and t.is_alive():
        try:
            h.shutdown()
        except Exception as e:
            sys.stderr.write("[serve_userscript] shutdown 失败: %s\n" % e)
    try:
        h.server_close()
    except Exception as e:
        sys.stderr.write("[serve_userscript] server_close 失败: %s\n" % e)


if __name__ == "__main__":
    p = int(sys.argv[1]) if len(sys.argv) > 1 else PORT
    try:
        httpd = make_server(p)
    except OSError:
        if probe(p):
            print("[提示] 端口%d 上已经有脚本服务在跑, 直接用: %s" % (p, url(p)))
            sys.exit(0)
        print("[错误] 端口%d 被别的程序占用了, 换一个: python serve_userscript.py 8767" % p)
        sys.exit(1)
    print("[OK] 油猴脚本安装服务: %s" % url(p))
    print("浏览器打开上面链接 -> Tampermonkey 自动弹出 安装/更新 页面 -> 点安装")
    httpd.serve_forever()

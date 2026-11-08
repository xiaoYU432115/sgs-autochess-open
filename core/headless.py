# -*- coding: utf-8 -*-
"""v4.160★ 无界面模式(--no-ui): 只跑桥接服务, 不弹 Tk 窗口。

为什么需要: 5 开时每个实例都弹一个 1400x850 的 Tk 窗口 + 各自一套后台线程
(日志写盘 / 协议泵 / LIVE 解析), 内存和 CPU 都扛不住, 桌面上堆 5 个窗口也
根本分不清谁是谁。实际用法是"要操作的那一两个开界面, 其余后台跑桥接"。

后台实例【有】的能力(与带界面完全一致):
  · /push 收状态、/cmd 下发指令 —— 游戏页脚本自己执行买卡/上阵/刷新等动作
  · /ext/bridge.meta + /ext/bridge.main.js —— 全能扩展拉脚本走的就是这两条
  · /rules、/img/、/dyn/ —— 资源替换(换肤/换背景)
  · /install.html —— 浏览器装脚本并把端口写进 localStorage
  · 协议流水 / 逐局记录(matchlog) 落盘 —— 各实例写各自的目录, 不会混
后台实例【没有】的: 界面按钮、剧本、全局热键、OCR 截图 —— 这些依赖 Tk 窗口
与窗口句柄, 需要界面时把 --no-ui 去掉重启即可(数据目录不变, 配置全在)。
"""
import os
import sys
import time


def run(port, log=None):
    """起桥接服务并阻塞。返回进程退出码(0=正常退出, 2=端口没绑上)。"""
    log = log or (lambda m: None)
    from core.bridge import BridgeServer, STATE, restart_requested, consume_restart_flag

    srv = BridgeServer(port)
    srv.start()
    if getattr(srv, "bind_error", None):
        log(srv.bind_error)
        log("→ 本实例没有桥接服务, 退出。请换一个 --port。")
        return 2

    log("")
    log("  桥接服务已启动:  http://127.0.0.1:%d" % port)
    log("  安装脚本页:      http://127.0.0.1:%d/install.html" % port)
    log("  状态:            http://127.0.0.1:%d/status" % port)
    log("  (Ctrl-C 退出)")
    log("")

    try:
        while True:
            time.sleep(1.0)
            # /restart 与 /reconfigure(改端口): 标记文件是本实例自己的
            if restart_requested():
                consume_restart_flag()
                log("[重启] 收到重启请求, 正在重启本实例…")
                try:
                    srv.stop()
                except Exception:
                    pass
                try:
                    STATE.flush_net()
                except Exception:
                    pass
                try:
                    os.execv(sys.executable, [sys.executable] + sys.argv)
                except Exception as e:
                    log("[重启] 重启失败: %s" % e)
    except KeyboardInterrupt:
        log("\n[退出] 正在停止桥接服务…")
        try:
            STATE.flush_net()
        except Exception:
            pass
        try:
            srv.stop()
        except Exception:
            pass
        return 0

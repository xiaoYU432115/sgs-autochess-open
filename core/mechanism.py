# -*- coding: utf-8 -*-
"""机制(v4.89★) —— 助手端这一半: 陆逊小抄 + 3选1。

★ 陆逊小抄会【真的把锦囊用出去】: ReqChessUseSpell 就是游戏自己用锦囊那条指令, 服务器
  真扣牌, 没有"试一下不消耗"的入口。它当年能循环, 是因为陆逊技能把用掉的手牌补回来;
  官方修掉之后就只剩"真消耗、不回补"(见 ui/mechanism_tab.py 的启动确认与说明)。

★ 3选1(v4.89★)见下面的 Select3Mech。

原理(游戏页 bridge/sgs_bridge.user.js 的 SgsLuXun 里写了完整依据, 这里只说结论):
  每一轮做三件事 ——
    ① 使用:  manager.ReqChessUseSpell(手牌第 N 张的 goodsID, [])   ← 与游戏自己用锦囊同源
    ② 同步:  manager.ReqChessFlushInfo()                            ← 让服务器把手牌重发一遍
    ③ 刷新:  refreshHandCardsOnly() + UI_UPDATE_HAND_CARD 事件      ← 只重画手牌区
  重复触发陆逊技能的收益就来自"用出去 → 手牌又回来 → 再用"这个循环。

★ 关于"同步": 上游 refs/AutoUseSpell.js 里这一步的兜底写的是 ReqShopRefreshChess,
  那是【刷商店】, 会真扣虎符 —— 本实现没有照抄, 拿不到 ReqChessFlushInfo 就跳过同步,
  绝不拿刷商店凑(见脚本里 SgsLuXun._sync 的说明)。

★ 关于"能跑": 上游那份的 startLuXun / stopLuXun / toggleLuXun 三个函数【只有调用、
  没有定义】(全文搜不到 function startLuXun), 按钮点下去就是 ReferenceError, 所以
  陆逊小抄那条路从来没真跑通过。本实现是重新写进桥接脚本的, 不是转发调用。
"""

import json


class _MechBase(object):
    """助手端 → 游戏页 下发指令的公共部分。"""

    def __init__(self, ops, log=None):
        self.ops = ops
        self.log = log or (lambda m, level="INFO": None)

    def _cmd(self, action, **kw):
        try:
            res = self.ops.send_recv(action, kw, timeout=6.0)
        except Exception as e:
            return {"ok": False, "reason": "下发指令异常: %s" % e}
        if not res:
            return {"ok": False, "reason": "游戏页没有回应(没进对局 / 脚本没更新到 v4.90?)"}
        try:
            data = json.loads(res)
        except Exception:
            return {"ok": False, "reason": "返回不是JSON: %s" % str(res)[:120]}
        return data if isinstance(data, dict) else {"ok": False, "reason": "返回格式不对"}


class LuXunMech(_MechBase):
    """陆逊小抄的助手端入口。"""

    def start(self, index=0, gap_ms=300, max_times=200, max_idle=30,
              max_same=6, stop_same=True):
        """启动循环。

        index     : 用手牌第几张(0 = 第一张, 也就是最左边那张)
        gap_ms    : 每轮之间隔多少毫秒(太快服务器会漏, 太慢效率低; 300 是实测可用的值)
        max_times : 最多跑多少轮(保险, 防止真跑成死循环)
        max_idle  : 连着这么多轮"游戏不可操作/发送失败"就自动停
        max_same  : 连着这么多轮【手牌一模一样】就停 —— 服务器静默忽略这条指令的信号。
                    陆逊漏洞被官方修掉之后就是这个表现: 一直发、计数器一直涨、游戏里
                    什么都没发生。没有这个判据用户只能干看着空转。(v4.88★)
        stop_same : False 关掉上面这个判据(万一以后真有"用完又原样回来"的漏洞, 会被它拦下)
        """
        return self._cmd("luxun_start", index=int(index), gapMs=int(gap_ms),
                         maxTimes=int(max_times), maxIdle=int(max_idle),
                         maxSame=int(max_same), stopOnSame=1 if stop_same else 0)

    def stop(self, reason="助手端手动停止"):
        return self._cmd("luxun_stop", reason=str(reason))

    def state(self):
        return self._cmd("luxun_state")


class Select3Mech(_MechBase):
    """3选1(挑武将 / 挑锦囊 / 挑对手武将那个窗)的助手端入口。

    与游戏页 bridge/sgs_bridge.user.js 的 SgsSelect3 对应(v4.89★)。三条路:

      state()  —— 看这一刻服务器有没有在等你挑(phase / curIdx / WaitSelectCards 里的候选)
      auto()   —— 只挑【游戏这一档窗口里本来就有的第一张】, 走 manager 自己的
                  ReqChessSkillSelectSpellOrChess / ReqSelectOtherChess / ReqChessSelectSpellID,
                  与手动点一张完全同源 —— 服务器认。
      force()  —— 先试窗口的确认按钮; 拿不到才走兜底 WaitSelectCards=null + UI_UPDATE_SELECT_CARD。
                  ⚠ 那个兜底是【纯本地关窗】, 服务器一个包都没收到 —— 窗口没了但那一档
                    还在服务器上等你。返回结果里 local=True 就说明走的是这条兜底。

    为什么不用上游那份 refs/EasySelect3.js: 它是【控制台粘贴型】脚本, get3State 等四个全局
    函数是最后一行挂上去的, 不整段粘进控制台跑一次就根本没有(直接敲必然 ReferenceError)。
    """

    def state(self):
        return self._cmd("select3_state")

    def auto(self, times=1):
        """自动选。

        times: 连挑几档 —— v4.95★ 有些技能(实测诸葛亮就是)一次要连挑【好几档】:
               取证 24:00 那份是 档号 0→1→2 = 武将 → 锦囊 → 锦囊, 每挑完一档服务器立刻
               挂下一档。传 1 = 只挑当前这一档(老行为); 传 6 = 连选到底(挑到没得挑为止)。
        """
        return self._cmd("select3_auto", times=int(times))

    def force(self):
        return self._cmd("select3_force")


class TraceMech(_MechBase):
    """「招募 → 战斗」这个过程到底发生了什么 —— 取证录制器(只读)。

    与游戏页 bridge/sgs_bridge.user.js 的 SgsTrace 对应(v4.89★)。录三路并打上相对秒数:
      ① OUT  发出去的包(hook mgr.proxy.SendProto)
      ② IN   服务器推过来的包(hook Laya 事件总线) + errCode
      ③ PHASE 阶段/服务器在等的档号/待选数/可操作 —— 变了才记一行

    ★ 只读: 一个包都不多发, 也不改任何时序。它只是"把发生的事抄下来", 用来回答
      「不选的话, 进战斗那一瞬间到底是服务器替我选了, 还是客户端自己关了窗」。
    """

    def start(self, max_ms=180000, tag="招募 → 战斗"):
        return self._cmd("trace_start", maxMs=int(max_ms), tag=str(tag))

    def stat(self, timeout=6.0):
        """v4.101★ 只问"还在录吗 / 多少行", 不带内容 —— 给界面轮询用。

        以前轮询也走 dump(200): 每 2 秒把 200 行 JSON 从游戏页搬到助手再解析一遍, 只为
        看一眼行数。录制时这是双端最重的一条链(页面序列化 + 传输 + 助手解析 + 重画 Text),
        而它每 2 秒跑一次。现在轮询只问这个(几十字节), 完整内容留到「取回/停止」时再搬。
        """
        return self._cmd("trace_stat")

    def dump(self, n=200, timeout=20.0):
        """取回录制内容。

        n 默认只给最后 200 行(够 UI 显示), 但【存盘要传大 n 拿全段】—— 以前固定 200
        导致存盘文件只有尾巴, 招募/挑选那一段永远看不到(v4.90★)。
        整段可能上千行(JSON 上 MB), 所以这里单独放宽超时(基类默认 6 秒不够传)。
        """
        try:
            res = self.ops.send_recv("trace_dump", {"n": int(n)}, timeout=float(timeout))
        except Exception as e:
            return {"ok": False, "reason": "下发指令异常: %s" % e}
        if not res:
            return {"ok": False, "reason": "游戏页没有回应(没进对局 / 脚本没更新到 v4.90?)"}
        try:
            data = json.loads(res)
        except Exception:
            return {"ok": False, "reason": "返回不是JSON: %s" % str(res)[:120]}
        return data if isinstance(data, dict) else {"ok": False, "reason": "返回格式不对"}

    def stop(self, reason="助手端停止", timeout=20.0):
        """停止并【整段】交回(脚本侧 stop 现在返回全部行, 不再是最后 200 行)。"""
        try:
            res = self.ops.send_recv("trace_stop", {"reason": str(reason)}, timeout=float(timeout))
        except Exception as e:
            return {"ok": False, "reason": "下发指令异常: %s" % e}
        if not res:
            return {"ok": False, "reason": "游戏页没有回应(没进对局 / 脚本没更新到 v4.90?)"}
        try:
            data = json.loads(res)
        except Exception:
            return {"ok": False, "reason": "返回不是JSON: %s" % str(res)[:120]}
        return data if isinstance(data, dict) else {"ok": False, "reason": "返回格式不对"}

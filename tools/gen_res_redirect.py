# -*- coding: utf-8 -*-
"""资源替换(换立绘/头像/音效)规则生成器 —— 从赛季配置表生成"匹配串 → 本地文件"清单。

原理(用户提到的 GoRes / Redirector 那类扩展就是这个逻辑):
  游戏客户端加载资源时走的是固定 URL, 例如(都是从本机 data/diagnostics.json 里抓到的真实样本):
    棋子立绘  https://web.sanguosha.com/x/pc/res/assets/runtime/tavernChess/piece/luzhi.png?v=1d4c50b41
    武将大图  https://web.sanguosha.com/10/pc/res/assets/runtime/tavernChess/general/604701.png
    头像      https://web.sanguosha.com/10/pc/res/assets/runtime/dress/static/head/9xx.png
    音效      https://web.sanguosha.com/x/pc/res/assets/runtime/voice/tavernChess/effectSound/OpponentChessEnter.mp3?v=d8afa1631
  扩展的作用就是"看到 URL 里含某个串 → 不去下载, 改成读你给的文件"。
  所以只要知道【资源名】, 就能批量生成规则。而资源名不用猜 —— 配置表里就有:
    ChessConf.DefaultSkin        (棋子立绘名, 例: luzhi = 卢植)
    ChessGeneralConf.DefaultSkin (主公立绘名, 例: dongzhuo = 董卓)
  这就是本工具存在的意义: 换赛季/出新棋子后, 重新生成一遍规则即可, 不用手工对名单。

注意: 本工具【只生成规则和空目录】, 不会去动游戏文件、也不联网。
      图片要你自己放到 `我的图/` 里(文件名 = 资源名.png)。

用法:
  python tools/gen_res_redirect.py                # 当前赛季
  python tools/gen_res_redirect.py --season s10
生成物(都在 参考_配置表/资源替换/ 下):
  {season}_匹配规则.txt    一行一条: 匹配串 <TAB> 目标(本地绝对路径)
  {season}_匹配规则.tsv    同上, 带表头(资源名/棋子名/匹配串/目标文件) 便于用表格软件看
  我的图/                   把 <资源名>.png 丢进来(工具会把目标指到这里)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import resource_rules as RR


def main():
    season = None
    if "--season" in sys.argv:
        try:
            season = sys.argv[sys.argv.index("--season") + 1]
        except IndexError:
            print("--season 后面要跟赛季名(如 s10)")
            return 2
    if not season:
        from core import csv_loader
        for p in csv_loader._season_config_paths(season):
            if os.path.exists(p):
                season = os.path.basename(os.path.dirname(p)) or "current"
                break
    info = RR.generate_redirect_files(season)
    if not info.get("count"):
        print("读不到赛季配置表 —— 先跑 tools/season_config.py 抓一次")
        return 1
    print("已生成: " + info["txt"])
    print("已生成: " + info["tsv"])
    print("图片目录: " + RR.IMG_DIR)
    print("规则数: %d (%s)" % (info["count"], ", ".join(
        "%s %d" % (k, v) for k, v in info["kinds"].items())))
    print("样例 5 条:")
    rules = RR.build_rules(season)
    for r in rules[:5]:
        print("   %s: %s(%s) → %s" % (r["kind"], r["name"], r["pattern"], r["target"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

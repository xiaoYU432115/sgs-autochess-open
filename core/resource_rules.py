# -*- coding: utf-8 -*-
"""资源替换规则管理(v4.68★)。

背景
======
游戏客户端加载图片/音效时走的是固定 URL(如棋子立绘、主公大图、战场背景)。
浏览器扩展(GoRes / Redirector 等)看到 URL 含某个串, 就把请求重定向到本地文件。
本模块统一管理"匹配串 → 本地文件"的规则: 读取/保存用户自定义规则、从赛季配置表
批量生成棋子/主公默认规则、写出 Redirector 可导入的 txt/tsv。

数据文件
========
  data/resource_rules.json        用户自定义规则(背景/任意资源)
  参考_配置表/资源替换/          生成给扩展读的文件
    {season}_匹配规则.txt         纯规则(扩展用)
    {season}_匹配规则.tsv         带表头(给人看)
    我的图/                       用户把要替换的图片按资源名丢进来
"""
import io
import json
import os
import re
import sys
import threading
import time

from core.config import BASE_DIR, DATA_DIR
from core import csv_loader

RULES_PATH = os.path.join(DATA_DIR, "resource_rules.json")
REF_DIR = os.path.join(BASE_DIR, "参考_配置表", "资源替换")
IMG_DIR = os.path.join(REF_DIR, "我的图")

# URL 模板(与 tools/gen_res_redirect.py 保持一致)
# v4.75★ 主公立绘路径修正: 以前写的是 general/maxBig/{skin}.png(早期从抓包推测的形状,
#   从没整批验证过), 用户截图图库一片黑后才实测 —— maxBig 在 x/10 两条线路【全部 404】,
#   真身是 tavernChess/general/{skin}.png(两条线路都在, 赛季表 49 个主公 skin 49/49 命中)。
#   以前的「新/旧路径」双保险其实 = 10 线路 + x 线路(不是新旧客户端), 沿用这个结构。
PIECE_URL = "web.sanguosha.com/x/pc/res/assets/runtime/tavernChess/piece/{skin}.png"
GENERAL_URL = "web.sanguosha.com/10/pc/res/assets/runtime/tavernChess/general/{skin}.png"
OLD_GENERAL_URL = "web.sanguosha.com/x/pc/res/assets/runtime/tavernChess/general/{skin}.png"


def norm_season(season=None):
    """把赛季名收口成一种写法(s10 / S10 / 10 → "s10"), 用它当文件名。

    v4.69★ 为什么必须收口: 界面上那个「赛季」框用户填的是 "s10", 而框旁边的人会顺手
      lstrip("sS") 变成 "10" —— 于是同一个赛季生成了【两个】文件:
        s10_匹配规则.txt(工具/命令行生成) 与 10_匹配规则.txt(界面点生成)
      用户按提示去找 "新的 s10_匹配规则.txt", 却看到 `10_匹配规则.txt` 时间更新 ——
      "到底该导入哪个" 每次都要问一遍。文件名只认这一份归一化结果。
    """
    s = str(season or "").strip()
    if not s:
        return ""
    if len(s) > 1 and s[0] in "sS" and s[1:].isdigit():
        return "s" + s[1:]
    if s.isdigit():
        return "s" + s
    return s


# ============================================================================
# v4.74★ 赛季: 界面上那个框以前是"填了没反应", 这里补上"到底读的哪张表"
# ============================================================================
def season_info(season=None):
    """赛季 → {"season","dir","path","has"}: 这个赛季实际会读哪张配置表、那张表在不在。

    用户原话"左上角的赛季也没有效果啊"。原因有两层, 界面以前一层都看不出来:
      ① 赛季名有两种写法(season.json 里的 "10" 与界面归一化的 "s10"), 而 csv_loader 旧代码
         对【已经带 s 的】又拼了一个 s → `ss10`, 主路径永远不存在, 只能靠"扫 data/season
         取最大的那个赛季号"兜底 —— 于是填 s9/s11 和填 s10 的结果完全一样;
      ② 填一个 data/season 下根本没有的赛季名时静默回退, 不报错也不提示。
    现在把结论交给界面直接显示(状态栏)。
    """
    sea = norm_season(season or "")
    try:
        p = csv_loader.season_config_path(season)
    except Exception as e:
        return {"season": sea, "dir": "", "path": "", "has": False,
                "err": "%s: %s" % (type(e).__name__, e)}
    return {"season": sea or norm_season(csv_loader.CURRENT_SEASON),
            "dir": csv_loader.season_dir_name(season),
            "path": p or "",
            "has": bool(p and os.path.exists(p))}


def season_choices():
    """界面「赛季」下拉的候选: data/season 下真实存在的赛季目录(有配置表的标 ★)。"""
    try:
        return csv_loader.season_dir_choices()
    except Exception as e:
        sys.stderr.write("[res_rules] 列赛季目录失败: %s\n" % e)
        return []


# v4.69★ 规则的目标(target)不再只能是本地文件 —— 也可以是一个【网络地址】。
#
# 用户原话: "换皮肤可以直接用三国杀服务器端的皮肤啊?"
#   可以直接用。Redirector / GoRes 这类扩展的规则本质就是"URL 含 A → 换成 B", 而 B
#   本身就可以是另一个 URL。所以把 棋子立绘 那条规则的目标从"本地 png"改成
#   "官方某个皮肤图的 URL", 游戏就会去加载官方那张图 —— 一张图都不用下载。
#
#   注意: 这条路径不经过本机的 我的图/ 目录, 也不经过本地图片服务, 所以
#     · 不参与"缺图 404"检查(target_exists 一律返回 True);
#     · 生成规则时也不会被 only_existing 过滤掉。
_REMOTE_RE = re.compile(r"^(https?:)?//", re.I)


def is_remote_target(target):
    """目标是不是网络地址(http/https/协议相对 //host/…)。"""
    return bool(_REMOTE_RE.match(str(target or "").strip()))


# ==================================================================
# v4.87★ 两套机制: URL 替换 / 即时注入
#   同一条资源只能走一套 —— 走 inject 的, URL 替换那边必须让开(否则换完皮又被换一次图)。
#   判定收在一个函数里, 界面/服务端/脚本三处口径才不会分叉。
# ==================================================================
MODE_URL = "url"
MODE_INJECT = "inject"


def rule_mode(rule, modes=None, inject_on=None):
    """这条规则实际走哪套机制 → "url" 或 "inject"。

    优先级: 规则自己的 mode > 类别默认(modes[kind]) > url。
    ★ 总开关没开(inject_on=False)时一律降级成 url ——
      "不开注入" 必须等价于 "和加这个功能之前一模一样", 否则就是在赌存量规则不出事。
    """
    if modes is None or inject_on is None:
        try:
            from core import config as _cfg
            c = (_cfg.load_config() or {}).get("res_inject") or {}
            if modes is None:
                modes = c.get("modes") or {}
            if inject_on is None:
                inject_on = bool(c.get("on"))
        except Exception:
            modes = modes or {}
            inject_on = False if inject_on is None else inject_on
    m = str((rule or {}).get("mode") or "").strip().lower()
    if not m:
        m = str((modes or {}).get(str((rule or {}).get("kind") or "")) or "").strip().lower()
    if m != MODE_INJECT:
        return MODE_URL
    return MODE_INJECT if inject_on else MODE_URL


def inject_rules(rules=None, modes=None, inject_on=None):
    """一批规则里【真正走注入】的那些(供界面显示/服务端下发时分流)。"""
    out = []
    for r in (rules if rules is not None else load_rules()):
        if rule_mode(r, modes, inject_on) == MODE_INJECT:
            out.append(r)
    return out


def _load_json(p, default=None):
    # v4.109★ 改走 core.utils.load_json_file —— 原实现与它等价(不存在/损坏都返回 default),
    #   签名不动, 调用点零改动。
    from core.utils import load_json_file
    return load_json_file(p, default)


def _save_json(p, obj):
    d = os.path.dirname(p)
    if d:
        os.makedirs(d, exist_ok=True)
    with io.open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def load_rules():
    """读取用户自定义规则列表(每个元素是 dict)。

    v4.75★ 顺带做一次迁移: 主公立绘真身在 tavernChess/general/, 以前模板写错成
      general/maxBig/(服务器 404, 图库一片黑的就是它)。存量规则里的旧写法在这儿
      原地改写成新路径并写回 —— 不迁移的话, 用户的自定义主公规则会一直指向死地址。
    """
    rules = _load_json(RULES_PATH, [])
    if not isinstance(rules, list):
        return []
    migrated = False
    out = []
    for r in rules:
        if not isinstance(r, dict):
            continue
        # 补全缺省字段, 避免旧数据缺键
        r.setdefault("id", "r%d" % int(time.time() * 1000))
        r.setdefault("name", "")
        r.setdefault("kind", "背景")
        r.setdefault("pattern", "")
        r.setdefault("target", "")
        r.setdefault("enabled", True)
        r.setdefault("season", "")
        # v4.87★ 即时注入: mode 空 = 跟随类别默认(见 rule_mode), voice = 点击时播的语音
        r.setdefault("mode", "")
        r.setdefault("voice", "")
        # v4.110★ 两个时间戳: updated_at = 这条最后一次【改】的时间, synced_at = 最后一次
        #   确认【推到浏览器生效】的时间。以前没有这两个, 于是"我改过的 / 系统生成的 /
        #   已经推出去的"在界面上长得一模一样 —— 用户原话"改的没改的、是不是最新分不清"。
        r.setdefault("updated_at", 0)
        r.setdefault("synced_at", 0)
        if "general/maxBig/" in str(r.get("pattern") or ""):
            r["pattern"] = str(r["pattern"]).replace("general/maxBig/",
                                                     "tavernChess/general/")
            migrated = True
        # v4.75★ 目标写了官方地址却【没带协议】("web.sanguosha.com/x/pc/…") —— is_remote_target
        #   只认 http(s):// 和 //host/, 裸域名会被当成"本地文件"去找 → 不存在 → 这条规则
        #   被 /rules 静默跳过, 游戏里怎么换都没反应(用户现场: 异瞳*魔·神马超那条)。
        #   官方资源清单/图库页复制出来的地址就是这种裸域名形态, 这里统一补上 https://。
        t = str(r.get("target") or "").strip()
        for h in _OFFICIAL_HOSTS:
            if t.lower().startswith(h.lower()):
                r["target"] = "https://" + t
                migrated = True
                break
        out.append(r)
    if migrated:
        try:
            _save_json(RULES_PATH, out)
            sys.stderr.write("[res_rules] 已迁移存量规则(general/maxBig/→tavernChess/general/, "
                             "裸域名目标→https://)并写回\n")
        except Exception as e:
            sys.stderr.write("[res_rules] 迁移写回失败(本次会话内已生效): %s\n" % e)
    return out


def _rule_sig(r):
    """规则内容签名 —— 用来认"这条到底改过没有"。

    v4.110★ 只有签名【真变了】才刷新 updated_at。不然每次保存(加载默认规则、体检迁移、
      补协议前缀…)都会把时间刷成现在, "改过没推"这个状态就永远显示不出来。
    """
    return "|".join(str(r.get(k) or "") for k in
                    ("name", "kind", "pattern", "target", "enabled", "mode", "voice", "season"))


def save_rules(rules):
    """保存用户自定义规则。

    v4.110★ 顺带维护 updated_at: 拿旧文件里同 id 的签名跟新的比, 只有真改了才把
      updated_at 刷成现在。这样界面才分得出"我改过但还没推"和"早就推出去了"。
    """
    try:
        old = {str(r.get("id")): _rule_sig(r)
               for r in (_load_json(RULES_PATH, []) or []) if isinstance(r, dict)}
    except Exception:
        old = {}
    now = int(time.time() * 1000)
    for r in (rules or []):
        if not isinstance(r, dict):
            continue
        rid = str(r.get("id"))
        r.setdefault("synced_at", 0)
        if rid in old and old[rid] != _rule_sig(r):
            r["updated_at"] = now          # 真改了 → 记下改动时间
        else:
            r.setdefault("updated_at", now)   # 新规则/没变过 → 给个初值
    _save_json(RULES_PATH, rules)


def mark_synced():
    """v4.110★ 浏览器【确认重拉成功】后调: 给所有规则盖上 synced_at, 返回盖了几条。

    只在这一个地方盖 —— 只有浏览器真回话说"我拉到了", 才算推出去。以前同步完只是
    打一行状态字, 助手一关就什么都不记得了, 下次打开还是一脸"到底生效没有"。
    """
    rules = _load_json(RULES_PATH, [])
    if not isinstance(rules, list):
        return 0
    now = int(time.time() * 1000)
    n = 0
    for r in rules:
        if isinstance(r, dict):
            r["synced_at"] = now
            n += 1
    _save_json(RULES_PATH, rules)
    return n


def sync_state(r):
    """单条规则的同步状态 → (键, 显示文字)。键给界面上色用。

    判的顺序有讲究: 停用 / 缺图是"这条根本不会生效", 比"推没推"更要紧, 所以先判。
    """
    if not r.get("enabled", True):
        return ("off", "停用")
    if not target_exists(r.get("target")):
        return ("miss", "缺图")
    up = int(r.get("updated_at") or 0)
    sy = int(r.get("synced_at") or 0)
    if sy <= 0:
        return ("never", "没推过")
    if up > sy:
        return ("dirty", "改过没推")
    return ("ok", "已生效")


def _norm_path(p):
    return os.path.normpath(os.path.abspath(str(p or "")))


def target_exists(target):
    """目标是否可用 —— 本地文件看是否放了图, 网络地址一律算可用。

    v4.69★ 网络地址在这里【没有"存在性"可查】(除非真去请求一次, 那会让界面卡住),
      所以直接算"有"。界面上那一列的含义因此变成"这条规则准备好了吗":
      本地规则 = 我的图/ 里有这张图; 网络规则 = 地址填了就行。
    """
    t = str(target or "").strip()
    if not t:
        return False
    if is_remote_target(t):
        return True
    return os.path.exists(_norm_path(t))


def ensure_img_dir():
    os.makedirs(IMG_DIR, exist_ok=True)
    return IMG_DIR


def collect_default_rules(season=None):
    """从赛季配置表收集棋子立绘、主公立绘的默认规则。

    返回 [{"kind", "name", "pattern", "target", "enabled"}]
    这些规则不写入 data/resource_rules.json, 而是按需与自定义规则合并后生成扩展文件。
    """
    cfg = csv_loader.season_config(season)
    if not cfg:
        return []
    ensure_img_dir()
    out = []
    seen = set()
    for r in (cfg.get("ChessConf") or []):
        if not isinstance(r, dict):
            continue
        skin = str(r.get("DefaultSkin") or "").strip()
        name = str(r.get("ChessName") or "").strip()
        if not skin:
            continue
        pat = PIECE_URL.format(skin=skin)
        key = ("棋子立绘", skin, pat)
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "kind": "棋子立绘",
            "name": name or skin,
            "pattern": pat,
            "target": os.path.join(IMG_DIR, "%s.png" % skin).replace("\\", "/"),
            "enabled": True,
        })
    for r in (cfg.get("ChessGeneralConf") or []):
        if not isinstance(r, dict):
            continue
        skin = str(r.get("DefaultSkin") or "").strip()
        name = str(r.get("GeneralName") or "").strip()
        if not skin:
            continue
        for kind, pat in (("主公立绘", GENERAL_URL.format(skin=skin)),
                           ("主公立绘(旧路径)", OLD_GENERAL_URL.format(skin=skin))):
            key = (kind, skin, pat)
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "kind": kind,
                "name": name or skin,
                "pattern": pat,
                "target": os.path.join(IMG_DIR, "%s.png" % skin).replace("\\", "/"),
                "enabled": True,
            })
    return out


# v4.70★ 「直接用官方那张图」—— 用户原话:
#   "换皮肤那, 武将改动是已经要把三国杀服务器的加载好了对应的武将名字皮肤可以切换;
#    然后还可以自定义"
#
# 结论(都是可核对的, 不是猜的):
#   ✔ 每位棋子/主公的立绘在服务器上就是一张固定 URL, 名字 = 配置表的 DefaultSkin(武将拼音)。
#     所以【任意一条规则】的目标都能指到【任意一位武将的官方立绘】—— 把卢植那张换成陆逊那张,
#     一张图都不用下载, 也不用本地图片服务。这就是"按武将名字切换"。
#   ✔ 而且**一位武将不止一套**。ChessConf 每行带 SeasonID, 同一武将不同赛季的 DefaultSkin 会变:
#       陆逊  第1赛季 luxun   → 第2赛季起 luxun_02
#       关羽  第1赛季 guanyu  → 第2赛季起 guanyu_02
#     本赛季(s10)的表里两种名字都在, 共 19 位武将有两套。旧资源一般不会从服务器删掉,
#     所以这两套都能当目标 —— 这就是真正的"皮肤可以切换"(不用找图, 官方那两套来回换)。
#   ✔ 立绘有三个家族(都从抓包确认过路径形状, 见 资源替换方案.md 第二节):
#       runtime/tavernChess/piece/{skin}.png    棋盘上的棋子
#       runtime/tavernChess/card/{skin}.png     手牌/商店里那张武将牌
#       runtime/tavernChess/general/{skin}.png  主公大图(v4.75 实测修正, 两条线路都有)
#   ✘ 不做的: 凭后缀硬编 *_03..*_05 之类"可能存在的皮肤"。服务器上有没有我们不知道,
#     编出来就是 404 → 棋子变透明(资源替换方案.md 第七节 ③)。只列【配置表里确有其名】的。
OFFICIAL_HOST = "https://web.sanguosha.com"
# 匹配串都带域名(x / 10 两种线路), 所以"补全成 URL"这一步不是简单拼前缀 ——
# 拼错了会得到 web.sanguosha.com/web.sanguosha.com/... 这种地址(自己踩过)。
_OFFICIAL_HOSTS = ("web.sanguosha.com/", "www.sanguosha.com/")
CARD_URL = "web.sanguosha.com/x/pc/res/assets/runtime/tavernChess/card/{skin}.png"

# v4.73★ OL 大立绘两条家族(用户实测抓到的, 不是猜的):
#   runtime/general/big/static/500103.png        刘备某套皮肤的大图
#   runtime/general/big/static/621401.png        神马超(OL 皮肤编号 6214 + 品阶 01)
#   runtime/general/big/dynamic/821401/daiji.png 动态神马超的一帧(821401 = 621401 + 200000)
#   runtime/general/big/dynamic/821401/beijing.png 动态背景帧
# 【枚举不了】: 500103 / 621401 这种数字编号是主游戏(OL)的 dress 皮肤表, 自走棋客户端的
#   ChessConf / ChessGeneralConf 里只有拼音皮肤名(machao / shenmachao), 不带数字映射 ——
#   离线数据推不出"刘备有哪几套大图", 瞎编编号就是 404(和"凭后缀硬编 *_03"是同一种错)。
#   所以这两条家族不给"按武将名挑", 给「贴官方网址」: 用户在 DevTools 抓到什么 URL,
#   粘进来就归一化成匹配串(剥协议/域名/`?v=`缓存参数), 抓到什么就能换什么。
BIG_STATIC_URL = "web.sanguosha.com/x/pc/res/assets/runtime/general/big/static/{no}.png"
BIG_DYNAMIC_URL = ("web.sanguosha.com/x/pc/res/assets/runtime/"
                   "general/big/dynamic/{no}/{frame}.png")

# 家族标记 → 界面「类型」下拉里的叫法(check_pattern / 贴网址都用这一份, 别写两份)
# v4.75★ "general/maxBig/" 已死(两条线路都 404), 真身是 "tavernChess/general/";
#   旧的 maxBig 写法在 _res_family 的短形里仍认得(为了把存量规则迁到新写法)。
_URL_FAMILY_KIND = {
    "tavernChess/piece/": "棋子立绘",
    "tavernChess/card/": "武将牌面",
    "tavernChess/general/": "主公立绘",
    "general/big/static/": "OL大图",
    "general/big/dynamic/": "OL大图",
}


def official_pattern_from_url(url):
    """粘贴的官方资源网址 → 匹配串(剥掉协议/域名/`?v=…`缓存参数)。

    用户拿到这些 URL 的方式就是浏览器 DevTools 的网络面板(比如发现神马超的动态帧),
    所以入口不设防: 带不带协议、带不带 ?v= 都认, 只要求路径里有 /pc/res/assets/。
    返回 {"pattern", "family", "kind"}; 认不出(非官方资源路径)返回 {}。
      https://web.sanguosha.com/x/pc/res/assets/runtime/general/big/static/500103.png?v=7cb…
        → {"pattern": "runtime/general/big/static/500103.png",
           "family": "general/big/static/", "kind": "OL大图"}
    """
    p = str(url or "").strip()
    m = re.match(r"^https?://", p, re.I)
    if m:
        p = p[m.end():]
    if not (p.lower().startswith(_OFFICIAL_HOSTS[0]) or p.lower().startswith(_OFFICIAL_HOSTS[1])
            or p.startswith("/")):
        return {}
    q = p.find("?")
    if q >= 0:
        p = p[:q]                      # ?v=7cb5488811 这类缓存参数不进匹配串
    p = p.strip("/")
    i = p.find("/pc/res/assets/")
    if i < 0:
        return {}
    rel = p[i + len("/pc/res/assets/"):]
    fam = _res_family(rel)
    return {"pattern": rel, "family": fam, "kind": _URL_FAMILY_KIND.get(fam, "")}


def official_target_url(pattern):
    """资源路径 / 匹配串 → 官方完整 URL。三种写法都认, 认不出的按 x 线路兜底。

      web.sanguosha.com/x/pc/…/luxun.png  → https://web.sanguosha.com/x/pc/…/luxun.png  (配置表模板)
      /x/pc/res/…/luxun.png               → https://web.sanguosha.com/x/pc/res/…          (绝对路径)
      tavernChess/piece/luxun.png         → https://web.sanguosha.com/x/pc/res/assets/…   (相对路径, 兜底)
      https://别的站/x.png                → 原样返回
    """
    p = str(pattern or "").strip()
    if not p:
        return ""
    if is_remote_target(p):
        return p
    low = p.lower()
    for h in _OFFICIAL_HOSTS:
        if low.startswith(h):
            return "https://" + p
    if p.startswith("/"):
        return OFFICIAL_HOST + p
    return "%s/x/pc/res/assets/%s" % (OFFICIAL_HOST, p)


def official_skin_options(season=None):
    """每位武将/主公在服务器上的官方立绘清单(界面「挑选官方立绘」用)。

    返回 [{"kind", "name", "skin", "pattern", "url", "season", "current"}]
      kind    = 棋子立绘 / 武将牌面 / 主公立绘
      name    = 武将名(界面按它找)
      skin    = 立绘资源名(= URL 里那个文件名, 也是"第几套"的差别所在)
      current = 这套是不是【本赛季在用的那套】—— 同名两套时, 不标一下用户不知道哪张是"现在的"
    排序: 类别 → 武将名 → 立绘名(同名的两套会挨在一起, 一眼能看出"这个武将有 2 套")。
    """
    cfg = csv_loader.season_config(season)
    out = []
    seen = set()

    def _add(kind, name, skin, pattern, season_no=0, current=False):
        url = official_target_url(pattern)
        if not url or (kind, skin) in seen:
            return
        seen.add((kind, skin))
        out.append({
            "kind": kind,
            "name": str(name or skin),
            "skin": skin,
            "pattern": pattern,
            "url": url,
            "season": int(season_no or 0),
            "current": bool(current),
        })

    rows = [r for r in (cfg.get("ChessConf") or []) if isinstance(r, dict)]
    # 先算"本赛季在用的那套": 同一个武将名下 SeasonID 最大的那个 DefaultSkin
    cur_skin = {}
    for r in rows:
        nm = str(r.get("ChessName") or "").strip()
        sk = str(r.get("DefaultSkin") or "").strip()
        if not nm or not sk:
            continue
        try:
            sn = int(r.get("SeasonID") or 0)
        except Exception:
            sn = 0
        if nm not in cur_skin or sn >= cur_skin[nm][0]:
            cur_skin[nm] = (sn, sk)
    for r in rows:
        nm = str(r.get("ChessName") or "").strip()
        sk = str(r.get("DefaultSkin") or "").strip()
        if not sk:
            continue
        try:
            sn = int(r.get("SeasonID") or 0)
        except Exception:
            sn = 0
        is_cur = (cur_skin.get(nm, (0, ""))[1] == sk)
        _add("棋子立绘", nm, sk, PIECE_URL.format(skin=sk), sn, is_cur)
        _add("武将牌面", nm, sk, CARD_URL.format(skin=sk), sn, is_cur)
    for r in (cfg.get("ChessGeneralConf") or []):
        if not isinstance(r, dict):
            continue
        nm = str(r.get("GeneralName") or "").strip()
        sk = str(r.get("DefaultSkin") or "").strip()
        if not sk:
            continue
        _add("主公立绘", nm, sk, GENERAL_URL.format(skin=sk), 0, True)
    out.sort(key=lambda x: (x["kind"], x["name"], x["skin"]))
    return out


def relative_pattern(pattern):
    """完整匹配串 → `/pc/res/assets/` 之后那一段(规则里推荐写的形态)。

    为什么要给这个: 匹配串带域名的话就只有那一条线路能命中(棋子立绘/牌面走 `x/pc/`,
      主公大图/图集走 `10/pc/`)。写成 `runtime/tavernChess/piece/luxun_02.png` 这种
      "不含域名"的形态两条线路都能命中, 这也是用户"我写了个匹配串怎么不生效"里最容易
      被忽略的一层(见 资源替换方案.md 第二节末尾)。
    """
    p = str(pattern or "").strip()
    if not p:
        return ""
    i = p.find("/pc/res/assets/")
    if i < 0:
        return p
    return p[i + len("/pc/res/assets/"):]


def _res_family(s):
    """匹配串属于哪条 URL 家族(用来判断"用户是不是把棋子写成主公了")。"""
    t = str(s or "")
    for k in ("tavernChess/piece/", "tavernChess/card/", "tavernChess/general/",
              "general/big/static/", "general/big/dynamic/"):
        if k in t:
            return k
    # 也认"只写了后半截"的形态(例: runtime/piece/luxun.png) —— 给建议时按这个判家族,
    #   才不会把"想换棋子立绘"的人推去 card/ 那条上。
    # v4.75★ "/maxBig/" 是已死的旧写法, 仍映射到主公立绘(存量规则迁移/旧截图都要认得);
    #   不能用 "/general/" 当短形 —— 它会撞上 general/big/static 那两家。
    for short, full in (("/piece/", "tavernChess/piece/"),
                        ("/card/", "tavernChess/card/"),
                        ("/maxBig/", "tavernChess/general/"),
                        ("/big/static/", "general/big/static/"),
                        ("/big/dynamic/", "general/big/dynamic/")):
        if short in t:
            return full
    return ""


def known_patterns(season=None, current_only=False):
    """本赛季配置表里【确有其名】的资源完整匹配串(棋子立绘 / 武将牌面 / 主公立绘)。

    current_only=True 时只列【本赛季真的会被请求】的那一套 —— 这是判断"能不能命中"的
      唯一正确口径。两者必须分开: `luxun.png` 在第 1 赛季的行里确实存在, 但本季(s10)
      的表用的是 `luxun_02.png`, 游戏只会请求后者。用全量去判断就会得出"luxun.png 也能
      命中"这个错误结论 —— 而那正是用户"配了没反应"的现场。
    current_only=False 时连往季的名字一起列, 用来"猜用户想写的是哪一条"。
    """
    try:
        opts = official_skin_options(season)
    except Exception as e:
        sys.stderr.write("[res_rules] 读官方立绘清单失败: %s\n" % e)
        return []
    out, seen = [], set()
    for o in opts:
        if current_only and not o.get("current"):
            continue
        pat = str(o.get("pattern") or "")
        if pat and pat not in seen:
            seen.add(pat)
            out.append(pat)
    return out


def check_pattern(pattern, season=None):
    """自检一条匹配串【本赛季能不能命中】—— 返回 {"ok", "hint", "suggest"}。

    用户原话: "tavernChess/card/xiahoudun.png / …/piece/luxun.png 我用这俩换咋都不行?
      为什么你写的那几个生效了?" —— 不是规则没生效, 而是这俩匹配串【本赛季不会被请求】:
      夏侯惇/陆逊从第 2 赛季起 DefaultSkin 变成 xiahoudun_02 / luxun_02, 本季表里用的就是
      带 _02 的那个。按"包含"匹配, `…/xiahoudun.png` 不是 `…/xiahoudun_02.png` 的子串,
      所以一条都命中不了(见 资源替换方案.md 第九节 ③)。

    这里做两件事: ① 判定能不能命中; ② 能猜出正确写法时给出 suggest(少写赛季后缀 / 目录
    或域名写错这两种最常见的问题都能猜), 界面拿它直接问用户"要不要改成这条"。
    """
    pat = str(pattern or "").strip()
    if not pat:
        return {"ok": False, "hint": "匹配串是空的", "suggest": ""}
    cur = known_patterns(season, current_only=True)
    if not cur:
        # 读不到配置表时别乱下结论 —— 说清"判不了", 而不是吓用户"你的规则是错的"
        return {"ok": True, "hint": "没读到本赛季配置表, 无法自检(规则本身不拦)", "suggest": ""}
    for full in cur:
        if pat in full:
            return {"ok": True, "hint": "本赛季会请求这个资源, 规则能命中", "suggest": ""}
    # ---- 没命中, 找"最像的那一条" ----
    norm = pat.replace("\\", "/")
    base = norm.rpartition("/")[2]
    stem, dot, ext = base.rpartition(".")
    if not dot:
        stem, ext = base, "png"
    stem_l, ext_l = stem.lower(), ext.lower()
    want_fam = _res_family(norm)
    same_name, missing_suffix = [], []
    for full in known_patterns(season, current_only=False):
        rel = relative_pattern(full)
        rb = rel.rpartition("/")[2]
        rstem, _, rext = rb.rpartition(".")
        if rext.lower() != ext_l:
            continue
        rl = rstem.lower()
        tail = rl[len(stem_l):] if rl.startswith(stem_l) else ""
        if rl == stem_l:
            same_name.append(rel)                      # 名字对, 目录/域名写错了
        elif tail and (tail.startswith("_") or tail.isdigit()):
            missing_suffix.append(rel)                 # 少了本赛季的后缀(_02 / 2)
    # 同家族的优先(用户想换棋子就别给他推到主公那条上去)
    def _pref(lst):
        return sorted(lst, key=lambda s: (0 if want_fam and want_fam in s else 1, s))
    if missing_suffix:
        suggest = _pref(missing_suffix)[0]
        return {"ok": False, "suggest": suggest, "hint":
                "本赛季用的是【%s】, 不是你写的 %s —— 游戏请求的是前者, 所以规则一直没命中。"
                "武将的 DefaultSkin 会随赛季变(第 2 赛季起变成 xiahoudun_02 / luxun_02 这种)。"
                % (suggest.rpartition("/")[2], base)}
    if same_name:
        suggest = _pref(same_name)[0]
        return {"ok": False, "suggest": suggest, "hint":
                "资源名是对的, 但路径/域名那一段不对 —— 本赛季确认存在的是 %s。"
                "(棋子立绘/牌面在 tavernChess/piece/ 或 card/, 主公立绘在 tavernChess/general/。)"
                % suggest}
    # 既不是"名字对目录错"也不是"少后缀", 有两种可能: 武将名真写错了, 或者它压根不属于
    #   配置表(背景/音效/图集这些表里没有)。后者没法判, 所以返回 ok=None 让界面显示成
    #   "无法判定"而不是"错的" —— 否则用户一条好好的背景规则会被吓成红的, 反而不敢用。
    fam = _res_family(norm)
    if fam in ("general/big/static/", "general/big/dynamic/"):
        # v4.73★ OL 大立绘: 编号在 OL 的 dress 表里, 自走棋配置表【没有】, 本来就判不了
        #   —— 但要给一句专门的说明, 不能落到下面"背景/音效"那段笼统话里。
        hint = ("这条属于 OL 大立绘(%s), 编号(如 500103 / 621401)在主游戏的皮肤表里, "
                "自走棋配置表里没有 —— 无法自动核对, 抓到的 URL 能用就是能用。" %
                ("静态" if fam.endswith("static/") else "动态帧"))
        if fam.endswith("dynamic/"):
            hint += (" 动态帧规律: dynamic/ 的编号 = 静态编号 + 200000"
                     "(例: static/621401 → dynamic/821401), 每一帧(daiji.png / beijing.png …)"
                     "是【单独一个文件】, 要换整段动画得一帧建一条规则。")
        return {"ok": None, "suggest": "", "hint": hint}
    if fam:
        return {"ok": False, "suggest": "", "hint":
                "看路径像武将/主公立绘, 但本赛季配置表里没有这个名字 —— 核对文件名是否等于 "
                "ChessConf.DefaultSkin / ChessGeneralConf.DefaultSkin(注意本赛季可能带 _02 "
                "后缀), 或点「按武将名填匹配串 …」让助手直接填。"}
    return {"ok": None, "suggest": "", "hint":
            "这条不在赛季配置表里(背景/音效/图集本来就不在表里), 自检无法判定 —— "
            "到浏览器网络面板核对 URL 里那一段是否一字不差即可。"}


def build_rules(season=None, custom_rules=None):
    """合并自定义规则 + 默认规则, 生成最终要给扩展用的规则列表。

    自定义规则优先: 如果 pattern 相同, 默认规则被覆盖(用户手动指定的背景/皮肤生效)。
    规则顺序: 自定义在前(优先匹配), 默认在后。

    v4.71★ 【停用】也必须能压住默认规则 —— 这一条是修 bug, 不是风格。
      现象(用户原话: "还有不能启动与否切换?"): 界面上选中一条默认规则点「切换启用」,
      那一列从"是"变成"否", 看着生效了, 可游戏里照样被替换 —— 因为旧版这里是
      "只有【启用】的自定义规则才记进 custom_pats", 于是被停用的那条占不住位置,
      同名默认规则又原样补了回来。
      停用一条规则 = "这个资源我不换", 所以覆盖关系必须按【pattern 出现过就占位】来算,
      与它启用与否无关。
    """
    if custom_rules is None:
        custom_rules = load_rules()
    defaults = collect_default_rules(season)
    # custom_pats = "被用户表过态的 pattern"(启用/停用都算), 默认规则遇到就不参与
    custom_pats = set()
    out = []
    _seen_exact = set()
    for r in custom_rules:
        if not isinstance(r, dict):
            continue
        pat = str(r.get("pattern") or "").strip()
        if not pat:
            continue
        custom_pats.add(pat)
        if not r.get("enabled", True):
            continue
        # v4.77★ 完全重复的规则(同一个匹配串 + 同一个目标)只留第一条。
        #   起因: 自检 R7b 在【用户真表】上抓到一条 xiangyu.png 配了两遍(多半是操作时加重的),
        #   而扩展端是按 pattern 去重的 → 第二条永远不会命中, 只能在弹出层写一句
        #   "重复匹配串 1 条" 让用户自己猜。同串同目标合并没有任何信息损失, 在源头做掉最干脆。
        #   ★ 只合并"匹配串和目标都一样"的: 同 pattern 不同目标是【真冲突】(谁赢看不出来),
        #     那种要留给用户自己决定(扩展端会照旧报"重复匹配串")。
        _key = (pat, str(r.get("target") or "").strip())
        if _key in _seen_exact:
            continue
        _seen_exact.add(_key)
        out.append(dict(r))
    # 再放默认规则(排除已被自定义表过态的 pattern)
    for d in defaults:
        if d["pattern"] in custom_pats:
            continue
        out.append(d)
    return out


# v4.71★ 界面上那一列「说明」的文案来源 —— 用户原话: "也备注好这都是什么"。
#
# 为什么值得单开一张表(界面里写 inline 也行): 这几句话是"用户看懂这张表"的全部解释,
#   下面「资源预览」HTML 和界面表格是【同一个口径】, 分两处写迟早会不一致。
KIND_NOTES = {
    "棋子立绘": "棋盘上的棋子(游戏请求 piece/<名字>.png)",
    "武将牌面": "手牌/商店里那张武将牌(card/<名字>.png)",
    "主公立绘": "主公大图(tavernChess/general/<名字>.png, 两条线路都有)",
    "主公立绘(旧路径)": "同一条资源的另一条线路(x/pc), 两个都配上最保险",
    "背景": "任意资源(匹配串自己写, 从网络面板里抄一段)",
    "自走棋背景": "自走棋的场景底图(tavernChess*Bg.png: 棋盘/节日/大厅/选将各一张)",
    "音效": "任意音频资源(同上, 匹配串自己写)",
    "其他": "不在上面几类里的资源",
}


def kind_note(kind):
    """类别 → 一句人话说明(表里没有的类别也不报错, 返回兜底文案)。"""
    return KIND_NOTES.get(str(kind or ""), "自定义资源")


# 会请求官方服务器、能直接拿 URL 预览的类别(其余类别的匹配串是用户自己抄的,
#   猜出来的 URL 多半不存在, 预览里就不给"官方这张"那一栏)。
OFFICIAL_KINDS = ("棋子立绘", "武将牌面", "主公立绘", "主公立绘(旧路径)")


def official_alive(url, timeout=6.0):
    """问一下官方服务器: 这张图在不在 → (alive, detail)。

    为什么需要真去请求一次(而不是像 target_exists 那样"有地址就算有"):
      用户问的是"根据这个能查到皮肤的 png 吗" —— 光把候选名字拼成 URL 是不算查到的,
      服务器上没有那个文件时, 规则一配就 404, 游戏里表现为"棋子变透明"。
      所以这里用 HEAD 真问一次(不下载图, 只拿状态码和大小), 结论才敢写"服务器上有这张"。

    失败一律当"查不到"返回(不抛异常): 离线/断网只说明"这次核不了", 不该让界面崩。
    返回值 detail 里带状态码或异常原因, 界面上直接显示给人看。
    """
    u = str(url or "").strip()
    if not u:
        return False, "没有地址"
    if not is_remote_target(u):
        return False, "不是网络地址"
    if u.startswith("//"):
        u = "https:" + u
    try:
        import urllib.request as _ur
        # 同 remote_image_size: 地址里带中文/空格要先转义, 否则连请求都发不出去
        req = _ur.Request(ascii_url(u), method="HEAD", headers={"User-Agent": "Mozilla/5.0"})
        with _ur.urlopen(req, timeout=float(timeout or 6.0)) as r:
            size = r.headers.get("Content-Length")
            try:
                size_txt = "%.1f KB" % (int(size) / 1024.0) if size else "大小未知"
            except Exception:
                size_txt = "大小未知"
            return (200 <= int(r.status) < 300), "HTTP %s, %s" % (r.status, size_txt)
    except Exception as e:
        code = getattr(e, "code", None)
        if code:
            return False, "HTTP %s(服务器上没有这个文件)" % code
        return False, "%s: %s" % (type(e).__name__, e)


# ============================================================================
# v4.72★ 【原图到底多大 / 换了会不会变形】—— 用户原话:
#   "还有那个替换前的尺寸大小, 分辨率, 像素多少? 要不换进去, 好乱都变形了"
#
# 为什么会变形(可核对, 见 参考_sgs_forward_looking-main/source/all_tavernchess_classes.txt):
#   游戏界面全是 `UIMakerUtil.MakeSgsTexture(this, "名字", x, y, w, h)` 这种写法 —— 贴图被
#   【塞进一个写死的矩形】。所以替换图的宽高比只要跟原图不一样, 画面里就一定是被拉伸的
#   (而不是"留白"或"裁掉"), 图再清晰也救不回来。用户看到的"好乱"就是这么来的。
# 于是"该放多大的图"只有一个正确依据: 【原图多大】。这里负责把它量出来。
#
# 怎么量而不下载整张图:
#   远程 → 只请求前 4KB(HTTP Range)解析文件头 —— 一张 500KB 的立绘只为拿宽高不值得;
#   本地 → 读文件头几千字节就够(常见格式的宽高都在头部);
#   量到过的写进 data/image_sizes.json 缓存, 下次直接显示(官方图尺寸基本不变)。
# ============================================================================
IMAGE_SIZE_CACHE_FILE = os.path.join(DATA_DIR, "image_sizes.json")
_IMG_EXT_RE = re.compile(r"\.(png|jpe?g|webp|bmp|gif)(\?|#|$)", re.I)

_SIZE_CACHE = None          # {url: [宽, 高, 格式]} —— 落盘缓存(只存【量到】的)
_SIZE_MISS = {}             # {url: 原因}           —— 本次运行内"没量到"的, 不落盘
_SIZE_LOCK = threading.Lock()


def ascii_url(url):
    """地址里带中文/空格时先转义 —— urllib 遇到非 ASCII 会直接抛 UnicodeEncodeError。

    官方的图都是纯 ASCII 路径, 但"目标"那一栏允许用户填任意地址(有人会填自己 NAS 上
    带中文名的图), 不转义的话这类地址永远量不到, 而报错信息还看不出是编码问题。
    已经转义过的(%XX)不会被二次转义。
    """
    u = str(url or "").strip()
    try:
        import urllib.parse as _up
        return _up.quote(u, safe=":/?#[]@!$&'()*+,;=%~")
    except Exception:
        return u


def parse_image_size(data):
    """图片字节流 → (宽, 高, 格式)。认不出返回 (0, 0, "")。

    只认文件头, 所以传前几 KB 就够 —— 见 local_image_size / remote_image_size。
    支持: PNG(含 APNG) / JPEG / GIF / WebP(VP8/VP8L/VP8X) / BMP。

    为什么要自己解析而不是用 PIL: 这里只需要"宽 x 高", 而 Pillow 读一张损坏的图会抛异常,
      也拿不到"只给了一段头部"的情况; 更关键的是 —— 我们【根本不想把图下载下来】。
    """
    b = bytes(data or b"")
    if len(b) < 16:
        return 0, 0, ""
    # PNG: 8 字节签名 + 第 12 字节起是 IHDR, 宽/高各 4 字节大端
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        if len(b) >= 24 and b[12:16] == b"IHDR":
            return (int.from_bytes(b[16:20], "big"), int.from_bytes(b[20:24], "big"), "png")
        return 0, 0, ""
    # GIF: 签名 6 字节, 宽/高各 2 字节小端
    if b[:6] in (b"GIF87a", b"GIF89a"):
        if len(b) >= 10:
            return (int.from_bytes(b[6:8], "little"), int.from_bytes(b[8:10], "little"), "gif")
        return 0, 0, ""
    # BMP: 'BM' + 第 18 字节起 宽/高各 4 字节小端(高为负 = 自上而下, 取绝对值)
    if b[:2] == b"BM":
        if len(b) >= 26:
            w = int.from_bytes(b[18:22], "little", signed=True)
            h = int.from_bytes(b[22:26], "little", signed=True)
            return abs(w), abs(h), "bmp"
        return 0, 0, ""
    # WebP: RIFF....WEBP, 三种子格式的宽高位置都不一样(踩过的坑: VP8X 存的是"画布尺寸",
    #   而且宽高都是"值-1"; VP8L 的宽高是位打包的, 不能按字节直读)
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        fmt = b[12:16]
        if fmt == b"VP8 ":
            if len(b) < 30:
                return 0, 0, ""
            if b[23:26] == b"\x9d\x01\x2a":       # 帧头同步码, 对不上说明这不是关键帧头
                w = int.from_bytes(b[26:28], "little") & 0x3FFF
                h = int.from_bytes(b[28:30], "little") & 0x3FFF
                return w, h, "webp"
        elif fmt == b"VP8L":
            # 无损: 只要 25 字节(v4.72 自检抓到的 bug —— 原来统一要求 30 字节,
            #   头刚好短一点的文件会被判成"认不出")
            if len(b) < 25:
                return 0, 0, ""
            if b[20] == 0x2F:                     # 无损格式的那个签名字节
                bits = int.from_bytes(b[21:25], "little")
                return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1, "webp"
        elif fmt == b"VP8X":
            if len(b) < 30:
                return 0, 0, ""
            return (int.from_bytes(b[24:27], "little") + 1,
                    int.from_bytes(b[27:30], "little") + 1, "webp")
        return 0, 0, ""
    # JPEG: FF D8 开始逐个 marker 走, 碰到 SOFn 才是宽高
    if b[:2] == b"\xff\xd8":
        i, n = 2, len(b)
        while i + 3 < n:
            if b[i] != 0xFF:
                i += 1
                continue
            m = b[i + 1]
            # 填充字节/无长度字段的标记(独立标记 RSTn 与 SOI/EOI)
            if m in (0xFF, 0x01) or 0xD0 <= m <= 0xD9:
                i += 2
                continue
            seg = int.from_bytes(b[i + 2:i + 4], "big")
            if seg < 2:
                return 0, 0, ""
            # SOF0..SOF15 里, C4(DHT)/C8(JPG)/CC(DAC) 不是帧头, 排除掉
            if 0xC0 <= m <= 0xCF and m not in (0xC4, 0xC8, 0xCC):
                if i + 9 > n:
                    return 0, 0, ""       # 头没读全(EXIF 很长时会有) → 宁可量不到也不瞎猜
                return (int.from_bytes(b[i + 7:i + 9], "big"),
                        int.from_bytes(b[i + 5:i + 7], "big"), "jpg")
            if m == 0xDA:                 # 扫描线数据开始, 后面不会再有宽高了
                return 0, 0, ""
            i += 2 + seg
        return 0, 0, ""
    return 0, 0, ""


def _size_cache_load():
    global _SIZE_CACHE
    with _SIZE_LOCK:
        if _SIZE_CACHE is None:
            d = _load_json(IMAGE_SIZE_CACHE_FILE, {})
            _SIZE_CACHE = d if isinstance(d, dict) else {}
        return _SIZE_CACHE


def remember_image_size(url, w, h, fmt):
    """量到的尺寸记下来(下次直接显示, 不再发请求)。写盘失败不影响这次使用。"""
    u = str(url or "").strip()
    if not u or not (w and h):
        return
    c = _size_cache_load()
    with _SIZE_LOCK:
        if c.get(u) == [int(w), int(h), str(fmt or "")]:
            return
        c[u] = [int(w), int(h), str(fmt or "")]
        try:
            _save_json(IMAGE_SIZE_CACHE_FILE, c)
        except Exception as e:
            sys.stderr.write("[res_rules] 图片尺寸缓存写盘失败: %s\n" % e)


def local_image_size(path, first=1024, more=65536):
    """本地文件 → ((宽, 高, 格式), 说明)。只读文件头, 不加载整张图。

    first/more: 先读 1KB(PNG/GIF/BMP 这几种 33 字节就够), 认不出再补读到 64KB
      (JPEG 前面塞了长 EXIF 时, SOF 会排在几 KB 之后)。
    """
    p = _norm_path(path)
    if not os.path.isfile(p):
        return (0, 0, ""), "文件不存在"
    try:
        with open(p, "rb") as f:
            data = f.read(int(first))
            w, h, fmt = parse_image_size(data)
            if not (w and h) and os.path.getsize(p) > len(data):
                data += f.read(max(0, int(more) - len(data)))
                w, h, fmt = parse_image_size(data)
    except Exception as e:
        return (0, 0, ""), "%s: %s" % (type(e).__name__, e)
    if not (w and h):
        return (0, 0, ""), "认不出尺寸(不是常见图片格式?)"
    return (w, h, fmt), "%dx%d" % (w, h)


def remote_image_size(url, timeout=6.0, use_cache=True):
    """远程图 → ((宽, 高, 格式), 说明)。**只下前 4KB**, 不下载整张图。

    拿不到就是拿不到(返回 0), 原因写进说明 —— 断网/404 都只是"这次量不到",
      不该让界面卡住或者抛异常。
    """
    u = str(url or "").strip()
    if not u:
        return (0, 0, ""), "没有地址"
    if not is_remote_target(u):
        return (0, 0, ""), "不是网络地址"
    if u.startswith("//"):
        u = "https:" + u
    if use_cache:
        c = _size_cache_load().get(u)
        if c:
            return (int(c[0]), int(c[1]), str(c[2] or "")), "缓存"
        miss = _SIZE_MISS.get(u)
        if miss:
            return (0, 0, ""), miss          # 同一次运行里不重复问同一个地址
    try:
        import urllib.request as _ur
        # 请求用转义后的地址(中文名/空格会让 urllib 直接抛 UnicodeEncodeError),
        #   缓存键仍用原地址 —— 下次拿同一个字符串来问就命中。
        req = _ur.Request(ascii_url(u), headers={"User-Agent": "Mozilla/5.0",
                                                "Range": "bytes=0-4095"})
        with _ur.urlopen(req, timeout=float(timeout or 6.0)) as r:
            # 服务器不认 Range 时(返回 200 + 整张图), 这里也只读 64KB 就撒手 ——
            #   要的是宽高, 不是那张图。
            data = r.read(65536)
        w, h, fmt = parse_image_size(data)
        if w and h:
            remember_image_size(u, w, h, fmt)
            return (w, h, fmt), "%dx%d" % (w, h)
        return (0, 0, ""), "取到了内容但认不出图片头(可能不是图片?)"
    except Exception as e:
        code = getattr(e, "code", None)
        why = ("HTTP %s" % code) if code else ("%s: %s" % (type(e).__name__, e))
        if use_cache:
            _SIZE_MISS[u] = why              # 只记在内存里, 下次启动会重新试
        return (0, 0, ""), why


def official_url_candidates(pattern):
    """匹配串 → 官方地址候选(按可能性排序, 逐个试)。

    为什么是一串而不是一个: 匹配串是"包含匹配"用的字符串, 用户从网络面板抄来的往往
      只是后半截(如 tavernChess/tavernChessBg.png), 而官方真实路径里还有 runtime/ 这一层
      —— 直接拼会得到 .../res/assets/tavernChess/... 这种不存在的地址(量尺寸就全量不到)。
    """
    p = str(pattern or "").strip()
    if not p:
        return []
    if is_remote_target(p):
        return [p if not p.startswith("//") else "https:" + p]
    out = []
    first = official_target_url(p)
    if first:
        out.append(first)
    if "/pc/res/" not in p.lower():
        rel = p.lstrip("/")
        for line in ("x", "10"):        # 两条线路都试(x: 棋子/牌面, 10: 主公大图/图集)
            u = "%s/%s/pc/res/assets/runtime/%s" % (OFFICIAL_HOST, line, rel)
            if u not in out:
                out.append(u)
    return out


def measure_official_size(pattern, timeout=6.0):
    """官方那张图多大 → ((宽, 高, 格式), 用上的地址, 说明)。

    逐个候选地址试, 命中一个就停 —— 默认规则的匹配串本身就是完整路径, 第一个候选就中,
    不会多发请求; 自定义背景那种"只写后半截"的才会多试一两个。
    """
    tried = []
    for u in official_url_candidates(pattern):
        sz, why = remote_image_size(u, timeout=timeout)
        if sz[0] and sz[1]:
            return sz, u, "%dx%d" % (sz[0], sz[1])
        tried.append("%s → %s" % (u, why))
    if not tried:
        return (0, 0, ""), "", "这条资源猜不出官方地址"
    return (0, 0, ""), "", "官方地址没量到: " + "; ".join(tried[:3])


def _ratio_txt(w, h):
    """宽高比写成人话(1024x1024 → 1:1, 800x600 → 4:3)。"""
    try:
        import math
        a, b = int(w or 0), int(h or 0)
        if a <= 0 or b <= 0:
            return "?"
        g = math.gcd(a, b) or 1
        return "%d:%d" % (a // g, b // g)
    except Exception:
        return "?"


def size_text(size):
    """(宽, 高, 格式) → "1024×1024"; 量不到给空串。"""
    try:
        w, h = int(size[0] or 0), int(size[1] or 0)
    except Exception:
        return ""
    return ("%d×%d" % (w, h)) if (w and h) else ""


def size_advice(orig, mine):
    """原图尺寸 vs 你这张 → (会不会变形, 一句人话建议)。

    等比怎么判: 先交叉相乘(避开浮点误差), 但**不要求完全相等**, 而是看相对误差是否在
      0.5% 以内。为什么必须有这个容差(实测踩到): 棋盘的背景原图是 2134×1200, 而大家
      手边下的壁纸多半是 1920×1080 —— 这两者数学上差 0.07%(2134:1200 = 16.007:9),
      肉眼完全看不出差别; 可要是按"精确等比"判, 用户每放一张 16:9 的图都会被报"会变形",
      他试两次之后就不再信这个提示了(比不提示更糟)。0.5% ≈ 2000 像素宽上差 10 像素,
      比手抖裁图的误差还小。
    返回 ok: True=不会变形 / False=会变形 / None=比不了(缺一边的尺寸)。
    """
    ow, oh = int(orig[0] or 0), int(orig[1] or 0)
    mw, mh = int(mine[0] or 0), int(mine[1] or 0)
    if not (ow and oh):
        if mw and mh:
            return None, ("你这张是 %d×%d —— 原图尺寸没量到(官方地址取不到或断网)。"
                          "自定义背景这类助手猜不出官方地址, 可以自己在网络面板里看一眼那张图多大,"
                          "或把它的完整 URL 填进「目标」。" % (mw, mh))
        return None, "两边的尺寸都没量到。"
    if not (mw and mh):
        return None, "原图 %d×%d(%s)。你的图还没量到(选了图/放了图才会读)。" % (
            ow, oh, _ratio_txt(ow, oh))
    diff = abs(ow * mh - oh * mw) / float(oh * mw)      # 宽高比的相对误差
    if diff <= 0.005:
        if ow == mw and oh == mh:
            return True, "你这张 %d×%d 和原图【一样大】—— 最省事, 直接放就行。" % (mw, mh)
        near = ("(比例差 %.1f%%, 看不出差别)" % (diff * 100)) if diff > 0.0005 else ""
        return True, ("比例一致%s, 不会被拉伸变形。尺寸不同时引擎会等比缩放: "
                      "你这张 %d×%d → 原图 %d×%d, 想最清晰就裁成 %d×%d。"
                      % (near, mw, mh, ow, oh, ow, oh))
    return False, (
        "⚠ 会变形(这就是你说的『好乱』): 原图 %d×%d 是 %s, 你这张 %d×%d 是 %s "
        "(比例差 %.0f%%) —— 引擎把图【塞进一个写死的矩形】, 比例不一样就会被拉伸。\n"
        "改法: 把你的图裁成 %d×%d(或等比的 %d×%d / %d×%d)。"
        % (ow, oh, _ratio_txt(ow, oh), mw, mh, _ratio_txt(mw, mh), diff * 100,
           ow, oh, ow // 2 or ow, oh // 2 or oh, ow * 2, oh * 2))


# 各种后缀游戏能不能显示 —— 用户问"背景可以放动态图或者视频吗? .mp4":
#   背景/皮肤在游戏里是【画布贴图】(UIMakerUtil.MakeSgsTexture → Laya 纹理 → 画进 canvas),
#   所以: 动图只会被取到一帧; 视频根本不会被解码成图片。据实写在这里, 免得用户白折腾。
FORMAT_NOTES = {
    "png": (True, "最稳, 推荐"),
    "jpg": (True, "照片类够用; 想要透明背景只能用 png/webp"),
    "jpeg": (True, "同 jpg"),
    "bmp": (True, "能用但文件大, 不如 png"),
    "webp": (True, "能用; 若是【动图 webp】, 只会显示第一帧(游戏按画布贴图画, 不是 CSS 背景)"),
    "gif": (True, "能显示, 但【动图 gif 只会显示第一帧】—— 游戏是按画布贴图画的, 不是 CSS 背景"),
    "mp4": (False, "视频【显示不出来】: 贴图只认图片, 换成 mp4 这一格会变空白"),
    "webm": (False, "视频显示不出来(同上)"),
    "mov": (False, "视频显示不出来(同上)"),
    "mkv": (False, "视频显示不出来(同上)"),
    "avi": (False, "视频显示不出来(同上)"),
    "svg": (None, "矢量图引擎多半不支持, 建议另存成 png"),
}


def check_target_format(target):
    """目标文件后缀 → (游戏能不能显示, 一句说明)。

    None = 不好说(认不出的后缀)。**不改行为, 只提醒** —— 用户要真想放什么就放什么,
      但"放了 mp4 然后发现那一格空白"是最难排查的一种, 所以在这里就说清。
    """
    t = str(target or "").strip()
    if not t or is_remote_target(t):
        return None, ""
    ext = os.path.splitext(t)[1].lower().lstrip(".")
    if not ext:
        return None, "这个文件没有后缀, 游戏可能认不出(建议 .png)"
    if ext in FORMAT_NOTES:
        return FORMAT_NOTES[ext]
    return None, "后缀 .%s 建议改成 .png" % ext


def rule_size_info(rule, timeout=6.0, probe_official=True):
    """一条规则的两张图尺寸 + 变形判定 → dict。

      official / official_url / official_note = 游戏【本来】那张(官方图)
      mine / mine_url / mine_note             = 你配的替换图
      ok / advice                             = 会不会变形 + 一句人话建议

    probe_official=False: 只读你那张(本地文件, 不联网) —— 界面刷 360 行时用这个,
      免得每刷一次表就发几百个请求。
    """
    r = rule if isinstance(rule, dict) else {}
    pat = str(r.get("pattern") or "")
    tgt = str(r.get("target") or "").strip()
    off, off_url, off_note = (0, 0, ""), "", "没量"
    if probe_official and pat and _IMG_EXT_RE.search(pat):
        off, off_url, off_note = measure_official_size(pat, timeout=timeout)
    if not tgt:
        mine, mine_url, mine_note = (0, 0, ""), "", "目标没填"
    elif is_remote_target(tgt):
        mine, mine_note = remote_image_size(tgt, timeout=timeout)
        mine_url = tgt
    else:
        mine, mine_note = local_image_size(tgt)
        mine_url = _norm_path(tgt)
    ok, advice = size_advice(off, mine)
    fmt_ok, fmt_note = check_target_format(tgt)
    return {"pattern": pat, "official": off, "official_url": off_url,
            "official_note": off_note, "mine": mine, "mine_url": mine_url,
            "mine_note": mine_note, "ok": ok, "advice": advice,
            "fmt_ok": fmt_ok, "fmt_note": fmt_note}


def size_cell_text(info):
    """尺寸信息 → 表格那一列要显示的一行字。"""
    if not info:
        return ""
    off = size_text(info.get("official"))
    mine = size_text(info.get("mine"))
    if off and mine:
        txt = "%s → %s" % (off, mine)
        if info.get("ok") is False:
            txt += " ⚠变形"
    else:
        txt = off or mine or ""
    if info.get("fmt_ok") is False:
        txt = (txt + " ⚠格式").strip()
    return txt


def target_preview_url(base_url, rule):
    """这条规则"当前要换成的那张图"拿什么地址预览 → (地址, 说明)。

    三种目标各自的看法不一样, 都说清楚, 免得用户以为"预览是空的=功能坏了":
      网络地址(含官方立绘) → 直接用那个地址(浏览器能开);
      本地文件存在        → 走本机图片服务 /img/名字(base_url 拿不到就给本地路径);
      本地文件还没有      → 空, 由调用方提示"把 名字.png 放进 我的图/"。
    """
    r = rule if isinstance(rule, dict) else {}
    t = str(r.get("target") or "").strip()
    if not t:
        return "", "目标没填"
    if is_remote_target(t):
        if t.startswith("//"):
            t = "https:" + t
        return t, "网络地址(不用下载)"
    if not os.path.exists(_norm_path(t)):
        return "", "还没放图: 把 %s 放进 我的图/ 就有了" % os.path.basename(t)
    u = remote_to_local_url(base_url, t) if base_url else ""
    return (u or t), ("本机图片服务 %s" % u if u else "本地文件(没拿到服务地址, 用文件路径打开)")


def preview_pair(base_url, rule, sizes=None):
    """一条规则的两张图: {"official", "current", "note", "ready", …} —— 资源预览页专用。

    official = 游戏【现在】在加载的那张(官方服务器上那张)
    current  = 你配的替换目标(放好的本地图 / 官方立绘 / 任意 URL)
    两张并排一看就知道"要不要换、换成什么、有没有放错"。

    v4.72★ sizes: {匹配串: rule_size_info(...)} —— 由界面"量尺寸"那一步攒好传进来。
      **这里刻意不自己去量**: 预览要的是"秒开", 而量 360 条尺寸要发几百个请求。
    """
    r = rule if isinstance(rule, dict) else {}
    kind = str(r.get("kind") or "")
    off = ""
    if kind in OFFICIAL_KINDS:
        off = official_target_url(r.get("pattern"))
    cur, note = target_preview_url(base_url, r)
    # 官方图地址: 猜得出候选就用候选里那个更准的(自定义背景那种"只写后半截"的匹配串,
    #   official_target_url 拼出来的地址可能缺 runtime/ 那一层)
    if not off:
        cands = official_url_candidates(r.get("pattern"))
        off = cands[0] if cands else ""
    sinfo = (sizes or {}).get(str(r.get("pattern") or "")) or {}
    return {"official": off, "current": cur, "note": note,
            "ready": bool(target_exists(r.get("target"))),
            "official_size": sinfo.get("official") or (0, 0, ""),
            "current_size": sinfo.get("mine") or (0, 0, ""),
            "size_ok": sinfo.get("ok"),
            "advice": sinfo.get("advice") or "",
            "fmt_ok": sinfo.get("fmt_ok"), "fmt_note": sinfo.get("fmt_note") or ""}


def preview_html(rules, base_url="", season="", probe=None, sizes=None):
    """生成「资源预览」网页(整张表并排看官方图 / 你要换的图) → html 文本。

    为什么做成一个网页而不是在 tkinter 里画 360 张图:
      用户问"有预览吗? 全部显示出来能够替换的" —— 要一眼看全, 只能靠浏览器(懒加载 + 缩放)。
      tkinter 里塞 700 张图会卡死, 而且没法放大看。
    probe: {匹配串: "查不到的原因"} —— 在线核对过"服务器上没这张"的, 在卡片上红字标出来。
    sizes: {匹配串: rule_size_info(...)} —— 量过尺寸的, 在卡片上写原图多大 / 你这张多大 /
      会不会变形(用户原话: "那个替换前的尺寸大小, 分辨率, 像素多少? 要不换进去, 好乱都变形了")。
    """
    probe = probe or {}
    base = str(base_url or "").rstrip("/")

    def _img_src(u):
        """把地址变成【浏览器真能加载】的形式: 网络地址照旧, 本地文件路径补成 file:///绝对路径。

        v4.110★ 修「预览全部(网页图墙)」里【自己换的那张图】全是裂图:
          target_preview_url 在拿不到本机图片服务地址时会退回「本地文件路径」, 而预览页是
          file:// 打开的, src 里写裸路径/相对路径浏览器根本取不到 —— 官方那张是 https,
          所以只有自己那张裂, 看起来就像"预览坏了"(其实是有图没显示出来)。
        """
        u = str(u or "")
        if not u:
            return ""
        low = u.lower()
        if u.startswith("//") or low.startswith(("http://", "https://", "file:///", "data:")):
            return u
        try:
            ap = os.path.abspath(_norm_path(u))
        except Exception:
            return u
        return "file:///" + ap.replace("\\", "/")

    ready = sum(1 for r in rules if target_exists(r.get("target")))
    n_mismatch = 0
    rows = []
    for r in rules:
        p = preview_pair(base, r, sizes=sizes)
        kind = str(r.get("kind") or "")
        bad = probe.get(str(r.get("pattern") or ""))
        off_img = ('<img loading="lazy" src="%s" alt="官方原图">' % _img_src(p["official"])
                   if p["official"] else '<div class="noimg">这类没有官方图</div>')
        cur_img = ('<img loading="lazy" src="%s" alt="要换成的图">' % _img_src(p["current"])
                   if p["current"] else '<div class="noimg">%s</div>' % p["note"])
        off_sz = size_text(p["official_size"])
        cur_sz = size_text(p["current_size"])
        sz_line = ""
        if off_sz or cur_sz:
            sz_line = ('<div class="sz">原图 %s%s</div>'
                       % (off_sz or "?", " · 你的图 %s" % cur_sz if cur_sz else ""))
        if p["size_ok"] is False:
            n_mismatch += 1
            sz_line += ('<div class="bad">⚠ 比例不同会被拉伸变形 —— 建议裁成 %s</div>'
                        % off_sz)
        if p["fmt_ok"] is False:
            sz_line += '<div class="bad">⚠ %s</div>' % p["fmt_note"]
        rows.append(
            '<div class="card">'
            '<div class="hd"><b>%s</b> · %s%s</div>'
            '<div class="imgs">'
            '<figure><figcaption>游戏现在加载的</figcaption>%s</figure>'
            '<figure><figcaption>你要换成的</figcaption>%s</figure>'
            '</div>'
            '<div class="pat">%s</div>'
            '<div class="st">%s</div>'
            '%s'
            '</div>' % (
                kind, r.get("name") or "", "",
                off_img, cur_img,
                r.get("pattern") or "",
                ('<span class="bad">已就绪</span>' if p["ready"]
                 else '<span class="warn">待放图</span>')
                + ('&nbsp;<span class="bad">官方查不到: %s</span>' % bad if bad else ""),
                sz_line,
            ))
    return """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>资源替换预览 · %(season)s</title>
<style>
body{background:#1e1e22;color:#e8e8e8;font:13px/1.5 "Microsoft YaHei UI",sans-serif;margin:0;padding:14px}
h1{font-size:16px;margin:0 0 6px}
.hint{color:#9a9a9a;font-size:12px;margin:0 0 12px}
.hint code{background:#2c2c33;padding:1px 4px;border-radius:3px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:10px}
.card{background:#26262c;border:1px solid #3a3a44;border-radius:6px;padding:8px}
.hd{font-size:12px;color:#e0b060;margin-bottom:6px}
.imgs{display:flex;gap:6px}
figure{margin:0;flex:1;text-align:center}
figcaption{font-size:11px;color:#9a9a9a;margin-bottom:2px}
img{width:100%%;height:110px;object-fit:contain;background:#17171b;border-radius:4px}
.noimg{height:110px;display:flex;align-items:center;justify-content:center;color:#777;
  background:#17171b;border-radius:4px;font-size:11px;padding:4px;text-align:center}
.pat{font-size:10px;color:#7fd4ff;word-break:break-all;margin-top:6px}
.st{font-size:11px;margin-top:4px}
.sz{font-size:11px;color:#9fd4a0;margin-top:3px}
.bad{color:#ff8080}.warn{color:#c8a24a}
</style></head><body>
<h1>资源替换预览 · 赛季 %(season)s</h1>
<p class="hint">共 <b>%(total)d</b> 条: 已就绪 <b>%(ready)d</b> 条, 待放图 <b>%(pending)d</b> 条%(mismatch_txt)s。
左边=游戏现在加载的官方图, 右边=你要换成的。<br>
要换哪张: 把图按资源名放进 <code>参考_配置表/资源替换/我的图/</code>
(如 <code>luxun_02.png</code>), 或点界面上的「给这条换图 …」/「挑选官方立绘」。</p>
<p class="hint"><b>尺寸怎么说:</b> 游戏是把贴图【塞进一个写死的矩形】画的, 所以图的
<b>宽高比要和原图一致</b>, 不一致就会被拉伸变形(这就是"换进去好乱"的原因)。
按原图尺寸裁最稳。量过尺寸的卡片下面会写「原图 W×H」, 比例不一样的会标红。<br>
<b>动图/视频:</b> 不行 —— 背景和皮肤在游戏里是<b>画布贴图</b>(Laya 纹理), 换成
<code>gif</code>/<code>webp 动图</code>只会显示第一帧, <code>mp4</code> 更不会被当成图片解码,
那一格会直接变空白。要放视频得往页面里注入播放器, 而整局棋盘都画在同一张画布上,
播放器一盖上去就把游戏挡了, 所以这条路走不通。</p>
<div class="grid">%(rows)s</div>
</body></html>""" % {
        "season": season or "(未填)",
        "total": len(rules),
        "ready": ready,
        "pending": len(rules) - ready,
        "mismatch_txt": ("(其中 <b>%d</b> 条比例不一致, 会被拉伸变形)" % n_mismatch
                         if n_mismatch else ""),
        "rows": "\n".join(rows),
    }


def remote_to_local_url(base_url, target):
    """本地文件 → 本机图片服务地址(形如 http://127.0.0.1:<端口>/img/xxx.png)。

    v4.69★ 为什么要能输出 http:// 而不是 file://:
      · 部分浏览器把指向 file:// 的重定向当成跨域/不安全, 直接拦掉(表现是"规则配了但没生效");
      · 游戏页若是 https, 从 https 跳到 file:// 多数浏览器也会拒绝。
      本机 http 服务两边都好使, 而且图还是同一个文件(不用复制第二份)。
    """
    b = str(base_url or "").rstrip("/")
    if not b:
        return ""
    return "%s/img/%s" % (b, os.path.basename(str(target or "")))


def generate_redirect_files(season=None, custom_rules=None, only_existing=True, base_url=""):
    """生成 {season}_匹配规则.txt/.tsv, 返回写出的路径与规则统计。

    only_existing=True(默认)
        只写「我的图/ 里确实有这张图」的规则。**这是一条修错, 不是为了省事**:
        默认规则有 360+ 条, 而用户通常只放几张图。旧版把 360 条全写进 txt, 用户照着
        "批量导入"之后, 那 350 多个【没有图的】资源全部 404 —— 现象是"一批棋子立绘不显示",
        而用户根本想不到是自己刚导入的规则干的(见 资源替换方案.md 第七节 ③)。
        所以默认只导出准备齐全的那些; 想拿全量名单(比如先看看都有哪些可换)再看 .tsv。

    base_url
        填了就输出 "http://127.0.0.1:port/img/xxx.png" 形态(配合助手自带的图片服务),
        留空则保持旧的本地路径形态。
    """
    os.makedirs(REF_DIR, exist_ok=True)
    os.makedirs(IMG_DIR, exist_ok=True)
    season = norm_season(season) or norm_season(csv_loader.CURRENT_SEASON) or "current"
    rules = build_rules(season, custom_rules)

    def _target_of(r):
        t = str(r.get("target") or "").strip()
        if not t:
            return ""
        if base_url and not is_remote_target(t):
            u = remote_to_local_url(base_url, t)
            if u:
                return u
        return t

    # 这一份是"给人看的全量"(tsv 永远全量 —— 用户就是要看"有哪些能换、还差哪张图"),
    # 下面那一份才是"给扩展吃的"(默认只含已放图的)。
    emitted, skipped = [], []
    for r in rules:
        t = _target_of(r)
        if not t:
            continue
        if only_existing and not target_exists(r.get("target")):
            skipped.append(r)
            continue
        emitted.append((r, t))

    txt = os.path.join(REF_DIR, "%s_匹配规则.txt" % season)
    tsv = os.path.join(REF_DIR, "%s_匹配规则.tsv" % season)
    with io.open(txt, "w", encoding="utf-8") as f:
        f.write("# 资源替换规则(赛季 %s; 由助手「资源替换」页生成)\n" % season)
        f.write("# 格式: 匹配串 <TAB> 目标。匹配串只要「包含」就命中(游戏会在后面拼 ?v=hash)\n")
        f.write("# 本文件默认【只含已经放好图的规则】(%d 条); 还差图的 %d 条写在 .tsv 里\n"
                % (len(emitted), len(skipped)))
        f.write("# 图放在 我的图/ 目录(文件名 = 资源名.png), 或用本机图片服务地址\n")
        for r, t in emitted:
            f.write("%s\t%s\n" % (r["pattern"], t))
    with io.open(tsv, "w", encoding="utf-8-sig") as f:
        f.write("类别\t资源名\t匹配串\t目标\t状态\n")
        for r in rules:
            t = _target_of(r) or str(r.get("target") or "")
            exists = "已就绪" if target_exists(r.get("target")) else "待放图"
            f.write("%s\t%s\t%s\t%s\t%s\n" % (
                r.get("kind", ""),
                r.get("name", ""),
                r.get("pattern", ""),
                t,
                exists,
            ))
    kinds = {}
    for r in rules:
        kinds[r.get("kind", "其他")] = kinds.get(r.get("kind", "其他"), 0) + 1
    ready_kinds = {}
    for r, _t in emitted:
        ready_kinds[r.get("kind", "其他")] = ready_kinds.get(r.get("kind", "其他"), 0) + 1
    cleaned = _clean_alias_files(season, txt, tsv)
    return {
        "txt": txt,
        "tsv": tsv,
        "count": len(emitted),
        "total": len(rules),
        "skipped": len(skipped),
        "kinds": ready_kinds,
        "all_kinds": kinds,
        "season": season,
        "base_url": str(base_url or ""),
        "cleaned": cleaned,
    }


def _clean_alias_files(season, keep_txt, keep_tsv):
    """删掉【同一个赛季的别名文件】—— 只可能是本模块自己生成的 (见 norm_season 的说明)。

    v4.69★ 为什么必须删: 归一化之前, 界面填 s10 会生成 `s10_匹配规则.txt`, 而命令行/别处
      会生成 `10_匹配规则.txt`。两个文件都在目录里、时间戳还更新, 用户按提示去找
      "新的 s10_匹配规则.txt" 时看到的是另一个 —— "到底该导入哪个"每次都成了问题,
      而正确的那个答案(导入错的=一批立绘 404)代价不小。
      所以生成时顺手把同赛季的别名文件清掉, 目录里永远只剩一份。

    安全边界(宁可少删, 不可误删): 只在这个模块自己的输出目录里, 只认
      `{任意}_匹配规则.txt/.tsv` 这个精确形状, 且【赛季归一化后等于当前赛季】的才删;
      其它赛季、其它文件一律不碰。
    """
    gone = []
    try:
        names = os.listdir(REF_DIR)
    except Exception:
        return gone
    # 保留集: 不写 try/except —— basename 对 str/None 都不会抛, 这里包一层只会多一个
    #   "静默 except: pass"(本项目有预算闸门, 那种空 except 是要被点名的新增)
    keep = set(os.path.basename(p) for p in (keep_txt, keep_tsv) if p)
    for fn in names:
        if fn in keep:
            continue
        for ext in (".txt", ".tsv"):
            suffix = "_匹配规则" + ext
            if not fn.endswith(suffix):
                continue
            other = fn[:-len(suffix)]
            if norm_season(other) != season:
                continue
            p = os.path.join(REF_DIR, fn)
            try:
                if os.path.isfile(p):
                    os.remove(p)
                    gone.append(fn)
            except Exception as e:
                sys.stderr.write("[res_rules] 清理旧规则文件失败 %s: %s\n" % (fn, e))
    return gone


def search_rules(rules, keyword, kinds=None):
    """按关键词 / 类型过滤规则(界面那个搜索框用)。

    keyword: 名称、匹配串、目标、类别 四列任一【包含】就留下(大小写不敏感)。
    kinds  : 允许的类别集合(None = 不限); 界面上是"只看棋子立绘/只看背景"这类下拉。
    """
    kw = str(keyword or "").strip().lower()
    out = []
    for r in (rules or []):
        if not isinstance(r, dict):
            continue
        if kinds and str(r.get("kind") or "") not in kinds:
            continue
        if not kw:
            out.append(r)
            continue
        hay = " ".join(str(r.get(k) or "") for k in ("name", "pattern", "target", "kind")).lower()
        if kw in hay:
            out.append(r)
    return out


def rule_from_default(rule):
    """把一条【默认规则】变成可编辑的【自定义规则】(带新 id)。

    v4.69★ 用户原话: "然后不能编辑啊, 默认规则; 那我怎么拿到对应的匹配串?"
      默认规则是每次从赛季配置表现算的, 存回去没有意义(换赛季就过期), 所以界面上不让改。
      但用户真正想做的事是"就拿这一条, 换个目标图" —— 与其让他手工把一长串匹配串抄进
      「添加自定义规则」, 不如给他一个按钮: 点一下就把这条复制成自定义规则, 匹配串原样带过来,
      之后想怎么改怎么改(同名 pattern 的自定义规则会覆盖默认规则, 见 build_rules)。
    """
    r = dict(rule or {})
    r["id"] = "r%d" % int(time.time() * 1000)
    r["season"] = ""
    return r


# ============================================================================
# v4.74★ 官方大图/皮肤资源清单
#   用户原话: "现在本项目中的资源替换里面没有这样的图片啊; 这个是刘备的皮肤 …
#             这些能直接也加到可以替换的资源里面; 这个资源可以获取吧? 新建一个放这个"
# ----------------------------------------------------------------------------
# 清单落在 data/official_resources.json, 两个来源:
#   ① 「扫描游戏已加载资源」: 桥接脚本把页面【真的加载过】的 res/assets URL 报回来
#      (performance.getEntriesByType('resource') + 资源替换 hook 的见闻)。
#      这是"获取"的主路 —— 用户在游戏里逛一圈皮肤/打一局, 清单自己就长出来了;
#   ② 「按编号探测」: 直接对官方地址发 HEAD, 存在才收进清单。用户给的样本
#      500103(刘备皮肤, 935KB) / 621401(神马超) 就是这样验的。
# 为什么不做"枚举": 编号不是连号(500103 / 621401 / 动态版 821401), 盲猜要发成千上万个请求;
#   采集则是零猜测的 —— 采到的每一条都是浏览器真请求过的地址。
# ============================================================================
OFFICIAL_RES_PATH = os.path.join(DATA_DIR, "official_resources.json")
OFFICIAL_RES_KEEP = 800                       # 清单上限, 超了丢"见过次数最少"的

_RES_ASSETS = "/pc/res/assets/"

# 资源家族 → (界面类别, 一句人话)。顺序有意义: 先匹配到的算数。
RES_FAMILIES = (
    ("general/big/dynamic/", "OL大图", "OL 动态皮肤的一帧(动态皮是一帧一个文件)"),
    ("general/big/static/", "OL大图", "OL 大立绘/皮肤大图(静态)"),
    ("tavernChess/general/", "主公立绘", "自走棋主公大图(两条线路都有; v4.75 前误写成 general/maxBig/, 那条已 404)"),
    ("general/maxBig/", "主公立绘", "主公大图的旧写法(服务器已 404, 读到只为了迁移)"),
    ("tavernChess/piece/", "棋子立绘", "棋盘上的棋子立绘"),
    ("tavernChess/card", "武将牌面", "手牌/商店里的武将牌面"),
    ("tavernChess/", "自走棋图集", "整张图集(自走棋界面的图都在里面, 换一张会整片一起变)"),
    ("dress/", "皮肤头像", "皮肤头像/小图"),
    ("voice/", "音效", "语音/音效"),
    ("animate/", "动画帧", "骨骼动画帧(换单帧会错位, 慎用)"),
    ("newStyleBaseUI/", "界面图", "新版界面的图集(整张换, 不是单帧)"),
    ("baseUI/", "界面图", "基础界面图集(整张换)"),
)

# v4.86★ 自走棋背景全家福 —— 用户原话: "能不能改成全部?"。
#
# 为什么必须成套(源码 TavernChessGameScene, 两条分支):
#     TavernChessManager.GetInstance().InHoliday
#       ? "tavernChessHolidayBg"    ← 节日活动期走这张
#       : "tavernChessBg"           ← 平时走这张
#   只换其中一张 = "有时候换成了、有时候又变回官方图" —— 这就是要"全套"的原因。
#   其余三张是同一套命名(选将窗 / 大厅 / 棋盘区底), 想整个自走棋都换就一起选上。
# 顺序有意义: 第一条是主背景(实测原图 2134×1200 ≈ 16:9, 出图按这个比例最稳)。
TAVERN_BG_PRESETS = (
    ("tavernChess/tavernChessBg.png", "棋盘背景(对局里那张大底图)", "2134×1200"),
    ("tavernChess/tavernChessHolidayBg.png", "节日棋盘背景(活动期走这张, 不换就漏)", "2134×1200"),
    # v4.137★ 选将窗背景真身在 newStyleWindow/ —— 以前写成 tavernChess/ 那条(实测 404),
    #   于是"选将背景"这一条一直是死地址: 界面上看得到, 换过去毫无反应。
    ("newStyleWindow/tavernChessSelectGeneralBg.png", "选将窗背景", "整屏"),
    ("tavernChess/tavernChessHallBg.png", "自走棋大厅背景", "整屏"),
    ("tavernChess/tavernChessAreaBg.png", "棋盘区底图", "棋盘区"),
)
# v4.137★ 新版选人界面背景(newStyleTableScene/) —— 用户实测抓到的两张, 以前名单里压根没有,
#   所以"选人页面那张"和"选人页后面那层"一直无处可换。
#   ★ 同上面那批: 这两张也【不带 runtime/】(实测 runtime/ 前缀一律 404)。
NEWSTYLE_BG_PRESETS = (
    ("newStyleTableScene/newStyleTableBg.png", "新版选人页背景", "整屏"),
    ("newStyleTableScene/newStyleTableDeckBg.png", "新版选人页-后层背景(卡组那层)", "整屏"),
)
# v4.86★ 身份场背景(battleBg/) —— 用户问: "2.txt 里那个 battleBg/chengneidenghuo_s.jpg
#   能给身份场换背景吗?" 能: 这是【另一张桌子】的背景(不是自走棋那套), 走同一套规则换,
#   原图 1600×900 的 jpg。data/resource_rules.json 里那两条(城内灯火/吴国背景)就是它。
IDENT_BG_PRESETS = (
    ("battleBg/chengneidenghuo_s.jpg", "身份场背景-城内灯火(桌布/场景底图)", "1600×900"),
    ("battleBg/wuguobeijing_s.jpg", "身份场背景-吴国背景(桌布/场景底图)", "1600×900"),
)

# 「文件名以 Bg 结尾」才是背景(别的 tavernChess*.png 多半是图集, 换一张会整片一起变)。
#   允许 Bg 后面跟数字(tavernChessAreaBg2 / tavernChessHallBg2 这种)。
_TAVERN_BG_RE = re.compile(r"bg\d*\.(png|jpg|jpeg|webp)$", re.I)

# OL 大图的两条真实路径模板(用户现场样本里抄下来的)
BIG_STATIC_URL = OFFICIAL_HOST + "/x/pc/res/assets/runtime/general/big/static/{id}.png"
BIG_DYNAMIC_URL = OFFICIAL_HOST + "/x/pc/res/assets/runtime/general/big/dynamic/{id}/{frame}.png"
# 动态皮肤里那几个固定名字的帧 —— beijing(底图)/daiji(待机)/daiji_2(第二帧)都是用户实测存在
BIG_DYNAMIC_FRAMES = ("beijing", "daiji", "daiji_2", "daiji_3", "zhezhao", "biankuang")
DYNAMIC_ID_OFFSET = 200000                    # 821401 = 动态版的 621401(现场样本推出来的规律)

# v4.111★ 帧名不再写死 —— 官方皮肤表里【每套的帧名单都不一样】, 从官方配置里抄回来的实测样例:
#     beijing|daiji                        最简(底图 + 待机)
#     beijing|daiji|daiji_zf
#     beijing|daiji_cp|daiji_zj|daiji
#     beijing|daiji_hyy|daiji_zgl|daiji_zgz   4 帧
#   规律: 必有 beijing(底图) + daiji(待机), 其余是 daiji 的变体(daiji_XX, XX 为 2~4 字缩写)
#   或分页(daiji_2 / daiji_3)。写死 6 个帧名的下场是: 换过去以后缺帧(动画少人/少部件)。
DAIJI_VARIANTS = ("sq", "lb", "zf", "cp", "zj", "ghh", "zxc", "cc", "kr", "lx", "zgl", "zgz",
                  "myl", "cr", "zch", "hyy", "tx", "lm", "zy", "sd", "cl", "long", "xq", "smy",
                  "dq", "xhs")
DYN_BASE_FRAMES = ("beijing", "daiji")
DYN_PAGE_FRAMES = ("daiji_2", "daiji_3", "daiji_4")
# 骨架有两代后缀: 新皮肤 .skel, 老皮肤 .sk(实测两套都存在, 只试一种会整套探不出来)
DYN_SKEL_EXTS = (".skel", ".sk")


def dyn_frame_candidates():
    """这套动态皮【可能有哪些帧】—— 基础帧 + 分页 + 官方实测过的变体。给探测用(不联网)。"""
    out, seen = [], set()
    for fr in (DYN_BASE_FRAMES + DYN_PAGE_FRAMES + tuple("daiji_" + v for v in DAIJI_VARIANTS)):
        if fr in seen:
            continue
        seen.add(fr)
        out.append(fr)
    return out

# v4.110★ 动态皮「整套」清单 —— 用户原话: "换动态皮: 必须弄到整套 skel+atlas+png"。
#
# 实测(dynamic/7170101/ 这一套, 7 个文件):
#     beijing.skel 131KB / beijing.atlas 4.8KB / beijing.png 4.16MB / beijing_2.png 2.1MB
#     daiji.skel   451KB / daiji.atlas   15KB  / daiji.png   4.06MB     (daiji_2.png 是 404)
# 三件套各管一摊:
#     .skel   骨架(二进制): 骨头 / 插槽 / 动画时间轴 —— 一个像素都没有
#     .atlas  切片表(纯文本): "哪块图在大图的哪个坐标、多大、转了几度"(见 daiji.atlas 头几行)
#     .png    贴图(图集): 真正的像素, 2048×2048 起步; 一张装不下就分页(_2 / _3)
# 为什么只换 png 一定是错位碎块: atlas 里的坐标是给【自家 png】用的, skel 里的插槽名是给
#   【自家 atlas】用的。png 换成别人的图之后, 游戏仍按原 atlas 那个刀法去切 → 切出来全是
#   别人的部位。整套指向【同一个目标编号】时三件套彼此自洽, 出来就是另一套完整动画。
DYN_SUITE_ROLES = ((".skel", "骨架"), (".atlas", "切片表"), (".png", "贴图"))
DYN_SUITE_PAGES = (("", ""), ("_2", "第2页"), ("_3", "第3页"))   # 只有贴图会分页


def _dyn_url(num, fn):
    """动态皮目录里一个文件的完整地址。"""
    return "%s/x/pc/res/assets/runtime/general/big/dynamic/%s/%s" % (OFFICIAL_HOST, num, fn)


def _probe_many(cands, timeout=8.0, workers=8):
    """并发问官方"这些地址在不在" → 只回活着的(带 size)。单个地址炸了不影响其余。"""
    def _one(c):
        try:
            info = probe_res_url(c["url"], timeout=timeout)
        except Exception as e:
            info = {"ok": False, "size": 0, "note": str(e)}
        d = dict(c)
        d["ok"] = bool(info.get("ok"))
        d["size"] = int(info.get("size") or 0)
        d["note"] = str(info.get("note") or "")
        return d

    alive = []
    try:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(workers, max(1, len(cands)))) as ex:
            for d in ex.map(_one, cands):
                if d["ok"]:
                    alive.append(d)
    except Exception:                          # 没有线程池也别让功能挂掉
        for c in cands:
            d = _one(c)
            if d["ok"]:
                alive.append(d)
    return alive


def dyn_suite_files(num, frames=None, skelext=".skel"):
    """动态皮编号 → 这套"可能有"的文件清单 → [{file,frame,ext,role,url}]。不联网, 只拼名字。

    skelext: 骨架后缀(老皮肤是 .sk, 新的是 .skel), 由 probe_dyn_suite_full 先问出来;
      不传就按 .skel 拼(够大多数新皮肤用)。
    """
    s = "".join(ch for ch in str(num or "") if ch.isdigit())
    if not s:
        return []
    out, seen = [], set()
    roles = ((skelext, "骨架"), (".atlas", "切片表"), (".png", "贴图"))
    for fr in (frames or BIG_DYNAMIC_FRAMES):
        for ext, role in roles:
            pages = DYN_SUITE_PAGES if ext == ".png" else (("", ""),)
            for pg, pgnote in pages:
                fn = "%s%s%s" % (fr, pg, ext)
                if fn in seen:                 # daiji_2 既是帧名又是分页, 去重
                    continue
                seen.add(fn)
                out.append({"file": fn, "frame": fr, "ext": ext.lstrip("."),
                            "role": role if not pgnote else "%s(%s)" % (role, pgnote),
                            "url": _dyn_url(s, fn)})
    return out


def probe_dyn_suite_full(num, timeout=8.0):
    """v4.111★ 把一套动态皮的【真实文件】全找出来(不再靠写死的帧名单猜)。

    三阶段, 逐步收窄, 免得对每个猜测都发一次请求:
      ① 问 daiji.skel / daiji.sk → 定这套用哪一代骨架后缀(老皮是 .sk, 不然整套探空);
      ② 拿这个后缀去问【每个候选帧】的骨架 → 骨架在, 这一帧才存在;
      ③ 存在的帧再补 .atlas / .png / 分页(_2 _3) —— 贴图才分页, 骨架切片表不分页。
    """
    s = "".join(ch for ch in str(num or "") if ch.isdigit())
    if not s:
        return []

    # ① 定后缀
    skelext = ".skel"
    hit = _probe_many([{"file": "daiji" + e, "frame": "daiji", "ext": e.lstrip("."),
                        "role": "骨架", "url": _dyn_url(s, "daiji" + e)}
                       for e in DYN_SKEL_EXTS], timeout, workers=4)
    if hit:
        skelext = "." + hit[0]["ext"]

    # ② 哪些帧真存在(每帧问一次骨架)
    fprobe = [{"file": fr + skelext, "frame": fr, "ext": skelext.lstrip("."), "role": "骨架",
               "url": _dyn_url(s, fr + skelext)} for fr in dyn_frame_candidates()]
    fhit = _probe_many(fprobe, timeout)
    if not fhit:
        return []
    frames = [d["frame"] for d in fhit]

    # ③ 存在的帧补切片表 / 贴图 / 分页
    cands, seen = [], {d["file"] for d in fhit}
    for fr in frames:
        for ext, role in ((".atlas", "切片表"), (".png", "贴图")):
            pages = DYN_SUITE_PAGES if ext == ".png" else (("", ""),)
            for pg, pgnote in pages:
                fn = "%s%s%s" % (fr, pg, ext)
                if fn in seen:
                    continue
                seen.add(fn)
                cands.append({"file": fn, "frame": fr, "ext": ext.lstrip("."),
                              "role": role if not pgnote else "%s(%s)" % (role, pgnote),
                              "url": _dyn_url(s, fn)})
    alive = fhit + _probe_many(cands, timeout)
    alive.sort(key=lambda x: (x["frame"], x["ext"], x["file"]))
    return alive


def probe_dyn_suite(num, timeout=8.0, files=None):
    """问官方服务器: 这套动态皮里哪些文件真存在 → 只留活着的(带 size)。

    files: 只探这几个文件名 —— 两阶段探测用(先探源, 再拿源的文件名去问目标有没有),
      省掉一半请求, 也保证"目标那边有没有"这个问题问的是源文件, 不会答非所问。
    不给 files 时走 probe_dyn_suite_full: 帧名自己探出来, 不靠写死的名单。
    复用 probe_res_url: skel/atlas 不是图片(量不出宽高), 但 HEAD 的状态码和大小照样准。
    """
    s = "".join(ch for ch in str(num or "") if ch.isdigit())
    if not s:
        return []
    if not files:
        return probe_dyn_suite_full(s, timeout)

    ext_role = {".skel": "骨架", ".sk": "骨架", ".atlas": "切片表", ".png": "贴图"}
    cand, seen = [], set()
    for f in files:
        f = str(f)
        if f in seen:
            continue
        seen.add(f)
        base, _, ext = f.rpartition(".")
        role = ext_role.get("." + ext, "")
        if "_2." in f or "_3." in f:
            role = "贴图(第%s页)" % f.rsplit("_", 1)[-1].split(".")[0]
        cand.append({"file": f, "frame": base, "ext": ext, "role": role, "url": _dyn_url(s, f)})
    alive = _probe_many(cand, timeout)
    alive.sort(key=lambda x: (x["frame"], x["ext"], x["file"]))
    return alive


_DYN_ID_RE = re.compile(r"/dynamic/(\d+)/")
_STATIC_ID_RE = re.compile(r"/static/(\d+)\.png")


def dyn_id_of_ref(ref, timeout=6.0):
    """皮肤表里挑出来的一条(pattern / 完整 URL) → 这套的【动态目录编号】。

    用户原话: "你不能找到官方的那个图吗? OL 大图里带动态帧的都有这个" —— 能:
      动态帧的地址本身就是 `…/big/dynamic/7220501/daiji.png`, 目录编号就写在里头,
      挑一套就等于把编号抄回来了, 不用人肉记数字。

    挑到的是【静态大图】(…/static/5220501.png)时编号要推: 实测 5220501 的动态版在
      7220501(+2000000), 老样本 621401 的动态版在 821401(+200000) —— 两个偏移都试,
      并且【真去问一句】那个目录下有没有 daiji.png/beijing.png: 猜出来的编号配出去就是 404。
    都探不到就返回 ""(这套压根没有动态版), 由调用方提示换一套。
    """
    s = str(ref or "").strip()
    if not s:
        return ""
    m = _DYN_ID_RE.search(s)
    if m:
        return m.group(1)
    m = _STATIC_ID_RE.search(s)
    if not m:
        return ""
    try:
        no = int(m.group(1))
    except Exception:
        return ""
    cands = [no + 2000000, no + DYNAMIC_ID_OFFSET, no - 2000000, no - DYNAMIC_ID_OFFSET]
    tried = set()
    for d in cands:
        d = int(d)
        if d <= 0 or d in tried:
            continue
        tried.add(d)
        if probe_dyn_suite(str(d), timeout=timeout, files=("daiji.png", "beijing.png")):
            return str(d)
    return ""


def res_ref(s):
    """任意写法 → (线路, 相对路径); 认不出资源路径时返回 ("", 原样)。

    容忍三种输入, 因为这三处都会往这里塞东西:
      · 完整 URL(`https://web.sanguosha.com/x/pc/res/assets/…`);
      · 被脚本 slice(-70/-100) 截过头的尾巴(`anguosha.com/x/pc/res/assets/…`);
      · 界面上那种相对匹配串(`runtime/tavernChess/piece/luxun.png`)。
    为什么要保留线路: 棋子立绘/OL大图走 `x/pc/`, 主公大图走 `10/pc/` —— 拼完整 URL 时
      要按原线路拼回去, 否则复制出来的地址是 404。
    """
    t = str(s or "").strip().strip('"\'')
    if not t:
        return "", ""
    i = t.find(_RES_ASSETS)
    if i < 0:
        # 相对形态: 顺手剥掉 query(`daiji.png?v=7cb5488811` → `daiji.png`) —— 脚本是按子串
        #   匹配 URL 的, 带上 ?v= 反而可能匹配不上(还容易在"去掉版本号"这件事上踩坑)
        return "", t.strip("/").split("?")[0].split("#")[0]
    head, rel = t[:i], t[i + len(_RES_ASSETS):].strip("/")
    rel = rel.split("?")[0].split("#")[0].strip()
    # 注意: 切点是 "/pc/res/assets/", 所以 head 只剩 `…/10`(`/pc` 那一段划给了 rel 的前缀判断)
    hd = head.rstrip("/")
    line = "10" if (hd.endswith("/10") or hd.endswith("/10/pc")) else "x"
    return line, rel


def res_full_url(rel, line=""):
    """相对路径 → 官方完整 URL(线路给不出时按家族兜底: 主公大图走 10, 其余 x)。"""
    r = str(rel or "").strip().lstrip("/")
    if not r:
        return ""
    ln = str(line or "")
    if ln not in ("x", "10"):
        ln = "10" if (r.startswith("general/maxBig/") or r.startswith("tavernChess/general/")) else "x"
    return "%s/%s/pc/res/assets/%s" % (OFFICIAL_HOST, ln, r)


def res_kind(rel):
    """相对路径 → (界面类别, 一句人话)。"""
    r = str(rel or "")
    # v4.86★ 自走棋里那些 *Bg.png(棋盘 bg / 节日 holidayBg / 大厅 hallBg / 选将 selectGeneralBg)
    #   是【单张底图】, 不是整张图集 —— 以前被归进「自走棋图集 / 换一张会整片一起变」,
    #   那句提示是错的(它只会换那一处)。
    if "tavernChess" in r and _TAVERN_BG_RE.search(r):
        return "自走棋背景", "自走棋场景底图 —— 换一张只换那一处"
    for fam, kind, note in RES_FAMILIES:
        if fam in r:
            return kind, note
    return "其他", "没归类的资源(匹配串自己写)"


def looks_like_bg(rel):
    """像不像"背景/场景/棋盘"图 —— 候选列表靠它把用户最想要的那批排前面。"""
    return bool(re.search(r"(bg|background|beijing|scene|board|qipan|tavernchess|maxbig)",
                          str(rel or ""), re.I))


def load_official_resources():
    """读官方资源清单 → 规则化后的 list(文件坏了/没有都返回 [])。"""
    d = _load_json(OFFICIAL_RES_PATH, {})
    items = d.get("items") if isinstance(d, dict) else d
    out = []
    for it in (items if isinstance(items, list) else []):
        if not isinstance(it, dict):
            continue
        rel = str(it.get("pattern") or "")
        if not rel:
            _line0, rel = res_ref(it.get("url") or "")
        if not rel:
            continue
        kind, note = res_kind(rel)
        it = dict(it)
        it["pattern"] = rel
        it["url"] = str(it.get("url") or "") or res_full_url(rel, it.get("line"))
        it["kind"] = str(it.get("kind") or "") or kind
        it["note"] = str(it.get("note") or "") or note
        out.append(it)
    return out


def save_official_resources(items):
    """写清单(顺带排序: 见过多的、最近见过的靠前)。失败不影响这次使用。"""
    it = sorted(list(items or []), key=lambda x: (-int(x.get("seen") or 0),
                                                  -int(x.get("last") or 0)))
    _save_json(OFFICIAL_RES_PATH, {"v": "4.74", "updated": int(time.time()), "items": it})
    return it


def merge_official_resources(urls, source="scan", keep=None):
    """把采集/探测到的 URL 并进清单 → {"added","total","items"}。

    urls 里允许混各种写法(完整 URL / 半截域名 / 相对匹配串), 按相对路径去重;
      已经有的只累加 seen(用来看"这个资源每次进游戏都在加载"—— 说明它是常驻资源,
      最适合拿来做替换)。
    """
    now = int(time.time())
    items = load_official_resources()
    index = {str(x.get("pattern") or ""): x for x in items}
    added = []
    for u in (urls or []):
        meta = {} if isinstance(u, str) else dict(u or {})
        line, rel = res_ref(u if isinstance(u, str) else (meta.get("url") or ""))
        if not rel:
            continue
        low = rel.lower()
        if not low.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp3", ".ogg", ".m4a")):
            continue
        if low.endswith((".atlas", ".json", ".sk", ".bin", ".dat")):
            continue
        if rel in index:
            it = index[rel]
            it["seen"] = int(it.get("seen") or 0) + 1
            it["last"] = now
            if line and not it.get("line"):
                it["line"] = line
            # 「按编号探测」会带上量到的宽高/大小, 顺手补进已有条目
            for k in ("w", "h", "size"):
                if meta.get(k):
                    it[k] = meta[k]
            continue
        kind, note = res_kind(rel)
        it = {"pattern": rel, "line": line or "", "url": res_full_url(rel, line),
              "kind": kind, "note": str(meta.get("note") or note),
              "src": str(source or "scan"),
              "seen": 1, "first": now, "last": now,
              "size": int(meta.get("size") or 0), "w": int(meta.get("w") or 0),
              "h": int(meta.get("h") or 0)}
        index[rel] = it
        items.append(it)
        added.append(it)
    n_keep = int(keep or OFFICIAL_RES_KEEP)
    if len(items) > n_keep:
        items = sorted(items, key=lambda x: (-int(x.get("seen") or 0),
                                             -int(x.get("last") or 0)))[:n_keep]
    save_official_resources(items)
    return {"added": len(added), "total": len(items), "items": added}


_AUDIO_PAT_RE = re.compile(r"\.(mp3|ogg|oga|m4a|wav)(\?|#|$)", re.I)


def is_audio_pattern(p):
    """匹配串是不是音频(音效/语音) —— 建规则时靠它决定"让你选图"还是"让你选音频"。

    v4.86★ 以前界面里选文件只给图片后缀, 于是 voice/ 那批 mp3 虽然扫得到、也建得了规则,
      却根本选不进音频文件 —— 「语音/音效替换」这条路等于没有入口(用户原话:
      "语音/音效这条路完全没入口")。现在按匹配串的后缀自动切换选择器的类型。
    """
    return bool(_AUDIO_PAT_RE.search(str(p or "")))


def upsert_rules(new_rules, season=""):
    """把一批规则并进 resource_rules.json(同 pattern 覆盖) → (总条数, 新建, 覆盖)。

    为什么单开一个函数: 「自走棋背景全套」一次要落 5 条, 而界面里那套"去掉同名再 append"
      是一次一条 —— 写 5 遍就是 5 次全量重写, 而且落一半时崩了会留下半成品。
      一次收口还顺带给了 dev 自检一个可以直接调的入口。
    """
    rules = load_rules()
    idx = {}
    for i, x in enumerate(rules):
        idx[str(x.get("pattern") or "")] = i
    added = 0
    for r in (new_rules or []):
        p = str(r.get("pattern") or "").strip()
        if not p:
            continue
        r = dict(r)
        if season and not r.get("season"):
            r["season"] = season
        if p in idx:
            rules[idx[p]] = r
        else:
            idx[p] = len(rules)
            rules.append(r)
            added += 1
    save_rules(rules)
    n = len([x for x in (new_rules or []) if str(x.get("pattern") or "").strip()])
    return len(rules), added, n - added


def probe_res_url(url, timeout=8.0):
    """探测一个官方资源地址 → {"ok","w","h","fmt","size","note"}。

    复用的是「核对官方图 / 量尺寸」那两个现成实现(HEAD 问存在 + 只下前 4KB 读宽高),
      所以这里的结论和表格里那两列是同一套口径, 不会出现"清单说有、表格说查不到"。
    """
    u = str(url or "").strip()
    if not u:
        return {"ok": False, "w": 0, "h": 0, "size": 0, "note": "没有地址"}
    alive, detail = official_alive(u, timeout)
    if not alive:
        return {"ok": False, "w": 0, "h": 0, "size": 0, "note": detail}
    size = 0
    m = re.search(r"([\d.]+)\s*KB", str(detail or ""))
    if m:
        try:
            size = int(float(m.group(1)) * 1024)
        except Exception:
            size = 0
    (w, h, fmt), note2 = remote_image_size(u, timeout)
    return {"ok": True, "w": w, "h": h, "fmt": fmt, "size": size,
            "note": detail if (w and h) else "%s; %s" % (detail, note2)}


def big_id_candidates(num, with_neighbors=True):
    """一个编号 → 该编号下"可能有"的官方地址(静态大图 + 动态皮肤的常见帧)。

    用户现场样本(刘备 500103/500104、神马超 621401、它的动态版 821401 的
      beijing.png / daiji.png / daiji_2.png)说明:
        · 静态大图: general/big/static/{编号}.png
        · 动态皮肤: general/big/dynamic/{编号+200000}/{帧名}.png, 帧名只有那么几个
    所以「按编号探测」就按这两条先试, 存在哪条就把哪条收进清单 —— 不猜名字、不猜内容。
    with_neighbors: 顺带探同一武将的连号(500103 → 500104, 用户给的样本正是这么挨着的)。
      界面上默认关掉 —— 一次探 7 个地址已经要好几秒, 再加 2 个不划算。
    """
    s = re.sub(r"[^\d]", "", str(num or ""))
    if not s:
        return []
    out = [BIG_STATIC_URL.format(id=s)]
    dyn = str(int(s) + DYNAMIC_ID_OFFSET)
    for fr in BIG_DYNAMIC_FRAMES:
        out.append(BIG_DYNAMIC_URL.format(id=dyn, frame=fr))
    if with_neighbors:
        for d in (1, 2):
            out.append(BIG_STATIC_URL.format(id=str(int(s) + d)))
    return out


def diagnostics_candidates(limit=400):
    """从诊断包里捞出【真实请求过】的资源, 供「用作背景 / 建规则」挑选。

    v4.73 以前的实现是错的, 用户现场就是"点「用作背景」配好了、游戏里毫无变化":
      它遍历 full.atlasFrames, 把【图集内部的帧名】当成独立文件, 拼成
      `…/newStyleBaseUI/generalBaseUIChessGradeBg.png` 这种地址。而游戏加载的是
      `newStyleBaseUI.atlas` + `newStyleBaseUI.png` 整张图集, 永远【不会单独请求帧名】——
      那批候选 100% 是死地址; 而且 atlasFrames 的键是脚本里 slice(-70) 留下的尾巴
      (`ttps://web.sanguosha.com/…`), 连拼出来的域名都是残的。
    现在只从"真实 URL"取, 三处:
      ① full.resUrls —— v4.74 起脚本用 performance.getEntriesByType('resource') 采的
         "页面真的加载过哪些资源"(最全, 含 hook 覆盖不到的加载方式);
      ② full.netEvts 里 `loaded:http…` 那几条(老脚本一直在报);
      ③ data/official_resources.json —— 用户在「官方大图/皮肤资源」区扫过/探测过的。
    返回 [{"url","pattern","kind","note","tags","score"}]: 界面显示 url(完整, 好认),
      建规则用 pattern(不含域名的相对串, x/10 两条线路都能命中)。
    """
    refs = {}

    def _add(s):
        t = str(s or "")
        if not t:
            return
        # 别的域名(如 yjcmgg…/api/list)不是游戏资源; 只有带资源目录的完整 URL 或相对串才收
        if "://" in t and _RES_ASSETS not in t:
            return
        line, rel = res_ref(t)
        if not rel:
            return
        low = rel.lower()
        # 图集/配置/音频不是"一张图": 换它们是另一套流程, 不进候选
        if low.endswith((".atlas", ".json", ".sk", ".bin", ".dat", ".mp3", ".ogg")):
            return
        if not low.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
            return
        if rel not in refs or (line and not refs[rel]):
            refs[rel] = line

    d = _load_json(os.path.join(DATA_DIR, "diagnostics.json"), [])
    for item in (d if isinstance(d, list) else []):
        if not isinstance(item, dict):
            continue
        full = item.get("full") or {}
        for u in (full.get("resUrls") or []):          # ① v4.74 新采集点
            _add(u)
        for e in (full.get("netEvts") or []):          # ② 老的网络事件
            if isinstance(e, str) and "loaded:http" in e:
                _add(e.split("loaded:", 1)[1])
        js = full.get("jsons")                         # ③ 真实请求过的 .atlas/.json 等
        keys = []
        if isinstance(js, dict):
            keys = list(js)
        elif isinstance(js, list):
            keys = [(x.get("k") or x.get("url") or "") for x in js if isinstance(x, dict)]
        for k in keys:
            s = str(k or "")
            if s.lower().endswith(".atlas"):
                # 图集是一对: `xxx.atlas` + `xxx.png`。xxx.png 才是真正的图, 而且它是
                #   【独立请求】的(脚本能命中) —— 所以拿 .png 那一半当候选才有意义。
                #   (换它 = 整张图集一起换, 界面上会按"界面图"标注)
                _add(s[:-len(".atlas")] + ".png")
                continue
            _add(s)
    for it in load_official_resources():               # ④ 用户扫过/探测过的官方资源
        _add(it.get("url") or it.get("pattern") or "")

    out = []
    for rel, line in refs.items():
        kind, note = res_kind(rel)
        tags = [kind]
        score = 0
        if looks_like_bg(rel):
            tags.append("可能是背景")
            score += 10
        if kind in ("OL大图", "主公立绘"):
            score += 6
        if kind == "棋子立绘":
            score += 4
        if rel.lower().endswith(".png"):
            score += 2
        out.append({"url": res_full_url(rel, line), "pattern": rel,
                    "kind": kind, "note": note, "tags": tags, "score": score})
    out.sort(key=lambda x: (-x["score"], x["url"]))
    return out[:int(limit or 400)]


def validate_rule(r):
    """返回 (ok, msg)。只检查格式, 不检查 target 是否已放图。"""
    if not isinstance(r, dict):
        return False, "规则必须是字典"
    pat = str(r.get("pattern") or "").strip()
    if not pat:
        return False, "匹配串不能为空"
    target = str(r.get("target") or "").strip()
    if not target:
        return False, "目标文件不能为空"
    return True, ""


def import_from_tsv(path):
    """从 tsv 导入规则(只导入自定义规则部分, 不含默认生成的目标目录)。"""
    if not os.path.exists(path):
        return []
    import csv as _csv
    rules = []
    seen = set()
    with io.open(path, encoding="utf-8-sig") as f:
        reader = _csv.DictReader(f, delimiter="\t")
        for row in reader:
            kind = (row.get("类别") or "").strip()
            name = (row.get("资源名") or "").strip()
            pat = (row.get("匹配串") or "").strip()
            target = (row.get("目标文件") or "").strip()
            if not pat or pat in seen:
                continue
            seen.add(pat)
            rules.append({
                "id": "r%d" % int(time.time() * 1000 + len(rules)),
                "name": name or pat,
                "kind": kind or "其他",
                "pattern": pat,
                "target": target,
                "enabled": True,
                "season": "",
            })
    return rules


if __name__ == "__main__":
    # 简单自测: 生成当前赛季规则
    info = generate_redirect_files()
    print("season=%s count=%d" % (info["season"], info["count"]))
    print("txt:", info["txt"])
    print("tsv:", info["tsv"])
    print("kinds:", info["kinds"])

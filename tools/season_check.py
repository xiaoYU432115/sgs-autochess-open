# -*- coding: utf-8 -*-
"""赛季体检 —— 拿【本季配置表】对着【助手现在真正在用的数据】逐项比, 告诉你"换季该做什么"。

为什么需要它(用户问"还有什么可以帮助我更新赛季、调整的工具吗?"):
  `tools/season_config.py` 负责"把新表抓下来并推给助手"; 但**什么时候该推、推完还差什么**,
  以前只能靠人记 —— 而"忘了推"的表现全都是【静默出错】, 不是报错:
    · lords.json 没更新 → 复盘里主公名/ID 对不上(用户已经踩过一次);
    · 随征名单没补 → 新的随征卡被当普通卡, 静默不随征;
    · ChessConf.csv 没更新 → 找卡失败 / 技能解析不到;
    · 资源规则没重生成 → 换立绘时新棋子没有对应规则;
    · 经济参数变了(官方调价) → 助手/脚本里的算法口径就旧了。
  这个工具就是把这些"该做但没人提醒"的事, 变成一份每次换季跑一遍的检查清单。

用法:
  python tools/season_check.py               # 本季体检(只读, 不改任何东西)
  python tools/season_check.py --md          # 额外写一份 参考_配置表/赛季体检_{season}.md
  python tools/season_check.py --season s11  # 指定赛季(默认当前)
退出码: 0 = 没有 ⚠(只有提醒不影响助手可用); 1 = 有 ⚠ 必须处理项
        —— 可直接用于"换季前后各跑一次"的对比。
"""
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Windows 中文控制台默认 GBK, 而本报告要打 ✓ / ⚠ / ℹ —— 不切成 utf-8 的话会在 print 那一步
# 直接 UnicodeEncodeError 崩掉(管道/重定向时尤其必现), 体检结果一个字都看不到 ——
# 等于"换季检查"这个工具从来没真正跑起来过。所以一进来就把输出编码钉死。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception as _enc_err:
    _enc_warn = _enc_err   # 切不过来也不致命; 真崩在 print 时看得到是编码的锅, 不是体检逻辑的锅

from core import resource_rules as RR

ISSUES = []      # [(等级, 标题, 明细)]
OKS = []


def issue(level, title, detail=""):
    ISSUES.append((level, title, detail))


def good(title, detail=""):
    OKS.append((title, detail))


def _load_json(p, default=None):
    import json
    try:
        with io.open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else None


def _ids_of(rows, keys=("ChessID", "SpellID", "GeneralID", "ID", "id")):
    out = set()
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        for k in keys:
            if r.get(k):
                out.add(str(r[k]))
                break
    return out


def _csv_ids(path, col=0):
    """读 CSV 的第一列(ChessConf.csv/ChessSpellConf.csv 的 ID 列就是第 0 列)。"""
    out = set()
    if not os.path.exists(path):
        return out
    with io.open(path, encoding="utf-8", errors="ignore") as f:
        head = True
        for line in f:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            if head:
                head = False
                continue
            parts = re.split(r"[\t,]", line)
            if parts and parts[0].strip():
                out.add(parts[0].strip())
    return out


def unknown_followup_ids(fu_all, skill_ids, fu_ids):
    """随征名单里【既不是随征棋子、也不是本季技能ID】的条目 = 认不出的残留。

    为什么不能只比棋子ID: 名单允许两种语义(见 docs/v4.68), v4.68 起脚本两头都认 ——
      · 棋子ID 如 20904231(黄巾兵, 来自 ChessBuffFollowUpConf);
      · 技能ID 如 942311(黄巾兵技能「随征」, 由『技能库』的"加进随征名单"按钮灌进来)。
    只按棋子ID 判, 942311 会被误报成"已失效" —— 用户照提示删掉, 他的随征监控就真坏一条。
    """
    return [i for i in sorted(fu_ids) if i not in fu_all and i not in skill_ids]


def main():
    args = sys.argv[1:]
    season = args[args.index("--season") + 1] if "--season" in args else None
    from core import csv_loader
    cfg = csv_loader.season_config(season)
    if not cfg:
        print("读不到赛季配置表 —— 先跑 tools/season_config.py fetch")
        return 2
    path = ""
    for p in csv_loader._season_config_paths(season):
        if os.path.exists(p):
            path = p
            season = os.path.basename(os.path.dirname(p)) or season or "current"
            break
    print("赛季: %s   配置表: %s" % (season, path.replace("\\", "/")))

    # ---- ① 棋子: 表 vs ChessConf.csv(助手用来找卡/解析技能的就是它) ----
    t_ids = _ids_of(cfg.get("ChessConf"))
    c_ids = _csv_ids(os.path.join(ROOT, "ChessConf.csv"))
    if not c_ids:
        issue("⚠", "ChessConf.csv 读不到/为空", "助手找卡会失败 —— 跑 tools/season_config.py csv 重新导出")
    else:
        miss = sorted(t_ids - c_ids)
        extra = sorted(c_ids - t_ids)
        if miss:
            issue("⚠", "棋子表里有 %d 个 ID 不在现行 ChessConf.csv 里" % len(miss),
                  "前 12 个: " + ", ".join(miss[:12]) + " → 跑 `season_config.py csv` 导出后再推送")
        else:
            good("棋子: 配置表 %d 个 ID 全在现行 ChessConf.csv 里" % len(t_ids))
        if extra:
            issue("ℹ", "现行 ChessConf.csv 有 %d 个 ID 已不在本季表里(旧赛季残留, 通常无害)" % len(extra),
                  "前 12 个: " + ", ".join(extra[:12]))

    # ---- ② 主公: 表 vs data/lords.json(复盘主公名/ID 就靠它) ----
    lords_cfg = {str(r.get("GeneralID")): str(r.get("GeneralName") or "")
                 for r in (cfg.get("ChessGeneralConf") or []) if isinstance(r, dict) and r.get("GeneralID")}
    lords_cur = _load_json(os.path.join(ROOT, "data", "lords.json"), {}) or {}
    if not lords_cfg:
        issue("⚠", "配置表里没有 ChessGeneralConf", "主公名只能靠旧表 —— 换季后容易对不上")
    else:
        lost = [k for k in lords_cfg if k not in lords_cur]
        diff = [(k, lords_cur[k].get("n"), lords_cfg[k]) for k in lords_cfg
                if k in lords_cur and isinstance(lords_cur[k], dict)
                and str(lords_cur[k].get("n") or "") != lords_cfg[k]]
        if lost:
            issue("⚠", "本季 %d 个主公不在 data/lords.json 里" % len(lost),
                  "".join("  %s=%s\n" % (k, lords_cfg[k]) for k in lost[:12]))
        if diff:
            issue("⚠", "data/lords.json 里 %d 个主公名与本季表不一致(复盘会显示错名字)" % len(diff),
                  "".join("  %s: 现行=%s → 本季=%s\n" % (k, a, b) for k, a, b in diff[:12]))
        if not lost and not diff:
            good("主公: %d 个 ID/名字与 data/lords.json 完全一致" % len(lords_cfg))

    # ---- ③ 锦囊: 表 vs ChessSpellConf.csv ----
    s_ids = _ids_of(cfg.get("ChessSpellConf"))
    cs_ids = _csv_ids(os.path.join(ROOT, "ChessSpellConf.csv"))
    if not cs_ids:
        issue("⚠", "ChessSpellConf.csv 读不到/为空", "锦囊找卡会失败 —— 跑 tools/season_config.py csv 重新导出")
    else:
        smiss = sorted(s_ids - cs_ids)
        if smiss:
            issue("⚠", "锦囊表里有 %d 个 ID 不在现行 ChessSpellConf.csv 里" % len(smiss),
                  "前 12 个: " + ", ".join(smiss[:12]))
        else:
            good("锦囊: 配置表 %d 个 ID 全在现行 ChessSpellConf.csv 里" % len(s_ids))

    # ---- ④ 随征: 表 vs data/lists/followup.json(漏了 = 静默不随征) ----
    # 注意: followup.json 是**用户自己的名单**(和 whitelist/blacklist 同族), 不是全量表;
    #       所以"名单里没有"本身不算错 —— 只有【本季新增的随征棋子】和【已经不是随征的残留】
    #       才值得提醒。老赛季的随征 ID(例如 S9 的 20906321 战马)不算问题。
    fu_rows = [r for r in (cfg.get("ChessBuffFollowUpConf") or []) if isinstance(r, dict)]
    fu_all = []          # 表里所有随征 ID(含旧赛季)
    for r in fu_rows:
        sid = str(r.get("ChessID") or "")
        if sid and sid not in fu_all:
            fu_all.append(sid)
    chess_season = {str(r.get("ChessID")): r.get("SeasonID")
                    for r in (cfg.get("ChessConf") or [])
                    if isinstance(r, dict) and r.get("ChessID")}
    chess_name = {str(r.get("ChessID")): (r.get("ChessName") or "")
                  for r in (cfg.get("ChessConf") or [])
                  if isinstance(r, dict) and r.get("ChessID")}
    cur_season = _load_json(os.path.join(ROOT, "data", "season", "season.json"), {}) or {}
    cur_season = str(cur_season.get("season") or "").lstrip("sS")
    fu_this = [i for i in fu_all if not cur_season or str(chess_season.get(i, "")) == cur_season]

    fu_cur = _load_json(os.path.join(ROOT, "data", "lists", "followup.json"), {}) or {}
    if isinstance(fu_cur, dict):
        fu_ids = set(str(i) for i in (fu_cur.get("ids") or []))
    elif isinstance(fu_cur, list):
        fu_ids = set(str(i) for i in fu_cur)
    else:
        fu_ids = set()
    if fu_this:
        lack = [i for i in fu_this if i not in fu_ids]
        if lack:
            issue("ℹ", "本季有 %d 个随征棋子不在你的随征名单里" % len(lack),
                  "".join("  %s %s\n" % (i, chess_name.get(i, "?")) for i in lack[:12]) +
                  "(你的名单是你自己选的, 不加也不算错; 只是提醒你本季新出/可随征的有这些)")
        else:
            good("随征: 本季 %d 个随征棋子, 你的名单里都有" % len(fu_this))
    # 名单里【两种 ID 都可能出现】(v4.68 起脚本两头都认) —— 详见 unknown_followup_ids()
    skill_name = {str(r.get("SkillID")): (r.get("SkillName") or "")
                  for r in (cfg.get("ChessSkillConf") or [])
                  if isinstance(r, dict) and r.get("SkillID")}
    dead = unknown_followup_ids(fu_all, set(skill_name), fu_ids)
    if dead:
        issue("ℹ", "随征名单里有 %d 个 ID 本季既不是棋子ID 也不是技能ID(可能是上季残留)" % len(dead),
              "".join("  %s\n" % i for i in dead[:12]) +
              "清掉不会有副作用(脚本认不出它, 属于白占一行); 不清也不影响")
    else:
        good("随征名单: %d 个条目本季都认得出(棋子ID 在随征表内 / 技能ID 在技能表内)" % len(fu_ids))

    # ---- ⑤ 经济参数: 表 vs 助手读到的口径 ----
    gl = (cfg.get("ChessGlobalConf") or [{}])[0] or {}
    try:
        costs = csv_loader.shop_costs(season) or {}
    except Exception:
        costs = {}
    for label, key in (("买卡价", "ChessPrice"), ("刷新价", "ShopRefreshCost"), ("卖价", "ChessSoldPrice")):
        v = gl.get(key)
        got = costs.get({"买卡价": "ShopBuyCost", "刷新价": "ShopRefreshCost",
                         "卖价": "ShopSoldPrice"}[label])
        if v is not None and got is not None and int(v) != int(got):
            issue("⚠", "%s 不一致: 表里 %s=%s, 助手读到 %s" % (label, key, v, got),
                  "说明有一处没跟着表走(写死过) —— 去 core/csv_loader.py 的 shop_costs() 对一下")
    if gl.get("ChessPrice") is not None:
        good("经济: 表里 买卡 %s / 刷新 %s / 卖 %s, 每回合基础收益 %s + 利息系数 %s"
             % (gl.get("ChessPrice"), gl.get("ShopRefreshCost"), gl.get("ChessSoldPrice"),
                gl.get("BaseIncrement"), gl.get("ProfitIncrement")))

    # ---- ⑥ 赛季标记 vs 表 ----
    sj = _load_json(os.path.join(ROOT, "data", "season", "season.json"), {}) or {}
    sids = _ids_of(cfg.get("ChessModeSeasonConf"), ("SeasonID", "ID"))
    if sj.get("season") and sids and str(sj.get("season")) not in sids:
        issue("ℹ", "data/season/season.json 写的是 %s, 但本季表里没有这个 SeasonID" % sj.get("season"),
              "表里有的 SeasonID: " + ", ".join(sorted(sids)[:10]))
    else:
        good("赛季标记: season.json=%s(表里对得上)" % sj.get("season"))

    # ---- ⑦ 资源规则是否需要重生成 ----
    # v4.68★ 用 core/resource_rules.py 统一判断, 既要覆盖本季所有 DefaultSkin,
    #   也要检查用户自定义规则是否指向真实存在的文件。
    skins = {str(r.get("DefaultSkin")) for r in (cfg.get("ChessConf") or [])
             if isinstance(r, dict) and r.get("DefaultSkin")}
    skins |= {str(r.get("DefaultSkin")) for r in (cfg.get("ChessGeneralConf") or [])
              if isinstance(r, dict) and r.get("DefaultSkin")}
    tsv = os.path.join(ROOT, "参考_配置表", "资源替换", "%s_匹配规则.tsv" % season)
    custom_rules = RR.load_rules()
    if custom_rules:
        # 自定义规则校验
        bad_custom = []
        for r in custom_rules:
            ok, msg = RR.validate_rule(r)
            if not ok:
                bad_custom.append((r.get("name", "?"), msg))
                continue
            if r.get("enabled") and not RR.target_exists(r.get("target")):
                bad_custom.append((r.get("name", "?"),
                                   "目标文件不存在: %s" % r.get("target")))
        if bad_custom:
            issue("⚠", "资源替换: %d 条自定义规则配置异常" % len(bad_custom),
                  "".join("  %s: %s\n" % (a, b) for a, b in bad_custom[:8]) +
                  "进『资源替换』tab 修正, 或把图片放进对应路径。")
        else:
            good("资源替换: %d 条自定义规则格式正确且目标文件存在" % len(custom_rules))
    if not os.path.exists(tsv):
        issue("ℹ", "还没有资源替换规则文件", "想换立绘/头像就跑 `python tools/gen_res_redirect.py`")
    else:
        text = io.open(tsv, encoding="utf-8-sig").read()
        merged = RR.build_rules(season, custom_rules)
        n = len(merged)
        missing = sorted(s for s in skins if ("/%s.png" % s) not in text)
        if missing:
            issue("ℹ", "资源规则缺 %d 个本季立绘资源" % len(missing),
                  "前 12: " + ", ".join(missing[:12]) +
                  "\n跑 `python tools/gen_res_redirect.py` 重新生成")
        else:
            good("资源规则: %d 条 ≥ 本季 %d 个立绘资源, 全部覆盖" % (n, len(skins)))

    # ---- ⑧ 剧本: 引用的卡本季还在不在(换季最容易"静默失效"的一块) ----
    # 为什么单独查: 剧本里存的是【卡名 + 保存那一刻解析出来的 ID 快照(targetIds)】。
    #   换季换表后: 卡名对不上 = 这一步永远匹配不到; ID 快照还是旧的 = 找卡找不到。
    #   两种都不会报错 —— 表现是"剧本照跑, 但那一步没干活", 不翻日志根本发现不了。
    name_ids = {}       # 卡名 -> 本季 ChessID 集合(一个名字可能有多个变体卡)
    for r in (cfg.get("ChessConf") or []):
        if not isinstance(r, dict):
            continue
        nm = str(r.get("ChessName") or "").strip()
        cid = str(r.get("ChessID") or "").strip()
        if nm and cid:
            name_ids.setdefault(nm, set()).add(cid)
    ok_names = set(name_ids)
    ok_names |= {str(r.get("GeneralName") or "").strip() for r in (cfg.get("ChessGeneralConf") or [])
                 if isinstance(r, dict) and r.get("GeneralName")}
    ok_names |= {str(r.get("SpellName") or "").strip() for r in (cfg.get("ChessSpellConf") or [])
                 if isinstance(r, dict) and r.get("SpellName")}
    # 剧本里会出现、但不是卡名的值(势力 / 范围关键字) —— 不当"失效"报
    NOT_CARD = {"魏", "蜀", "吴", "群", "汉", "黄巾", "西凉", "袁", "晋",
                "hand", "lineup", "shop", "all", "any"}
    cfg_all = _load_json(os.path.join(ROOT, "config.json"), {}) or {}
    scen = [s for s in (cfg_all.get("scenarios") or []) if isinstance(s, dict)]
    # 卡名字段【按 op 限定】: ifStart / waitUntil / loopStart 的 target 装的是"等到/判断什么",
    #   值可能是【数字阈值】(10 / 50 / 100)或【阶段名】(战斗 / 招募), 不是卡名 ——
    #   一视同仁地当卡名查会满屏误报(实测 10 条里 8 条是这种)。
    CARD_FIELDS_BY_OP = {
        "deploy": ("target",),
        "shopFind": ("target",),
        "recycle": ("targets", "exclude"),
        "followUp": ("targets", "exclude", "cards"),
    }

    def _known(nm):
        """认不认得这个名字('闪电·五' 这类带等级后缀的锦囊/变体, 去掉后缀再认一次)。"""
        if nm in ok_names:
            return True
        return nm.split("·")[0].strip() in ok_names

    dead_names = {}     # 剧本名 -> [(卡名, 字段名)]
    stale_ids = {}      # 剧本名 -> [(卡名, 本季ID数, 快照ID数)]
    for sc in scen:
        sname = sc.get("name") or sc.get("id") or "?"
        for st in (sc.get("steps") or []):
            if not isinstance(st, dict):
                continue
            op = str(st.get("op") or "")
            if op not in CARD_FIELDS_BY_OP:
                continue                # 这个 op 的这几个字段不装卡名(剧本名/阈值/文本都在这里)
            snap = set(str(x) for x in (st.get("targetIds") or []) if x)
            for f in CARD_FIELDS_BY_OP[op]:
                v = st.get(f)
                if not v:
                    continue
                names = [str(x).strip() for x in (v if isinstance(v, list) else [v])]
                for nm in names:
                    if not nm or nm in NOT_CARD:
                        continue
                    if nm not in ok_names:
                        dead_names.setdefault(sname, []).append((nm, f))
                    elif snap and nm in name_ids and not (name_ids[nm] & snap):
                        stale_ids.setdefault(sname, []).append((nm, len(name_ids[nm]), len(snap)))
    if dead_names:
        issue("⚠", "%d 条剧本引用了本季表里没有的卡名(这一步永远匹配不到)" % len(dead_names),
              "".join("  【%s】: %s\n" % (s, ", ".join("%s(%s)" % (a, b) for a, b in sorted(set(v))[:8]))
                      for s, v in list(dead_names.items())[:8]) +
              "换季下架/改名都会这样 —— 打开剧本把这张卡换成新卡, 或删掉这一步。")
    if stale_ids:
        issue("⚠", "%d 条剧本卡名还在、但存的 ID 是本季之前的(找卡会失败)" % len(stale_ids),
              "".join("  【%s】: %s\n" % (s, ", ".join("%s(本季%d个ID, 快照%d个)" % (a, b, c)
                                                       for a, b, c in sorted(set(v))[:6]))
                      for s, v in list(stale_ids.items())[:8]) +
              "剧本存的是【保存那一刻】解析出来的 ID 快照 —— 打开剧本重新保存一次就会带上新 ID。")
    # callScenario 的 target 是【子剧本名】—— 子剧本改名/删掉之后, 主剧本跑到那一步
    #   不会报错, 就是"静默无事发生", 最难查的那种失效。
    scen_names = {str(s.get("name") or "").strip() for s in scen if s.get("name")}
    dangling = {}
    for sc in scen:
        sname = sc.get("name") or sc.get("id") or "?"
        for st in (sc.get("steps") or []):
            if not isinstance(st, dict) or str(st.get("op") or "") != "callScenario":
                continue
            t = str(st.get("target") or "").strip()
            if t and t not in scen_names:
                dangling.setdefault(sname, set()).add(t)
    if dangling:
        issue("⚠", "%d 条剧本调用了不存在的子剧本(跑到那一步静默无事发生)" % len(dangling),
              "".join("  【%s】→ 找不到: %s\n" % (s, ", ".join(sorted(v))) for s, v in dangling.items()) +
              "子剧本改名/删除之后就会这样 —— 去剧本页改指向, 或把子剧本建回来。")

    if not dead_names and not stale_ids and not dangling:
        good("剧本: %d 条引用的卡名/ID/子剧本 都与本季表对得上" % len(scen))

    # ---- 汇总 ----
    # ⚠ = 会让助手行为不对(必须处理); ℹ = 提醒/可选项(不影响退出码, 免得换季时被噪音挡住)
    bad = [(l, t, d) for l, t, d in ISSUES if l == "⚠"]
    tips = [(l, t, d) for l, t, d in ISSUES if l != "⚠"]
    print("")
    for t, d in OKS:
        print("  ✓ " + t)
        if d:
            for line in str(d).splitlines():
                print("      " + line)
    for lvl, t, d in ISSUES:
        print("  %s %s" % (lvl, t))
        if d:
            for line in str(d).splitlines():
                print("      " + line)
    print("")
    print("体检结果: %d 项一致, %d 项必须处理, %d 项提醒" % (len(OKS), len(bad), len(tips)))
    if not bad and ISSUES:
        print("(只有提醒项, 助手当前能正常用; 换季后把 ⚠ 清空就算过关)")
    if "--md" in args:
        out = os.path.join(ROOT, "参考_配置表", "赛季体检_%s.md" % season)
        with io.open(out, "w", encoding="utf-8") as f:
            f.write("# 赛季体检 %s\n\n" % season)
            f.write("生成: `python tools/season_check.py --season %s --md`\n\n" % season)
            f.write("| 结论 | 数量 |\n|---|---|\n")
            f.write("| ✓ 一致 | %d |\n| ⚠ 必须处理 | %d |\n| ℹ 提醒 | %d |\n\n"
                    % (len(OKS), len(bad), len(tips)))
            f.write("## 一致(%d)\n\n" % len(OKS))
            for t, d in OKS:
                f.write("* ✓ %s\n" % t)
                if d:
                    f.write("  ```\n  " + str(d).replace("\n", "\n  ") + "\n  ```\n")
            f.write("\n## 要处理(%d)\n\n" % len(ISSUES))
            for lvl, t, d in ISSUES:
                f.write("* %s **%s**\n" % (lvl, t))
                if d:
                    f.write("  ```\n  " + str(d).replace("\n", "\n  ") + "\n  ```\n")
        print("已写: " + out)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())

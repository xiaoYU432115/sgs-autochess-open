# -*- coding: utf-8 -*-
"""赛季配置数据工具 —— 抓取 / 解密 / 导出 CSV / 转 JSON(赛季更新用)。

为什么需要它
------------
助手判断"这张卡是什么、什么技能、要不要选人、按名字找卡"全靠本地那几张表:
  · ChessConf.csv / ChessSpellConf.csv  (棋子表 / 锦囊表, 制表符分隔)
  · data/chess.json / cards.json / tavern_spells.json (名字映射)
赛季一更新, 官方配置就变了(改名、加卡、技能ID换号), 本地表不跟着换 → 找卡失败、
技能解析不到(实机踩过: 丁奉的 ChessSkill 明明是 [330411], 脚本却说"没解析到任何技能ID")。

这个工具就是"换表"的那一步, 流程与社区参考实现(sgs_forward_looking)一致:
  ① 下载 Config.sgs + resc(解密用的 wasm)
  ② 解密 Config.sgs → 解压 → 里面就有 TavernChessConfig.sgs
  ③ 再解密 TavernChessConfig.sgs → TavernChessConfig.json(所有自走棋配置表)
  ④ 导出 ChessConf.csv / ChessSpellConf.csv (列与旧表完全一致, 直接覆盖助手根目录那两张)
  ⑤ 合并 data/chess.json / cards.json / tavern_spells.json 的名字(按 ID 更新, 不删旧赛季)
  ⑥ 写 data/season/season.json —— 助手启动时读它来决定"当前赛季"(不再写死 10)

用法
----
    python tools/season_config.py check                # 只看现状(不联网)
    python tools/season_config.py all                  # 一键: 抓取+解密+导出CSV+合并JSON
    python tools/season_config.py fetch                # 只抓取解密(存赛季目录)
    python tools/season_config.py csv                  # 只导出 CSV(从已抓到的 json)
    python tools/season_config.py json                 # 只合并 JSON + 写赛季标记
    python tools/season_config.py fetch --season 11 --version 11   # 下赛季(路径段/赛季号)
    python tools/season_config.py fetch --from-json 路径.json      # 直接吃现成 json(不联网)

    python tools/season_config.py fetch --sgs 某个TavernChessConfig.sgs   # 只解密这个文件

抓完之后: 重启助手(重新连接会自动重推"棋子名/锦囊名/技能"三张映射)。

界面入口(推荐, 不用敲命令)
--------------------------
助手界面 → 【赛季数据】tab:
  ① 一键下载解析   = fetch + 解析(只写赛季目录, 不动现行数据)
  ② 查看数据       = 看解析出来的棋子/锦囊(本季/关键词筛)
  ③ 推送到现行数据 = push_to_current(merge,   保留旧赛季键, CSV 列序沿用旧表)
     替换现行数据 = push_to_current(replace, 完全按快照重建)
  ④ 导入 / 导出    = import_any / export_all(所有格式整包)
上面每一步写现行数据之前都会整份备份到 data/_backup/。
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)                 # 自走棋助手/
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core import sgs_decrypt_util            # noqa: E402
from core import silence as _SIL             # noqa: E402
from core.config import DATA_DIR             # noqa: E402

# v4.113★ Windows 控制台默认 GBK, 日志里的 ✓ / ⚠ 一打印就 UnicodeEncodeError —— 实测
#   `python tools/season_config.py fetch` 会在【解密成功】那一行当场崩掉(文件其实已经
#   写好了, 但后面的 CSV/JSON 步骤全没跑, 看着像"抓取失败")。统一按"打不出的字换 ?"。
try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception: _SIL.note("tools/season_config.py", "sys.stdout.reconfigure(errors='replace')")

# v4.113★ 官方资源路径段 {ver} 是【客户端版本目录】(10 = 十周年), 跟自走棋赛季号
#   【不是一回事】—— 以前写成"路径段 = 赛季号", 一到 S11 就去请求 /11/, 拿回来的是占位页,
#   于是赛季目录里有 s11/ 却一张表都没有(表现: 切到 S11 之后"什么都查不到")。
#
#   实测 2026-10-01:
#     /10/Config.sgs → 8396255 字节(真配置, 2026-10-01 更新)
#     /9/ /11/ /12/  → 全是 11439 字节, 连 Last-Modified 都同一个 —— 同一个占位页
#
#   赛季号在【配置内部】: ChessConf / ChessSpellConf 每行都带 SeasonID 字段, 而且
#   【1~11 季全挤在同一份配置里】(S10 棋子 373 行、S11 棋子 372 行、S11 锦囊 96 行)。
#   → 换季不是换路径, 是换 SeasonID 那一筛; 抓哪一季都用 /10/ 这份。
DEFAULT_VERSION = "10"
URL_TPL_CONFIG = "https://web.sanguosha.com/{ver}/pc/res/config/Config.sgs"
URL_TPL_RESC = "https://web.sanguosha.com/{ver}/pc/libs/min/resc"

ROOT_CHESS_CSV = os.path.join(ROOT, "ChessConf.csv")
ROOT_SPELL_CSV = os.path.join(ROOT, "ChessSpellConf.csv")
REF_RESC = os.path.join(ROOT, "参考_sgs_forward_looking-main", "resc")


_LOG_FN = None

# v4.71★ 最近一次抓到的 Config.sgs 的"官方版本指纹"(大小 + Last-Modified)。
#   用户问"赛季更新这个能更新到吗? 加上可以更新" —— 能更新(整套 fetch/parse/push 都在),
#   缺的是"先告诉我官方有没有新东西"这一步: 没有指纹就只能整份重下 8MB 再比 md5。
#   下载时顺手记下来存进 meta.json, 以后 check_update() 两个 HEAD 请求就能判断。
_LAST_CONFIG_META = {}


def set_logger(fn):
    """UI(赛季数据 Tab)把日志接到界面日志框; 传 None 恢复打印到控制台。"""
    global _LOG_FN
    _LOG_FN = fn if callable(fn) else None


def log(msg):
    """所有日志都过这里: UI 挂过回调就走回调(界面日志框), 否则打印。回调出错不吞日志。"""
    if _LOG_FN is not None:
        try:
            _LOG_FN(str(msg))
            return
        except Exception as e:
            # 界面日志框出问题也不能把这条日志吞掉
            sys.stderr.write("[season] 日志回调失败: %s\n" % e)
    print(msg, flush=True)


_ui_log = log


def _md5(path):
    try:
        h = hashlib.md5()
        with open(path, "rb") as f:
            for blk in iter(lambda: f.read(1 << 20), b""):
                h.update(blk)
        return h.hexdigest()
    except Exception:
        return ""


def season_dir(season):
    d = os.path.join(DATA_DIR, "season", "s%s" % season)
    os.makedirs(d, exist_ok=True)
    return d


def _download(url, dest):
    import requests
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    log("  下载: %s" % url)
    with requests.get(url, stream=True, timeout=(10, 120)) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for blk in r.iter_content(chunk_size=1 << 16):
                if blk:
                    f.write(blk)
    log("  已保存: %s (%d 字节)" % (dest, os.path.getsize(dest)))
    # v4.71★ 顺手把响应头带回去 —— "检查官方有没有更新"要靠【Last-Modified / Content-Length】
    #   和本地快照比(v4.71 之前没存, 只能靠整份重下去比 md5, 那是 8MB)。旧调用方忽略返回值即可。
    try:
        return dict(r.headers or {})
    except Exception:
        return {}
    return dest


def _find_resc(argv_resc=None):
    """解密用的 resc(wasm): 参数 > 助手根目录 > 参考仓库 > 赛季目录。"""
    cands = []
    if argv_resc:
        cands.append(argv_resc)
    cands += [os.path.join(ROOT, "resc"), REF_RESC]
    for c in cands:
        if c and os.path.exists(c):
            log("  使用 resc: %s" % c)
            return c
    return None


def _ensure_resc(sdir, ver, argv_resc=None):
    """解密用的 resc(wasm): 本地有就用本地的, 没有就按官方地址下一个(实测 /10/pc/libs/min/resc 可下)。"""
    local = _find_resc(argv_resc)
    if local:
        return local
    dst = os.path.join(sdir, "resc")
    log("  本地没有 resc, 从官方下载 ...")
    _download(URL_TPL_RESC.format(ver=ver), dst)
    if os.path.getsize(dst) < 4000:
        raise SystemExit("下载到的 resc 不对劲(%d 字节), 用 --resc 手动指定解密文件" % os.path.getsize(dst))
    return dst


# ---------------------------------------------------------------- fetch(抓取+解密)
def cmd_fetch(args):
    season = str(args.season)
    sdir = season_dir(season)
    # v4.113★ 路径段【不等于赛季号】: 10 是十周年客户端版本目录(实测 /9/ /11/ /12/ 全是
    #   11439 字节占位页)。赛季号只决定解析时按哪个 SeasonID 筛 —— 抓哪一季都下 /10/ 这份。
    ver = str(args.version or DEFAULT_VERSION).strip() or DEFAULT_VERSION
    out_json = os.path.join(sdir, "TavernChessConfig.json")
    resc = _ensure_resc(sdir, ver, args.resc) if not args.from_json else None

    if args.from_json:
        src = args.from_json
        if not os.path.exists(src):
            raise SystemExit("找不到 --from-json 指定的文件: %s" % src)
        shutil.copyfile(src, out_json)
        log("[1/1] 直接使用现成 json: %s → %s" % (src, out_json))
        return out_json

    # 情况 A: 直接给了一个 TavernChessConfig.sgs
    if args.sgs:
        if not os.path.exists(args.sgs):
            raise SystemExit("找不到 --sgs 指定的文件: %s" % args.sgs)
        log("[1/1] 解密 %s ..." % args.sgs)
        p = sgs_decrypt_util.decrypt_sgs_file(input_path=_P(args.sgs), wasm_path=_P(resc),
                                              output_path=_P(out_json))
        _check_json(p)
        return out_json

    # 情况 B: 完整流程 —— 下 Config.sgs → 解压 → 找 TavernChessConfig.sgs → 解密
    tmp = os.path.join(sdir, "_tmp")
    os.makedirs(tmp, exist_ok=True)
    cfg = os.path.join(tmp, "Config.sgs")
    resc_copy = os.path.join(sdir, "resc")
    if os.path.abspath(resc) != os.path.abspath(resc_copy) and not os.path.exists(resc_copy):
        shutil.copyfile(resc, resc_copy)

    log("[1/3] 抓取 Config.sgs (路径段 ver=%s)" % ver)
    _hdr = _download(URL_TPL_CONFIG.format(ver=ver), cfg)
    global _LAST_CONFIG_META
    _LAST_CONFIG_META = {"config_size": os.path.getsize(cfg),
                         "config_mtime": str((_hdr or {}).get("Last-Modified") or ""),
                         "config_url": URL_TPL_CONFIG.format(ver=ver)}
    if os.path.getsize(cfg) < 100000:
        raise SystemExit(
            "下载到的 Config.sgs 只有 %d 字节 —— 这个版本路径(/pc/res/config/)还没上线, "
            "或路径段写错了。用法: --version 11 (下赛季一般是 11)" % os.path.getsize(cfg))

    log("[2/3] 解密并解压 Config.sgs ...")
    exdir = sgs_decrypt_util.decrypt_sgs_file(input_path=_P(cfg), wasm_path=_P(resc_copy),
                                              extract_zip=True)
    exdir = str(exdir)
    tavern_sgs = None
    for root, _dirs, files in os.walk(exdir):
        for fn in files:
            if fn == "TavernChessConfig.sgs":
                tavern_sgs = os.path.join(root, fn)
                break
        if tavern_sgs:
            break
    if not tavern_sgs:
        raise SystemExit("Config.sgs 里没找到 TavernChessConfig.sgs —— 官方路径可能变了(试试 --version)")
    shutil.copyfile(tavern_sgs, os.path.join(sdir, "TavernChessConfig.sgs"))
    log("  找到 TavernChessConfig.sgs: %d 字节" % os.path.getsize(tavern_sgs))

    log("[3/3] 解密 TavernChessConfig.sgs → json ...")
    p = sgs_decrypt_util.decrypt_sgs_file(input_path=_P(tavern_sgs), wasm_path=_P(resc_copy),
                                          output_path=_P(out_json))
    _check_json(p)
    try:
        shutil.rmtree(tmp)
    except Exception as e:
        log("  (临时目录没删掉, 不影响结果: %s)" % e)
    return out_json


def _P(p):
    from pathlib import Path
    return Path(p)


def _check_json(p):
    p = str(p)
    if not os.path.exists(p):
        raise SystemExit("解密失败, 没有生成 %s" % p)
    with open(p, "r", encoding="utf-8") as f:
        d = json.load(f)
    log("  ✓ 解密成功: %s (%.1f MB, 顶层表 %d 个)" % (p, os.path.getsize(p) / 1048576.0, len(d)))
    for k in ("ChessConf", "ChessSpellConf", "ChessSkillConf"):
        v = d.get(k)
        n = len(v) if hasattr(v, "__len__") else 0
        log("    · %s: %d 行" % (k, n))


# ---------------------------------------------------------------- CSV 导出
def _rows_of(cfg, table):
    v = cfg.get(table) or []
    if isinstance(v, dict):
        return list(v.values())
    return list(v)


def _fmt(v):
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        return "[" + ",".join(_fmt(x) for x in v) + "]"
    if isinstance(v, bool):
        return "1" if v else "0"
    s = str(v)
    return s.replace("\t", " ").replace("\r", " ").replace("\n", " ").strip()


def _read_header(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            head = f.readline().rstrip("\r\n")
        cols = [c for c in head.split("\t")]
        if len(cols) > 3:
            return cols
    except Exception as e:
        log("  (旧表头读不出来, 按新表列顺序写: %s)" % e)
    return None


def _write_tsv(path, rows, keep_header=None):
    """列顺序: 沿用旧表表头(保证助手/用户脚本不会因列序变化而错位), 新列追加在末尾。"""
    cols = []
    if keep_header:
        cols = [c for c in keep_header]
    names = set()
    for r in rows:
        if isinstance(r, dict):
            for k in r.keys():
                names.add(k)
    for c in cols:
        names.discard(c)
    key_order = []
    for r in rows:
        if isinstance(r, dict):
            for k in r.keys():
                if k in names and k not in key_order:
                    key_order.append(k)
    cols = cols + key_order
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".new"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write("\t".join(cols) + "\n")
        for r in rows:
            if not isinstance(r, dict):
                continue
            f.write("\t".join(_fmt(r.get(c)) for c in cols) + "\n")
    if os.path.exists(path):
        bak = path + ".bak"
        try:
            if not os.path.exists(bak):
                shutil.copyfile(path, bak)
        except Exception as e:
            log("  警告: 旧表备份失败(%s), 仍然覆盖" % e)
    os.replace(tmp, path)
    return cols, len(rows)


def cmd_csv(args):
    season = str(args.season)
    sdir = season_dir(season)
    jp = args.json_path or os.path.join(sdir, "TavernChessConfig.json")
    if not os.path.exists(jp):
        raise SystemExit("还没有解密后的 json: %s\n  先跑: python tools/season_config.py fetch" % jp)
    cfg = json.load(open(jp, "r", encoding="utf-8"))

    chess_rows = _rows_of(cfg, "ChessConf")
    spell_rows = _rows_of(cfg, "ChessSpellConf")
    if not chess_rows:
        raise SystemExit("ChessConf 是空的, 这个 json 不对")

    # 1) 赛季目录里留一份
    c1, n1 = _write_tsv(os.path.join(sdir, "ChessConf.csv"), chess_rows,
                        keep_header=_read_header(os.path.join(sdir, "ChessConf.csv")))
    c2, n2 = _write_tsv(os.path.join(sdir, "ChessSpellConf.csv"), spell_rows,
                        keep_header=_read_header(os.path.join(sdir, "ChessSpellConf.csv")))
    log("[CSV] 赛季目录: ChessConf.csv %d 行 / ChessSpellConf.csv %d 行" % (n1, n2))

    # 2) 覆盖助手根目录那两张(助手真正读的就是它们)
    h1 = _read_header(ROOT_CHESS_CSV)
    h2 = _read_header(ROOT_SPELL_CSV)
    _write_tsv(ROOT_CHESS_CSV, chess_rows, keep_header=h1)
    _write_tsv(ROOT_SPELL_CSV, spell_rows, keep_header=h2)
    log("[CSV] 已更新助手根目录: ChessConf.csv (%d 列) / ChessSpellConf.csv (旧表备到 .bak)" % len(c1))
    return {"chess": n1, "spell": n2}


# ---------------------------------------------------------------- JSON 合并
def _spell_display_name(row):
    nm = _fmt(row.get("SpellName"))
    rk = _fmt(row.get("SpellTextRank"))
    return (nm + "·" + rk) if (nm and rk) else nm


def _merge_json(ns_path, entries, src_tag):
    """按 ID 合并(只更新名字, 不删别的赛季/别人的键) —— 赛季表是全量, 键都在里面。"""
    old = {}
    if os.path.exists(ns_path):
        try:
            old = json.load(open(ns_path, "r", encoding="utf-8")) or {}
        except Exception:
            old = {}
    added = updated = 0
    for k, nm in entries.items():
        if not nm:
            continue
        cur = old.get(k)
        if not isinstance(cur, dict):
            old[k] = {"n": nm, "src": src_tag}
            added += 1
        else:
            if cur.get("n") != nm:
                updated += 1
            cur["n"] = nm
            cur.setdefault("src", src_tag)
    os.makedirs(os.path.dirname(ns_path), exist_ok=True)
    with open(ns_path, "w", encoding="utf-8") as f:
        json.dump(old, f, ensure_ascii=False, indent=2)
    return {"total": len(old), "added": added, "updated": updated}


def cmd_json(args):
    season = str(args.season)
    sdir = season_dir(season)
    jp = args.json_path or os.path.join(sdir, "TavernChessConfig.json")
    if not os.path.exists(jp):
        raise SystemExit("还没有解密后的 json: %s\n  先跑: python tools/season_config.py fetch" % jp)
    cfg = json.load(open(jp, "r", encoding="utf-8"))
    tag = "season_s%s" % season

    chess_entries, spell_entries, cur_spells = {}, {}, {}
    for row in _rows_of(cfg, "ChessConf"):
        cid = _fmt(row.get("ChessID"))
        nm = _fmt(row.get("ChessName"))
        if cid.isdigit() and nm:
            chess_entries[cid] = nm
    for row in _rows_of(cfg, "ChessSpellConf"):
        sid = _fmt(row.get("SpellID"))
        nm = _spell_display_name(row)
        if not (sid.isdigit() and nm):
            continue
        spell_entries[sid] = nm
        if _fmt(row.get("SeasonID")) == season and _fmt(row.get("IsAvailable")) in ("1", ""):
            cur_spells[sid] = nm

    r1 = _merge_json(os.path.join(DATA_DIR, "chess.json"), chess_entries, tag)
    r2 = _merge_json(os.path.join(DATA_DIR, "cards.json"), spell_entries, tag)
    r3 = _merge_json(os.path.join(DATA_DIR, "tavern_spells.json"), cur_spells, tag)
    log("[JSON] chess.json  %s" % r1)
    log("[JSON] cards.json  %s" % r2)
    log("[JSON] tavern_spells.json(本季 %d 条) %s" % (len(cur_spells), r3))

    # v4.113★ version 记的是【资源路径段】(十周年=10), 不是赛季号 —— 两份分开记,
    #   免得再看 meta.json 的人把 "version": "10" 当成"第 10 季"。
    _ver = str(args.version or DEFAULT_VERSION).strip() or DEFAULT_VERSION
    meta = {
        "season": season, "version": _ver,
        "source": URL_TPL_CONFIG.format(ver=_ver),
        "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "json": os.path.basename(jp), "json_md5": _md5(jp),
        "counts": {"chess": len(chess_entries), "spell": len(spell_entries), "spell_this_season": len(cur_spells)},
        "merged": {"chess": r1, "cards": r2, "tavern_spells": r3},
    }
    with open(os.path.join(sdir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    # 关键: 写"当前赛季"标记 —— 助手启动时读它(core/csv_loader._read_current_season)
    sdir2 = os.path.join(DATA_DIR, "season")
    os.makedirs(sdir2, exist_ok=True)
    with open(os.path.join(sdir2, "season.json"), "w", encoding="utf-8") as f:
        json.dump({"season": season, "version": meta["version"],
                   "updated_at": meta["fetched_at"], "counts": meta["counts"]}, f,
                  ensure_ascii=False, indent=2)
    log("[JSON] 已写 data/season/season.json → 当前赛季 = S%s" % season)
    return meta


# ---------------------------------------------------------------- check(现状)
def cmd_check(args):
    log("== 赛季数据现状 ==")
    sj = os.path.join(DATA_DIR, "season", "season.json")
    if os.path.exists(sj):
        log("标记文件: %s\n  %s" % (sj, open(sj, encoding="utf-8").read().strip()))
    else:
        log("标记文件: (无) —— 助手回退默认赛季 10; 跑一次 all 就会生成")
    from core import csv_loader
    csv_loader._cache.clear()
    rows = csv_loader.load_from_csv()
    try:
        csv_season = csv_loader.CURRENT_SEASON
    except Exception:
        csv_season = "?"
    skills = [r for r in rows if r.get("skills")]
    log("当前赛季(csv_loader): S%s | ChessConf.csv %d 行 | 带技能的行 %d" % (csv_season, len(rows), len(skills)))
    m = csv_loader.chess_skill_map()
    log("技能映射: ids %d 条 / 名字 %d 条 | 样例 丁奉=%s 21003051=%s"
        % (m["n"], len(m["names"]), m["names"].get("丁奉"), m["ids"].get("21003051")))
    sp = csv_loader.load_spell_conf() if hasattr(csv_loader, "load_spell_conf") else None
    if sp:
        log("ChessSpellConf.csv %d 行 | 样例 %s" % (len(sp), sp[0]))
    for p in (ROOT_CHESS_CSV, ROOT_SPELL_CSV):
        log("  %s  %d 字节  改于 %s" % (p, os.path.getsize(p) if os.path.exists(p) else -1,
                                     time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(p)))
                                     if os.path.exists(p) else "-"))
    return None


# ================================================================ 一键(UI「赛季数据」Tab 用)
def current_season():
    """助手现在认的赛季号(读 data/season/season.json, 读不到就 csv_loader 的默认 10)。"""
    try:
        from core import csv_loader
        return str(getattr(csv_loader, "CURRENT_SEASON", "10"))
    except Exception:
        return "10"


class _FetchArgs(object):
    """给 cmd_fetch/cmd_csv 复用的小参数盒子 —— UI 不走 argparse, 直接造一个。"""

    def __init__(self, **kw):
        self.season = "10"
        self.version = None
        self.resc = None
        self.sgs = None
        self.from_json = None
        self.json_path = None
        self.__dict__.update(kw)


def fetch_and_parse(season="10", version=None, from_json=None, sgs=None, resc=None):
    """『一键下载解析』: 抓官方配置 → 解密 → 解析成助手要的各张表(供 UI 按钮调用)。

    只写赛季目录 data/season/s<赛季>/(Config json / 两张 CSV / meta.json),
    **不动**现行数据 —— 用户看过『查看数据』没问题, 再点『推送到现行数据』或『替换现行数据』,
    那两步才会覆盖助手真正在读的文件。这样"下载解析"永远不会把助手弄坏。

    from_json / sgs 给了就跳过联网(用本地文件解析)。返回 info 供界面显示概览。
    """
    season = (str(season or "").strip() or "10")
    ver = (str(version).strip() or None) if version else None
    log("══ 一键下载解析 · 赛季 S%s%s ══" % (season, ("  (资源路径段 %s)" % ver) if ver else ""))
    if from_json:
        log("  用本地 json(不联网): %s" % from_json)
    elif sgs:
        log("  用本地 sgs(不联网): %s" % sgs)

    jp = cmd_fetch(_FetchArgs(season=season, version=ver, resc=resc, sgs=sgs, from_json=from_json))

    log("[解析] 读 %s ..." % jp)
    with open(jp, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    chess_rows = _rows_of(cfg, "ChessConf")
    spell_rows = _rows_of(cfg, "ChessSpellConf")
    if not chess_rows:
        raise RuntimeError("解析出来的 ChessConf 是空的 —— 这份 json 不是自走棋的 TavernChessConfig")
    ent = season_entries(season, cfg)

    # 赛季目录里留一份 CSV: 列 = json 里的键顺序, 与助手根目录那两张格式一致(可直接『导入』)
    sdir = season_dir(season)
    c1, _ = _write_tsv(os.path.join(sdir, "ChessConf.csv"), chess_rows, keep_header=None)
    c2, _ = _write_tsv(os.path.join(sdir, "ChessSpellConf.csv"), spell_rows, keep_header=None)

    tables = {t: len(_rows_of(cfg, t)) for t in ("ChessConf", "ChessSpellConf",
                                                 "ChessSkillConf", "ChessGeneralConf")}
    info = {"season": season, "version": str(ver or season), "json": jp, "dir": sdir,
            "cols": len(c1), "tables": tables,
            "chess": len(ent["chess"]), "spell": len(ent["spells"]), "skills": len(ent["skills"]),
            "this_chess": len(ent["this_chess"]), "this_spells": len(ent["this_spells"]),
            "chess_rows": len(chess_rows), "spell_rows": len(spell_rows),
            "json_size": os.path.getsize(jp)}
    log("[解析] 表: " + " / ".join("%s=%d" % (k, v) for k, v in tables.items()))
    log("[解析] 可用: 棋子 %d(本季商店 %d) / 锦囊 %d(本季可用 %d) / 技能 %d"
        % (info["chess"], info["this_chess"], info["spell"], info["this_spells"], info["skills"]))
    log("[解析] 已写赛季目录: ChessConf.csv(%d 行/%d 列) / ChessSpellConf.csv(%d 行/%d 列); 现行数据未改动"
        % (len(chess_rows), len(c1), len(spell_rows), len(c2)))

    # meta.json: 记住这份快照哪来的(push_to_current 从里面读 version)
    # v4.71★ 多存两个字段: Config.sgs 的大小与官方 Last-Modified —— 下次点「检查官方更新」
    #   拿这两个跟官方 HEAD 一比就知道"官方那份换过没有"(不用重下 8MB)。
    _cmeta = dict(_LAST_CONFIG_META or {})
    try:
        with open(os.path.join(sdir, "meta.json"), "w", encoding="utf-8") as f:
            json.dump({"season": season, "version": info["version"],
                       "source": "(本地导入)" if (from_json or sgs)
                                 else URL_TPL_CONFIG.format(ver=info["version"]),
                       "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                       "json": os.path.basename(jp), "json_md5": _md5(jp),
                       "config_size": _cmeta.get("config_size"),
                       "config_mtime": _cmeta.get("config_mtime") or "",
                       "counts": {"chess": info["chess"], "spell": info["spell"],
                                  "chess_this_season": info["this_chess"],
                                  "spell_this_season": info["this_spells"]}},
                      f, ensure_ascii=False, indent=2)
    except Exception as e:
        log("  (meta.json 没写成, 不影响解析: %s)" % e)
    log("下一步: 点『推送到现行数据』(保留旧赛季, 推荐) 或『替换现行数据』(整份重建)")
    return info


# ================================================================ 检查官方有没有更新(v4.71★)
def _head_meta(url, timeout=15):
    """HEAD 一个官方地址 → {"status", "size", "mtime"}; 失败时 status 为 0/异常名。

    HEAD(而不是 GET)是因为 Config.sgs 有 8MB —— 只想知道"官方那份换过没有",
    没必要每次点检查都把 8MB 拖下来。
    """
    out = {"url": url, "status": 0, "size": 0, "mtime": "", "err": ""}
    try:
        import requests
        r = requests.head(url, timeout=timeout, allow_redirects=True)
        out["status"] = int(r.status_code)
        out["size"] = int(r.headers.get("Content-Length") or 0)
        out["mtime"] = str(r.headers.get("Last-Modified") or "")
    except Exception as e:
        out["err"] = "%s: %s" % (type(e).__name__, e)
    return out


def check_update(season=None, version=None, timeout=15):
    """官方配置有没有更新? → dict(供界面「检查官方更新」按钮用; 只 HEAD, 不下载)。

    用户原话: "赛季更新这个能更新到吗? 加上可以更新"。
    能更新(下载/解密/导出/推送整条链都在『一键下载解析』里), 缺的是【先看一眼值不值得下】。
    这里做三件事(一共就 2~3 个 HEAD 请求):
      ① 比官方【本季路径段】那份 Config.sgs 和本地快照记的大小/时间 —— 变了 = 官方改过表;
      ② 比『本地现行数据』(根目录 ChessConf.csv) 和最新快照 —— 下载了但没推送的话提醒他;
      ③ 探一下【下一个路径段】是否已经上线(下赛季发布时它才开始有大文件, 现在是个占位) ——
         这就是"能提前知道要换赛季"。

    返回:
      {"season", "version", "remote": {...}, "changed", "has_snapshot", "pushed",
       "next_version", "next_live", "notes": [...]}
    说明: 本地快照早于 v4.71 时没有指纹字段, changed 记为 None(不是"没变", 是"比不了"),
      这时 notes 里会让他先跑一次「一键下载解析」把指纹存下来。
    """
    season = str(season or current_season() or "10").strip() or "10"
    # v4.113★ 路径段【不跟赛季号走】—— 它就是十周年版本目录(固定 10)。以前这里写
    #   `version or season`, 于是填赛季号 11 会去抓 /11/(占位 11439 字节) → 空表。
    ver = str(version or DEFAULT_VERSION).strip() or DEFAULT_VERSION
    sdir = season_dir(season)
    meta_p = os.path.join(sdir, "meta.json")
    notes = []
    meta = {}
    if os.path.exists(meta_p):
        try:
            with open(meta_p, "r", encoding="utf-8") as f:
                meta = json.load(f) or {}
        except Exception as e:
            notes.append("本地 meta.json 读不了(%s), 这次只能报官方那份的大小/时间" % e)

    log("== 检查官方更新 · 赛季 S%s(路径段 %s) ==" % (season, ver))
    remote = _head_meta(URL_TPL_CONFIG.format(ver=ver), timeout=timeout)
    if remote["status"] == 200 and remote["size"] >= 100000:
        log("  官方 Config.sgs: %d 字节, 更新于 %s" % (remote["size"], remote["mtime"] or "?"))
    elif remote["status"] == 200:
        log("  官方 Config.sgs 只有 %d 字节 —— 这个路径段还没上线(占位文件)" % remote["size"])
    else:
        log("  取官方 Config.sgs 失败: %s%s" % (remote["status"] or "", remote["err"]))

    # 比指纹: 只有两边都拿得到结论时才敢说"变了/没变"
    changed = None
    if remote["status"] == 200 and remote["size"] >= 100000:
        lsize = meta.get("config_size")
        lmtime = str(meta.get("config_mtime") or "")
        if lsize and (int(lsize) != remote["size"] or (lmtime and remote["mtime"]
                                                       and lmtime != remote["mtime"])):
            changed = True
            notes.append("官方那份和本地快照【不一样】了 —— 官方改过这张表, 建议重新『一键下载解析』")
        elif lsize:
            changed = False
            notes.append("官方那份和本地快照一致 —— 暂时不用重下")
        else:
            notes.append("本地快照没记官方指纹(早于 v4.71 的快照) —— 这点判断不了; "
                         "先跑一次『一键下载解析』就会记下来")

    # 下载了但没推送? 比根目录 CSV 和快照的修改时间
    pushed = None
    csv_p = os.path.join(sdir, "ChessConf.csv")
    if os.path.exists(csv_p) and os.path.exists(ROOT_CHESS_CSV):
        pushed = os.path.getmtime(ROOT_CHESS_CSV) >= os.path.getmtime(csv_p)
        if not pushed:
            notes.append("赛季目录里有更新的 CSV, 但助手根目录那两张表还是旧的 —— "
                         "点『推送到现行数据』才会真正生效")

    # v4.113★ 下赛季【不再看路径段】(路径段是十周年版本目录, 永远是 10) —— 而是看本地
    #   快照那份配置里 SeasonID 的最大值: 换季是换 SeasonID, 1~11 季都在同一份配置里。
    nxt = ""
    next_live = False
    try:
        jp = os.path.join(sdir, "TavernChessConfig.json")
        mx = 0
        if os.path.exists(jp):
            with open(jp, "r", encoding="utf-8") as f:
                cfg = json.load(f) or {}
            for r in (cfg.get("ChessConf") or []):
                if isinstance(r, dict):
                    try:
                        mx = max(mx, int(str(r.get("SeasonID") or 0)))
                    except Exception: _SIL.note("tools/season_config.py", "int(SeasonID)")
        if season.isdigit() and mx > int(season):
            nxt = str(mx)
            next_live = True
            notes.append("⚠ 这份配置里已经有 S%s 的数据(SeasonID=%s) —— 换季只要把赛季号填 %s "
                         "再点『一键下载解析』, 资源路径段保持 %s 不动" % (mx, mx, mx, ver))
        else:
            log("  配置里最大的 SeasonID = %s(当前 S%s) —— 官方还没放出更新的赛季"
                % (mx or "?", season))
    except Exception as e:      # noqa: BLE001
        log("  (读本地快照算 SeasonID 失败: %s)" % e)
    for n in notes:
        log("  · %s" % n)
    return {"season": season, "version": ver, "remote": remote, "changed": changed,
            "has_snapshot": bool(meta), "pushed": pushed,
            "next_version": nxt, "next_live": next_live, "notes": notes}


# ================================================================ 现行数据(推送/替换/导入导出)
# "推送 / 替换"到底要动哪些文件? —— 下面每个都是助手**真的**会去读的, 一个都不能漏:
#   · ChessConf.csv / ChessSpellConf.csv          找卡/技能/白名单的根(core.csv_loader 读, 制表符)
#   · data/season/season.json                     当前赛季标记(决定白名单和"本季可用"口径)
#   · data/chess.json                             chessID→名字 全量(页面端名字解析/按名字找卡)
#   · data/cards.json                             卡牌名(武将+锦囊+主公)
#   · data/tavern_spells.json                     本季可用锦囊(剧本里的锦囊下拉/待选候选)
#   · data/tavern_chess.json                      本季可用武将(招募/上阵的候选列表)
#   · data/skills.json                            技能名+描述(技能解析兜底/展示)
#   · data/lists/whitelist.json / blacklist.json  本季白名单 / 全量黑名单(用户可手改)
#   (data/hero_picker.json 由 UI 勾选『顺带重建武将精选』时用 ops.build_hero_picker 重建)

def _all_files():
    """会写到的现行数据文件: [(展示用相对名, 绝对路径)] —— 备份/导出/替换都用这一份清单。"""
    return [
        ("ChessConf.csv", ROOT_CHESS_CSV),
        ("ChessSpellConf.csv", ROOT_SPELL_CSV),
        ("data/season/season.json", os.path.join(DATA_DIR, "season", "season.json")),
        ("data/chess.json", os.path.join(DATA_DIR, "chess.json")),
        ("data/cards.json", os.path.join(DATA_DIR, "cards.json")),
        ("data/tavern_spells.json", os.path.join(DATA_DIR, "tavern_spells.json")),
        ("data/tavern_chess.json", os.path.join(DATA_DIR, "tavern_chess.json")),
        ("data/skills.json", os.path.join(DATA_DIR, "skills.json")),
        # v4.43★ 主公 / 主公技能 —— 以前【没有任何写入方】, 这两份文件常年是空的,
        # 而『技能库』tab 查主公就靠它们(没有赛季快照时的兜底)。补进清单: 备份/还原要带上。
        ("data/lords.json", os.path.join(DATA_DIR, "lords.json")),
        ("data/lord_skills.json", os.path.join(DATA_DIR, "lord_skills.json")),
        ("data/lists/whitelist.json", os.path.join(DATA_DIR, "lists", "whitelist.json")),
        ("data/lists/blacklist.json", os.path.join(DATA_DIR, "lists", "blacklist.json")),
    ]


def load_snapshot(season, json_path=None):
    """读赛季快照 json(下载解析的产物) → (cfg, 路径)"""
    jp = json_path or os.path.join(season_dir(season), "TavernChessConfig.json")
    if not os.path.exists(jp):
        raise RuntimeError("还没有解密后的 json: %s\n  先点『一键下载解析』(或跑 fetch)" % jp)
    with open(jp, "r", encoding="utf-8") as f:
        return json.load(f), jp


def season_entries(season, cfg):
    """快照 → 现行数据需要的各张表(字段名已归一化成助手用的那套)。

    返回:
      chess/spells/skills  全量(名字映射用)
      this_chess           本季商店可买武将(IsShop=1) → tavern_chess.json
      this_spells          本季可用锦囊          → tavern_spells.json
      lords/lord_skills    主公(ChessGeneralConf) / 主公技能(ChessGeneralSkillConf) ★v4.43
    """
    season = str(season)
    chess, spells, skills = {}, {}, {}
    this_chess, this_spells = {}, {}
    lords, lord_skills = {}, {}
    for row in _rows_of(cfg, "ChessConf"):
        cid, nm = _fmt(row.get("ChessID")), _fmt(row.get("ChessName"))
        if not (cid.isdigit() and nm):
            continue
        chess[cid] = nm
        if _fmt(row.get("SeasonID")) == season and _fmt(row.get("IsShop")) == "1":
            this_chess[cid] = nm
    for row in _rows_of(cfg, "ChessSpellConf"):
        sid, nm = _fmt(row.get("SpellID")), _spell_display_name(row)
        if not (sid.isdigit() and nm):
            continue
        spells[sid] = nm
        if _fmt(row.get("SeasonID")) == season and _fmt(row.get("IsAvailable")) in ("1", ""):
            this_spells[sid] = nm
    for row in _rows_of(cfg, "ChessSkillConf"):
        kid, nm = _fmt(row.get("SkillID")), _fmt(row.get("SkillName"))
        if kid.isdigit() and nm:
            skills[kid] = {"n": nm, "desc": _fmt(row.get("SkillDesc"))}
    # 主公技能先建表, 后面主公靠 GeneralSkill 把技能名/描述抄进自己那条(『技能库』直接显示)
    for row in _rows_of(cfg, "ChessGeneralSkillConf"):
        sid, nm = _fmt(row.get("SkillID")), _fmt(row.get("SkillName"))
        if sid.isdigit() and nm:
            lord_skills[sid] = {"n": nm, "desc": _fmt(row.get("SkillDesc")), "type": "主公技能"}
    for row in _rows_of(cfg, "ChessGeneralConf"):
        gid, nm = _fmt(row.get("GeneralID")), _fmt(row.get("GeneralName"))
        if not (gid.isdigit() and nm):
            continue
        sid = _fmt(row.get("GeneralSkill"))
        sk = lord_skills.get(sid) or {}
        lords[gid] = {"n": nm, "skill_id": sid, "hp": _fmt(row.get("HitPoint")),
                      "skill": _fmt(sk.get("n")), "desc": _fmt(sk.get("desc")), "type": "主公"}
    return {"chess": chess, "spells": spells, "skills": skills,
            "this_chess": this_chess, "this_spells": this_spells,
            "lords": lords, "lord_skills": lord_skills}


def _replace_json(path, entries, tag):
    """完全替换: 只留这份快照里的 ID(表外的旧条目一律删掉), 但保留原有额外字段(atk/desc/…)。"""
    old = {}
    if os.path.exists(path):
        try:
            old = json.load(open(path, "r", encoding="utf-8")) or {}
        except Exception:
            old = {}
    if not isinstance(old, dict):
        old = {}
    new = {}
    for k, v in entries.items():
        cur = old.get(k)
        row = dict(cur) if isinstance(cur, dict) else {}
        if isinstance(v, dict):
            row.update(v)          # skills.json: 整条 {n, desc}
        else:
            row["n"] = v
            row["src"] = tag
        new[k] = row
    dropped = [k for k in old if k not in new]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(new, f, ensure_ascii=False, indent=2)
    return {"total": len(new), "dropped": len(dropped)}


def write_season_marker(season, version=None, counts=None):
    """写 data/season/season.json —— csv_loader 启动时读它决定 CURRENT_SEASON。

    version 是【资源路径段】(十周年 = 10, 固定), 跟 season(赛季号) 两码事: 以前这里
    写成 `version or season`, 于是切到 S11 就把路径段记成 11 —— 下次照着去抓 /11/ 占位页。
    """
    p = os.path.join(DATA_DIR, "season", "season.json")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    d = {"season": str(season), "version": str(version or DEFAULT_VERSION),
         "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    if counts:
        d["counts"] = counts
    with open(p, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    # v4.113★ 换季 = 【ID 整套换】(21001011 → 21101011, 名字一字不改) —— id_map 里那份
    #   "本季 ID 前缀"缓存是按旧赛季算的, 不清就还按旧季排, 剧本/招募按名字反查会拿到往季 ID。
    try:
        import core.id_map as _im      # noqa: E402
        if hasattr(_im, "reset_season_prefix"):
            _im.reset_season_prefix()
    except Exception: _SIL.note("tools/season_config.py", "write_season_marker → reset_season_prefix()")
    return p


def refresh_readers():
    """让长驻的助手进程立刻按新表/新赛季重读(不刷新的话还是旧赛季的白名单)。"""
    out = {}
    try:
        from core import csv_loader
        if hasattr(csv_loader, "_read_current_season"):
            csv_loader.CURRENT_SEASON = csv_loader._read_current_season()
        csv_loader._cache.clear()
        rows = csv_loader.load_from_csv(force_reload=True)
        out["season"] = getattr(csv_loader, "CURRENT_SEASON", "?")
        out["chess_rows"] = len(rows)
        if hasattr(csv_loader, "save_whitelist"):
            csv_loader.save_whitelist(csv_loader.get_whitelist_from_csv())
            out["whitelist"] = len(csv_loader.get_whitelist_from_csv())
        # v4.101★ 这一行以前是 save_blacklist(get_blacklist_from_csv()) —— 而后者返回的是
        #   【全部赛季的武将】(3346 条), 于是每次赛季更新都把黑名单灌成"禁止一切",
        #   F6 从此一张都买不到。现在: 全量武将写 all_generals.json(只供统计/查看),
        #   黑名单【不动】—— 它是用户自己的名单, 赛季更新不该覆盖它。
        if hasattr(csv_loader, "save_all_generals"):
            csv_loader.save_all_generals(csv_loader.get_all_generals_from_csv())
            out["all_generals"] = len(csv_loader.get_all_generals_from_csv())
    except Exception as e:
        _ui_log("  (刷新 csv_loader 失败, 重启助手即可: %s)" % e)
    try:
        from core import id_map
        _caches = getattr(id_map, "_caches", None)
        if isinstance(_caches, dict):
            _caches.clear()
    except Exception as e:
        _ui_log("  (清 id_map 缓存失败: %s)" % e)
    return out


def backup_current(tag="push"):
    """把现行数据整份备份到 data/_backup/<tag>_<时间>/ —— 推送/替换前必做, 出问题能退回去。"""
    ts = time.strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(DATA_DIR, "_backup", "%s_%s" % (tag, ts))
    n = 0
    for label, path in _all_files():
        if not os.path.exists(path):
            continue
        try:
            d = os.path.join(dest, os.path.dirname(label))
            os.makedirs(d, exist_ok=True)
            shutil.copyfile(path, os.path.join(dest, label.replace("/", os.sep)))
            n += 1
        except Exception as e:
            _ui_log("  (备份 %s 失败: %s)" % (label, e))
    if not n:
        return ""
    try:
        with open(os.path.join(dest, "_还原说明.txt"), "w", encoding="utf-8") as f:
            f.write("这是『推送/替换』之前的现行数据备份(%s)。\n"
                    "要退回: 把这里的文件按同样的相对路径覆盖回 自走棋助手/ 和 自走棋助手/data/ 即可。\n"
                    % time.strftime("%Y-%m-%d %H:%M:%S"))
    except Exception as e:
        _ui_log("  (还原说明没写成: %s)" % e)
    _ui_log("  已备份 %d 个文件 → data/_backup/%s_%s" % (n, tag, ts))
    _prune_backups()
    return dest


# v4.109★ 备份保留策略: 以前【只增不删】—— 每次推送/替换/导入都留一份整包,
#   长期用会堆成一堆没人翻的历史快照(实测 52 个文件 7.5MB, dist/ 里还有一份副本)。
#   真要回滚基本只用最近那一两次, 所以只保留最近 MAX_BACKUPS 份, 更早的自动清掉。
MAX_BACKUPS = 5


def _prune_backups(keep=MAX_BACKUPS):
    """只保留最近 keep 份备份目录(按修改时间倒序)。任何失败都不抛 —— 清理失败不能连累备份。"""
    try:
        root = os.path.join(DATA_DIR, "_backup")
        if not os.path.isdir(root):
            return 0
        subs = [os.path.join(root, d) for d in os.listdir(root)]
        subs = [p for p in subs if os.path.isdir(p)]
        if len(subs) <= keep:
            return 0
        subs.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        removed = 0
        for old in subs[keep:]:
            try:
                shutil.rmtree(old, ignore_errors=True)
                removed += 1
            except Exception: _SIL.note("tools/season_config.py", "shutil.rmtree(旧备份)")
            _ui_log("  (备份超过 %d 份, 已自动清掉最早的 %d 份)" % (keep, removed))
        return removed
    except Exception:
        return 0


def push_to_current(season, mode="merge", json_path=None, do_skills=True,
                    hook_after=None, rebuild_derived=True):
    """把赛季快照【按所有消费方的格式】写进现行数据。

    mode="merge"   推送: 名字映射按 ID 更新, 保留旧的/别的赛季的键; CSV 列序沿用旧表(安全, 推荐)
    mode="replace" 替换: 完全用这份快照重建(表外条目删掉); CSV 列序按快照重排

    返回 report: {mode, season, backup, items[(标签, 说明)], 明细 dict}
    """
    season = str(season)
    mode = "replace" if str(mode).lower().startswith("rep") else "merge"
    cfg, jp = load_snapshot(season, json_path)
    ent = season_entries(season, cfg)
    tag = "season_s%s" % season
    chess_rows = _rows_of(cfg, "ChessConf")
    spell_rows = _rows_of(cfg, "ChessSpellConf")
    if not chess_rows:
        raise RuntimeError("这份快照里 ChessConf 是空的, 不是有效的 TavernChessConfig")
    meta_p = os.path.join(os.path.dirname(jp), "meta.json")
    version = season
    if os.path.exists(meta_p):
        try:
            version = str((json.load(open(meta_p, "r", encoding="utf-8")) or {}).get("version") or season)
        except Exception as e:
            log("  (meta.json 读不了, 资源路径段按赛季号 %s 用: %s)" % (season, e))

    report = {"mode": mode, "season": season, "json": jp, "items": [], "backup": ""}
    _ui_log("[%s] 赛季 S%s ← %s" % ("推送" if mode == "merge" else "替换", season, os.path.basename(jp)))
    report["backup"] = backup_current("merge" if mode == "merge" else "replace")

    # ① 赛季标记(必须先写: csv_loader 靠它算本季白名单)
    write_season_marker(season, version, {"chess": len(ent["chess"]), "spell": len(ent["spells"]),
                                          "chess_this_season": len(ent["this_chess"]),
                                          "spell_this_season": len(ent["this_spells"])})
    report["items"].append(("data/season/season.json", "当前赛季 = S%s" % season))

    # ② 根目录两张表(助手读的就是它们) + 赛季目录留一份快照
    sdir = season_dir(season)
    if mode == "merge":
        h1 = _read_header(ROOT_CHESS_CSV)
        h2 = _read_header(ROOT_SPELL_CSV)
        note_cols = "列序沿用旧表"
    else:
        h1 = h2 = None
        note_cols = "列序按本次快照重排"
    c1, n1 = _write_tsv(ROOT_CHESS_CSV, chess_rows, keep_header=h1)
    c2, n2 = _write_tsv(ROOT_SPELL_CSV, spell_rows, keep_header=h2)
    _write_tsv(os.path.join(sdir, "ChessConf.csv"), chess_rows, keep_header=None)
    _write_tsv(os.path.join(sdir, "ChessSpellConf.csv"), spell_rows, keep_header=None)
    report["items"].append(("ChessConf.csv", "%d 行 / %d 列 (%s)" % (n1, len(c1), note_cols)))
    report["items"].append(("ChessSpellConf.csv", "%d 行 / %d 列" % (n2, len(c2))))

    # ③ 名字映射
    cards_all = dict(ent["chess"])
    cards_all.update(ent["spells"])          # cards.json = 武将 + 锦囊(通用卡牌库)
    plan = [("chess.json", ent["chess"], "全量棋子名"),
            ("cards.json", cards_all, "武将+锦囊名"),
            ("tavern_chess.json", ent["this_chess"], "本季商店武将"),
            ("tavern_spells.json", ent["this_spells"], "本季可用锦囊")]
    detail = {}
    for fn, entries, desc in plan:
        p = os.path.join(DATA_DIR, fn)
        if mode == "merge" and fn != "tavern_chess.json":
            # tavern_chess.json 是"本季可用武将"—— 赛季换了必须整份换掉, 否则招募列表还是旧赛季的卡
            r = _merge_json(p, entries, tag)
            report["items"].append(("data/" + fn, "%s %d 条(增%d/改%d)" % (desc, r["total"], r["added"], r["updated"])))
        else:
            r = _replace_json(p, entries, tag)
            report["items"].append(("data/" + fn, "%s %d 条(替换, 清掉%d)" % (desc, r["total"], r["dropped"])))
        detail[fn] = r
    if do_skills and ent["skills"]:
        r = _replace_json(os.path.join(DATA_DIR, "skills.json"), ent["skills"], tag) if mode == "replace" \
            else _merge_json_dict(os.path.join(DATA_DIR, "skills.json"), ent["skills"])
        report["items"].append(("data/skills.json", "技能 %d 条" % r["total"]))
        detail["skills.json"] = r
    # v4.43★ 主公 / 主公技能: 以前这两张表【一个写入方都没有】(id_map 里那条"抓全量配置"
    # 的老路径已经被赛季推送取代) → lords.json / lord_skills.json 恒为空, 『技能库』查主公
    # 只能查到空的。这里跟 skills.json 一起写, 两种模式都覆盖。
    for fn, entries, desc in (("lords.json", ent["lords"], "主公"),
                              ("lord_skills.json", ent["lord_skills"], "主公技能")):
        if not entries:
            continue
        p = os.path.join(DATA_DIR, fn)
        r = _replace_json(p, entries, tag) if mode == "replace" else _merge_json_dict(p, entries)
        report["items"].append(("data/" + fn, "%s %d 条" % (desc, r["total"])))
        detail[fn] = r

    # ④ 刷新白/黑名单(从刚写的 CSV 生成)
    rd = refresh_readers()
    report["items"].append(("data/lists/whitelist.json",
                            "本季白名单 %s 条" % rd.get("whitelist", "?")))
    detail["readers"] = rd

    # ⑤ 收尾钩子(UI 传 ops.build_hero_picker 来顺带重建武将精选)
    if hook_after:
        try:
            _ui_log("  重建武将精选(hero_picker.json) ...")
            r = hook_after()
            report["items"].append(("data/hero_picker.json", str(r) if r else "已重建"))
        except Exception as e:
            _ui_log("  (重建武将精选失败, 不影响其它数据: %s)" % e)
            report["items"].append(("data/hero_picker.json", "重建失败: %s" % e))

    # ⑥ v4.160★ 一键换季全自动: 推送完通用表后, 顺手把【按赛季的派生表】也重建好,
    #    免得换季后人还得记得单独跑 dev/_rebuild_season_data.py。涵盖: 本季卡池表、
    #    卡图拼音、本季阵容表(缺 xlsx 源则跳过并提示)。用 importlib 直接吃 dev 脚本,
    #    不依赖它是否是包(项目里 dev/ 没有 __init__)。
    if rebuild_derived:
        try:
            import importlib.util as _ilu
            _rp = os.path.join(ROOT, "dev", "_rebuild_season_data.py")
            _spec = _ilu.spec_from_file_location("rebuild_season_data", _rp)
            _mod = _ilu.module_from_spec(_spec)
            _spec.loader.exec_module(_mod)
            _mod.rebuild(season=season, clean=False, log=_ui_log)
            report["items"].append(("data/tavern_chess_s%s.json" % season,
                                    "派生表已随推送重建"))
        except Exception as e:
            _ui_log("  (派生表重建失败, 可手动跑 dev/_rebuild_season_data.py: %s)" % e)
            report["items"].append(("派生表重建", "失败: %s" % e))

    report["detail"] = detail
    _ui_log("[%s] 完成: 棋子 %d / 锦囊 %d / 本季武将 %d / 本季锦囊 %d"
            % ("推送" if mode == "merge" else "替换", len(ent["chess"]), len(ent["spells"]),
               len(ent["this_chess"]), len(ent["this_spells"])))
    if report["backup"]:
        _ui_log("  想退回: 用 data/_backup 里那份覆盖回来")
    return report


def _merge_json_dict(path, entries):
    """skills.json 专用: 值是 dict, 只补字段不删键(推送模式)。"""
    old = {}
    if os.path.exists(path):
        try:
            old = json.load(open(path, "r", encoding="utf-8")) or {}
        except Exception:
            old = {}
    if not isinstance(old, dict):
        old = {}
    for k, v in entries.items():
        cur = old.get(k)
        row = dict(cur) if isinstance(cur, dict) else {}
        row.update(v)
        row.setdefault("src", "season")
        old[k] = row
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(old, f, ensure_ascii=False, indent=2)
    return {"total": len(old), "added": 0, "updated": len(entries)}


_TABLES_CACHE = {}


def _json_tables(season, jp):
    """快照里的表行数。带缓存(键含 mtime+size, 文件一改自动失效)。

    为什么要缓存: 解密后的 json 有十几 MB, 而 summary() 在每次切到『赛季数据』tab 都会被调用,
    每次都 json.load 会明显卡顿。这里只缓存算出来的几十个数字, 不缓存 json 本体(省内存)。
    """
    try:
        st = os.stat(jp)
        key = (str(season), str(jp), int(st.st_mtime), st.st_size)
    except Exception:
        return None
    hit = _TABLES_CACHE.get(key)
    if hit is not None:
        return dict(hit)
    cfg = load_snapshot(season, jp)[0]
    ent = season_entries(season, cfg)
    out = {"tables": {t: len(_rows_of(cfg, t)) for t in ("ChessConf", "ChessSpellConf",
                                                         "ChessSkillConf", "ChessGeneralConf",
                                                         "ChessGeneralSkillConf")},
           "json_size": os.path.getsize(jp)}
    out["tables"]["本季武将(IsShop=1)"] = len(ent["this_chess"])
    out["tables"]["本季可用锦囊"] = len(ent["this_spells"])
    out["tables"]["技能可查(ChessSkillConf)"] = len(ent["skills"])       # ★v4.43 技能库查得到多少
    out["tables"]["主公+主公技能"] = len(ent["lords"]) + len(ent["lord_skills"])
    _TABLES_CACHE.clear()          # 只留最近一份, 避免文件反复变动时越攒越多
    _TABLES_CACHE[key] = dict(out)
    return out


def summary(season):
    """『查看数据』用: 快照概览 + 现行数据现状"""
    season = str(season)
    sdir = season_dir(season)
    jp = os.path.join(sdir, "TavernChessConfig.json")
    info = {"season": season, "dir": sdir, "json": jp, "has_json": os.path.exists(jp),
            "tables": {}, "files": [], "current_season": None}
    for label, path in _all_files():
        info["files"].append((label, path, os.path.exists(path),
                              os.path.getsize(path) if os.path.exists(path) else 0))
    try:
        from core import csv_loader
        info["current_season"] = csv_loader.CURRENT_SEASON
    except Exception as e:
        # 界面上会提示"(未知)", 这里不打断概览显示, 但把原因带上便于排查
        info["current_season_error"] = str(e)
    if info["has_json"]:
        try:
            t = _json_tables(season, jp) or {}
            info["tables"] = t.get("tables", {})
            info["json_size"] = t.get("json_size", 0)
        except Exception as e:
            info["error"] = str(e)
    return info


_ROWS_CACHE = {}


# ================================================================ 赛季切换向导 (v4.47★)
# 为什么要有这一块
# ----------------
# 『赛季数据』tab 以前只有「写入」没有「先看会变成什么」: 用户按下『推送』/『替换』的瞬间,
# 表就已经换掉了 —— 换错只能靠 data/_backup 手工还原。而换表真正的坑不在"写不写得进去",
# 而在【换完之后哪些卡没了、哪些ID还被人引用着】:
#   · 上一季商店里的卡这一季退场 → 以卡名保存的白名单/剧本目标会找不到卡;
#   · 『随征卡牌ID』里存的是【技能ID】(如 942311 黄巾兵), 它跟着赛季走, 退场了插件会静默不生效;
#   · 有些卡只是【改名】, 按名字反查的配置会全部失配 —— 这一类比"退场"更隐蔽, 必须先列出来。
# 所以这里给"换表前"提供一次对齐检查: 本季快照(要写入的) vs 现行数据(现在用的)。
def _read_tsv_rows(path):
    """读现行那张 TSV(CSV) → [{列名: 值}]。读不动就返回空列表(不打断向导)。"""
    rows = []
    if not os.path.exists(path):
        return rows
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            head = f.readline().rstrip("\r\n").split("\t")
            if len(head) <= 3:
                return rows
            for line in f:
                line = line.rstrip("\r\n")
                if not line:
                    continue
                vals = line.split("\t")
                rows.append({head[i]: (vals[i] if i < len(vals) else "") for i in range(len(head))})
    except Exception as e:
        log("  (读到现行表异常, 这次按空表算: %s: %s)" % (os.path.basename(path), e))
    return rows


def _live_store(kind):
    """现行『本季』表 {id: name} —— 商店武将 / 可用锦囊。

    优先读推送流程写的那两份小 json(data/tavern_chess.json / tavern_spells.json), 快且就是
    『现行数据里的本季』; 没有就按现在认的赛季号从 CSV 现算(SeasonID + IsShop/IsAvailable)。
    """
    fn = "tavern_chess.json" if kind == "chess" else "tavern_spells.json"
    p = os.path.join(DATA_DIR, fn)
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict) and d:
                return {str(k): _fmt(v if not isinstance(v, dict) else v.get("n")) for k, v in d.items()}
        except Exception as e:
            log("  (读 %s 失败, 改用 CSV 现算: %s)" % (fn, e))
    try:
        from core import csv_loader
        cur = str(getattr(csv_loader, "CURRENT_SEASON", "") or "")
    except Exception:
        cur = ""
    if not cur:
        return {}
    out = {}
    if kind == "chess":
        for r in _read_tsv_rows(ROOT_CHESS_CSV):
            cid, nm = _fmt(r.get("ChessID")), _fmt(r.get("ChessName"))
            if cid.isdigit() and nm and _fmt(r.get("SeasonID")) == cur and _fmt(r.get("IsShop")) == "1":
                out[cid] = nm
    else:
        for r in _read_tsv_rows(ROOT_SPELL_CSV):
            sid, nm = _fmt(r.get("SpellID")), _spell_display_name(r)
            if sid.isdigit() and nm and _fmt(r.get("SeasonID")) == cur \
                    and _fmt(r.get("IsAvailable")) in ("1", ""):
                out[sid] = nm
    return out


def _refs_of(ids, limit=60):
    """哪些【列表配置】还引用着这些 ID —— 换表后它们会静默失效, 所以要在向导里点名。

    只查 data/lists/*.json(7 个列表)。不查剧本: 剧本存的是中文卡名, 改名/退场的影响只能靠
    上面 renamed/removed 两张单子自己看, 硬扫剧本只会给出对不上的结果。
    """
    ids = set(str(i) for i in (ids or []) if str(i))
    if not ids:
        return []
    try:
        from core import list_config as LC
    except Exception as e:
        log("  (读列表配置失败, 跳过引用检查: %s)" % e)
        return []
    label = {"auto_buy": "自动招募", "multi_buy": "多选购买", "permanent_keep": "永久保留",
             "discard_target": "遣散目标", "followup": "随征卡牌ID",
             "whitelist": "白名单", "blacklist": "黑名单"}
    out = []
    for name in getattr(LC, "LIST_FILES", {}):
        try:
            d = LC.load_list(name) or {}
        except Exception as e:
            log("  (读列表 %s 失败, 跳过: %s)" % (name, e))
            continue
        hit = [str(i) for i in (d.get("ids") or []) if str(i) in ids]
        if hit:
            out.append({"list": label.get(name, name), "ids": hit[:limit], "count": len(hit)})
    return out


def diff_season(season):
    """赛季切换向导: 本季快照(要写入的) vs 现行数据(现在用的) —— 换表前先看会变成什么。

    返回:
      {
        "ok": bool, "season": .., "cur_season": ..,
        "chess":  {"added":[(id,name)], "removed":[(id,name)], "renamed":[(id,old,new)], "n_new":, "n_cur":},
        "spell":  同上,
        "refs":   [{"list": 列表名, "ids": [...], "count": n}]   # 退场/改名 ID 还被谁引用着
      }
    只读, 不写任何文件 —— 向导里的『一键切换』才动数据。
    """
    season = str(season)
    out = {"ok": False, "season": season, "cur_season": None, "error": "",
           "chess": {}, "spell": {}, "refs": [], "ref_ids": []}
    try:
        from core import csv_loader
        out["cur_season"] = getattr(csv_loader, "CURRENT_SEASON", None)
    except Exception as e:
        out["cur_season_error"] = str(e)

    jp = os.path.join(season_dir(season), "TavernChessConfig.json")
    if not os.path.exists(jp):
        out["error"] = ("还没有赛季 S%s 的解密快照, 没法对比。\n"
                        "  先点上面『① 一键下载解析』, 或用『导入本地数据』选一份 json/.sgs。"
                        % season)
        return out
    try:
        cfg, _jp = load_snapshot(season, jp)
        ent = season_entries(season, cfg)
    except Exception as e:
        out["error"] = "读快照失败: %s" % e
        return out

    def _diff(new_map, cur_map):
        new_map = {str(k): _fmt(v) for k, v in (new_map or {}).items() if _fmt(v)}
        cur_map = {str(k): _fmt(v) for k, v in (cur_map or {}).items() if _fmt(v)}
        added = sorted(((k, new_map[k]) for k in new_map if k not in cur_map), key=lambda x: x[1])
        removed = sorted(((k, cur_map[k]) for k in cur_map if k not in new_map), key=lambda x: x[1])
        renamed = sorted(((k, cur_map[k], new_map[k]) for k in new_map
                          if k in cur_map and cur_map[k] != new_map[k]), key=lambda x: x[2])
        return {"added": added, "removed": removed, "renamed": renamed,
                "n_new": len(new_map), "n_cur": len(cur_map)}

    out["chess"] = _diff(ent["this_chess"], _live_store("chess"))
    out["spell"] = _diff(ent["this_spells"], _live_store("spell"))

    # 引用检查的对象是【会失效的 ID】: 退场 + 改名(改名的 ID 还在, 但按名字保存的配置已经对不上)
    ref_ids = [i for i, _n in out["chess"]["removed"]]
    ref_ids += [i for i, _o, _n in out["chess"]["renamed"]]
    out["ref_ids"] = ref_ids
    out["refs"] = _refs_of(ref_ids)
    out["ok"] = True
    log("== 赛季切换向导 · S%s(要写入) vs S%s(现行) ==" % (season, out["cur_season"] or "?"))
    for kind, label in (("chess", "棋子"), ("spell", "锦囊")):
        d = out[kind]
        log("  %s: 要写入 %d 个 / 现行 %d 个 —— 新增 %d, 退场 %d, 改名 %d"
            % (label, d["n_new"], d["n_cur"], len(d["added"]), len(d["removed"]), len(d["renamed"])))
        for i, n in d["removed"][:8]:
            log("      退场: %s %s" % (i, n))
        for i, o, n in d["renamed"][:8]:
            log("      改名: %s  %s → %s" % (i, o, n))
    if out["refs"]:
        for r in out["refs"]:
            log("  ⚠ 列表『%s』还引用着 %d 个会失效的 ID: %s"
                % (r["list"], r["count"], ", ".join(r["ids"][:10])))
    else:
        log("  引用检查: 没有列表配置引用到会失效的 ID。")
    return out




def _extract_rows(cfg, season, kind, want_season, limit):
    """want_season: ""/None = 不过滤; 否则只保留 SeasonID == want_season 的行。

    v4.42★ 这里以前是 only_current(bool), 只能"筛本赛季" —— 于是『查看数据』看不了别的赛季。
      但一份快照里【本来就带着往季的行】(SeasonID=9/8/…), 只是没有入口把它们筛出来。
    """
    out = []
    want = str(want_season or "").strip()
    if kind == "spell":
        for row in _rows_of(cfg, "ChessSpellConf"):
            sid, nm = _fmt(row.get("SpellID")), _fmt(row.get("SpellName"))
            if not (sid.isdigit() and nm):
                continue
            if want and _fmt(row.get("SeasonID")) != want:
                continue
            out.append({"id": sid, "name": nm, "rank": _fmt(row.get("SpellRank")),
                        "season": _fmt(row.get("SeasonID")), "shop": _fmt(row.get("IsAvailable")),
                        "desc": _fmt(row.get("SpellSkillDesc"))})
            if len(out) >= limit:
                break
    else:
        for row in _rows_of(cfg, "ChessConf"):
            cid, nm = _fmt(row.get("ChessID")), _fmt(row.get("ChessName"))
            if not (cid.isdigit() and nm):
                continue
            if want and _fmt(row.get("SeasonID")) != want:
                continue
            out.append({"id": cid, "name": nm, "rank": _fmt(row.get("ChessRank")),
                        "season": _fmt(row.get("SeasonID")), "shop": _fmt(row.get("IsShop")),
                        "desc": _fmt(row.get("ChessSkillDesc"))})
            if len(out) >= limit:
                break
    return out


def view_rows(season, kind="chess", keyword="", only_current=False, limit=8000, season_filter=None):
    """『查看数据』表格: 棋子表 / 锦囊表。

    带缓存(键含 json 的 mtime+size, 快照一更新自动失效): 界面每次刷新/改关键词都重读十几 MB 的
    json 会明显卡; 现在只在第一次(每种 kind × 过滤条件)解析一次, 关键词在内存里筛。

    v4.42★ 新增 season_filter: 在【同一份快照】里按 SeasonID 筛任意赛季 —— 手上只有 s10 快照时
      也能看 S9 的条目。优先于 only_current。
      区分两个概念(以前被混成一个, 所以"看别的赛季"在界面上做不到):
        · season 参数   = 读哪份快照(data/season/s{season}/TavernChessConfig.json)
        · season_filter = 表格里筛出 SeasonID 等于哪个赛季的行
    """
    season = str(season)
    kind = "spell" if str(kind).lower().startswith("sp") else "chess"
    only = bool(only_current)
    want = "" if season_filter is None else str(season_filter).strip()
    if not want and only:
        want = season
    jp = os.path.join(season_dir(season), "TavernChessConfig.json")
    try:
        st = os.stat(jp)
        key = (season, kind, want, int(st.st_mtime), st.st_size)
    except Exception:
        key = None
    rows = _ROWS_CACHE.get(key) if key else None
    if rows is None:
        cfg, _jp = load_snapshot(season, jp)
        rows = _extract_rows(cfg, season, kind, want, limit)
        if key:
            _ROWS_CACHE.clear()          # 只留最近一份, 避免越攒越多
            _ROWS_CACHE[key] = rows
    kw = (keyword or "").strip()
    if not kw:
        return list(rows)
    return [r for r in rows if kw in r["name"] or kw in r["id"]]


# ================================================================ 技能 / 主公 查询 (v4.43★)
# 为什么单开这一块
# ----------------
# 『查看数据』以前只认两张表: 棋子(ChessConf) / 锦囊(ChessSpellConf)。可助手里有几个面板
# 用的【不是棋子ID, 而是技能ID】—— 最典型的是『随征卡牌ID』: 942311 是黄巾兵的「随征」技能
# (ChessSkillConf.SkillID)。用户报的原话是"三张表里都不存在", 这是对的, 因为
# chess.json / cards.json / tavern_chess.json 全都是【棋子】表, 技能不在里面 ——
# 于是全界面没有任何地方能查到"942311 到底是谁、什么效果", 只能靠纯数字 ID 硬加。
# 主公(ChessGeneralConf.GeneralID)和主公技能(ChessGeneralSkillConf.SkillID)同理, 以前
# 只有 lords.json / lord_skills.json 两个文件, 而它们【从来没有任何写入方】(恒为空)。
#
# 这里把三张表统一成一个查询入口, 返回字段与 view_rows 一致(id/name/type/tag/desc),
# 界面可以直接套同一套表格列。
SKILL_KIND_LABEL = {"skill": "武将技能", "lord": "主公", "lordskill": "主公技能"}

_SKILL_KIND_ALIAS = {
    "skill": "skill", "skills": "skill", "chessskill": "skill", "chessskillconf": "skill",
    "技能": "skill", "武将技能": "skill", "棋子技能": "skill", "随征": "skill",
    "lord": "lord", "lords": "lord", "general": "lord", "chessgeneralconf": "lord",
    "主公": "lord", "主公id": "lord",
    "lordskill": "lordskill", "lordskills": "lordskill", "generalskill": "lordskill",
    "chessgeneralskillconf": "lordskill", "主公技能": "lordskill", "主公技能id": "lordskill",
}

# 没有赛季快照时, 每类去读哪几份现行数据文件(推送流程写的 {id: {n, desc, ...}})
_SKILL_FALLBACK_FILES = {
    "skill": ("skills.json",),
    "lord": ("lords.json", "lord_skills.json"),
    "lordskill": ("lord_skills.json",),
}


def norm_skill_kind(kind):
    """把界面/命令行传进来的类别名归一化成 skill / lord / lordskill(认不出按武将技能)。"""
    return _SKILL_KIND_ALIAS.get(str(kind or "").strip().lower(), "skill")


def _as_int(v, default=0):
    """能转 int 就转, 转不了返回 default(表里这些字段偶尔是字符串/None/列表)。"""
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return default


def _keyword_map(cfg):
    """ChessClientKeywordConf → {ChessSkillType: "词条: 完整文案"}。

    为什么需要: 有些技能(实测 942311 黄巾兵)的 SkillDesc 就只有「随征」两个字,
    光看这一列等于什么都查不到; 完整文案在这张词条表里 ——
    ChessSkillType=10 随征 = 「无法从手牌上阵。使用后, 选择1名同势力友方, 使其获得自身属性和技能效果」。
    """
    out = {}
    for row in _rows_of(cfg, "ChessClientKeywordConf"):
        t = _as_int(row.get("ChessSkillType"))
        kw = _fmt(row.get("Keyword"))
        if not (t and kw):
            continue
        d = _fmt(row.get("KeywordDesc"))
        out[t] = (kw + ": " + d) if d else kw
    return out


def _skill_desc_of(row, kwmap):
    """拼出人能看懂的技能说明: SkillDesc + 词条全文 + 效果(ChessBuffTips 竖线后那段) + 选人提示。"""
    parts = [_fmt(row.get("SkillDesc"))]
    raws = row.get("ChessSkillKeyword")
    if not isinstance(raws, (list, tuple)):
        raws = [raws] if raws not in (None, "") else []
    seen = []
    for t in raws:
        txt = kwmap.get(_as_int(t))
        if txt and txt not in seen:
            seen.append(txt)
    if seen:
        parts.append("〔" + " / ".join(seen) + "〕")
    tips = _fmt(row.get("ChessBuffTips"))
    if "|" in tips:
        eff = tips.split("|", 1)[1].strip()
        if eff and eff not in parts[0]:
            parts.append("→ " + eff)
    act = _fmt(row.get("ActiveTips"))
    if act and act not in parts[0]:
        parts.append("〔选人〕" + act)
    return "  ".join(p for p in parts if p)


def _skill_tag_of(row, is_lord_skill=False):
    """技能行上的短标记: 需选目标 / 场景技 / 主动|被动。

    「需选目标」= ChessSkillConf.TargetTyp1 == 21(与 core/csv_loader 的 _skill_needs_pick 同一判据)——
    它正是"用出去之后还会弹一个挑牌窗"的那种技能, 查 ID 时最该看到的就是这一条。
    """
    tags = []
    if not is_lord_skill and _as_int(row.get("TargetTyp1")) == 21:
        tags.append("需选目标")
    if _as_int(row.get("IsScenarioSkill")) == 1:
        tags.append("场景技")
    if is_lord_skill:
        # 1=招募阶段能主动放, 2=战斗中被动触发(与描述自洽: 1002 点兵"招募阶段限1次"
        # vs 1003 黄天"每阵亡7个友方…")。取不到就不标, 不猜。
        tags.append({1: "主动", 2: "被动"}.get(_as_int(row.get("GeneralSkillType")), ""))
    return " ".join(t for t in tags if t)


def _extract_skill_rows(cfg, kind, limit):
    """三张表 → 统一的 [{id,name,type,tag,desc}]。"""
    out = []
    kwmap = _keyword_map(cfg)
    if kind == "lord":
        skills = {}
        for r in _rows_of(cfg, "ChessGeneralSkillConf"):
            skills[_as_int(r.get("SkillID"))] = r
        for row in _rows_of(cfg, "ChessGeneralConf"):
            gid, nm = _fmt(row.get("GeneralID")), _fmt(row.get("GeneralName"))
            if not (gid.isdigit() and nm):
                continue
            sid = _as_int(row.get("GeneralSkill"))
            sk = skills.get(sid) or {}
            sname, sdesc = _fmt(sk.get("SkillName")), _fmt(sk.get("SkillDesc"))
            tag = "体力%s · 主公技能ID %s" % (_fmt(row.get("HitPoint")) or "?", sid or "?")
            if _as_int(row.get("IsAvailable"), 1) != 1:
                tag += " · 未开放"
            out.append({"id": gid, "name": nm, "type": SKILL_KIND_LABEL["lord"], "tag": tag,
                        "desc": ("%s: %s" % (sname, sdesc)) if sname else _fmt(row.get("SearchKey"))})
            if len(out) >= limit:
                break
    elif kind == "lordskill":
        for row in _rows_of(cfg, "ChessGeneralSkillConf"):
            sid, nm = _fmt(row.get("SkillID")), _fmt(row.get("SkillName"))
            if not (sid.isdigit() and nm):
                continue
            out.append({"id": sid, "name": nm, "type": SKILL_KIND_LABEL["lordskill"],
                        "tag": _skill_tag_of(row, True), "desc": _fmt(row.get("SkillDesc"))})
            if len(out) >= limit:
                break
    else:
        for row in _rows_of(cfg, "ChessSkillConf"):
            sid, nm = _fmt(row.get("SkillID")), _fmt(row.get("SkillName"))
            if not (sid.isdigit() and nm):
                continue
            out.append({"id": sid, "name": nm, "type": SKILL_KIND_LABEL["skill"],
                        "tag": _skill_tag_of(row), "desc": _skill_desc_of(row, kwmap)})
            if len(out) >= limit:
                break
    return out


def _skill_rows_from_files(kind, limit):
    """兜底: 没有赛季快照就查现行数据文件(键就是 ID)。老用户不点『一键下载解析』也能查。"""
    def _read(fn):
        p = os.path.join(DATA_DIR, fn)
        if not os.path.exists(p):
            return {}
        try:
            with open(p, "r", encoding="utf-8") as f:
                d = json.load(f)
        except Exception as e:
            log("  (读 %s 失败: %s)" % (fn, e))
            return {}
        return d if isinstance(d, dict) else {}

    label = SKILL_KIND_LABEL[kind]
    out = []
    if kind == "lord":
        lsk = _read("lord_skills.json")
        for gid, it in (_read("lords.json") or {}).items():
            if not isinstance(it, dict):
                continue
            sid = _fmt(it.get("skill_id"))
            sk = lsk.get(sid) or {}
            sname, sdesc = _fmt(sk.get("n")), _fmt(sk.get("desc"))
            out.append({"id": _fmt(gid), "name": _fmt(it.get("n")), "type": label,
                        "tag": "体力%s · 主公技能ID %s" % (_fmt(it.get("hp")) or "?", sid or "?"),
                        "desc": ("%s: %s" % (sname, sdesc)) if sname else _fmt(it.get("desc"))})
    else:
        for sid, it in (_read(_SKILL_FALLBACK_FILES[kind][0]) or {}).items():
            if not isinstance(it, dict):
                continue
            out.append({"id": _fmt(sid), "name": _fmt(it.get("n")), "type": label,
                        "tag": "", "desc": _fmt(it.get("desc"))})
    out = [r for r in out if r["id"] and r["name"]]
    out.sort(key=lambda r: (_as_int(r["id"]), r["name"]))
    return out[:limit]


_SKILL_CACHE = {}


def view_skills(season, kind="skill", keyword="", limit=8000):
    """『技能库』表格: 武将技能(ChessSkillConf) / 主公(ChessGeneralConf) / 主公技能(ChessGeneralSkillConf)。

    返回 [{id, name, type, tag, desc}], 字段风格与 view_rows 一致, 界面可套同样的列。
    带缓存(键含快照 mtime+size): 单表两千多行, 每次切 tab 都重读十几 MB 的 json 会明显卡。
    快照不存在时退回现行数据文件(data/skills.json / lords.json / lord_skills.json),
    缓存键改成那几份文件的 mtime+size(文件一改就失效)。

    注意: 这三张表【没有 SeasonID 字段】—— 快照本身就是"那个赛季的版本", 所以这里不做赛季筛行,
    赛季号只决定读哪份快照(与 view_rows 的两个概念一致, 只是这里只剩前一个)。
    """
    season = str(season)
    kind = norm_skill_kind(kind)
    jp = os.path.join(season_dir(season), "TavernChessConfig.json")
    has_snapshot = os.path.exists(jp)
    if has_snapshot:
        try:
            st = os.stat(jp)
            key = (season, kind, ("snap", int(st.st_mtime), st.st_size))
        except OSError as e:
            log("  (快照 stat 失败, 这次不用缓存: %s)" % e)
            key = None
    else:
        stamps = []
        for fn in _SKILL_FALLBACK_FILES[kind]:
            try:
                st = os.stat(os.path.join(DATA_DIR, fn))
                stamps.append((fn, int(st.st_mtime), st.st_size))
            except OSError:
                stamps.append((fn, 0, 0))
        key = (season, kind, tuple(stamps))
    rows = _SKILL_CACHE.get(key) if key else None
    if rows is None:
        if has_snapshot:
            cfg, _jp = load_snapshot(season, jp)
            rows = _extract_skill_rows(cfg, kind, limit)
        else:
            rows = _skill_rows_from_files(kind, limit)
        if key:
            _SKILL_CACHE.clear()       # 只留最近一份, 避免越攒越多
            _SKILL_CACHE[key] = rows
    kw = (keyword or "").strip().lower()
    if not kw:
        return list(rows)
    return [r for r in rows
            if kw in r["name"].lower() or kw in r["id"] or kw in r["desc"].lower()]


def export_all(season, dest):
    """导出这一份赛季数据(所有格式, 可直接拿回来『导入/替换』)到指定目录。"""
    season = str(season)
    cfg, jp = load_snapshot(season)
    ent = season_entries(season, cfg)
    os.makedirs(dest, exist_ok=True)
    sdir = season_dir(season)
    written = []

    def _cp(src, name):
        if os.path.exists(src):
            shutil.copyfile(src, os.path.join(dest, name))
            written.append(name)

    _cp(jp, "TavernChessConfig.json")
    _cp(os.path.join(sdir, "TavernChessConfig.sgs"), "TavernChessConfig.sgs")
    _cp(os.path.join(sdir, "resc"), "resc")
    _cp(os.path.join(sdir, "meta.json"), "meta.json")
    _write_tsv(os.path.join(dest, "ChessConf.csv"), _rows_of(cfg, "ChessConf"), keep_header=None)
    _write_tsv(os.path.join(dest, "ChessSpellConf.csv"), _rows_of(cfg, "ChessSpellConf"), keep_header=None)
    written += ["ChessConf.csv", "ChessSpellConf.csv"]

    tag = "season_s%s" % season
    maps = [("chess.json", ent["chess"], "全量棋子名"),
            ("cards.json", dict(ent["chess"], **ent["spells"]), "武将+锦囊名"),
            ("tavern_chess.json", ent["this_chess"], "本季商店武将"),
            ("tavern_spells.json", ent["this_spells"], "本季可用锦囊")]
    for fn, entries, _desc in maps:
        data = {}
        for k, v in entries.items():
            data[k] = {"n": v, "src": tag}
        with open(os.path.join(dest, fn), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        written.append(fn)
    with open(os.path.join(dest, "skills.json"), "w", encoding="utf-8") as f:
        json.dump(ent["skills"], f, ensure_ascii=False, indent=2)
    written.append("skills.json")
    # v4.43★ 主公 / 主公技能: 导出包也带上, 换台机器导入后『技能库』查主公直接有数据
    with open(os.path.join(dest, "lords.json"), "w", encoding="utf-8") as f:
        json.dump(ent["lords"], f, ensure_ascii=False, indent=2)
    with open(os.path.join(dest, "lord_skills.json"), "w", encoding="utf-8") as f:
        json.dump(ent["lord_skills"], f, ensure_ascii=False, indent=2)
    written += ["lords.json", "lord_skills.json"]
    # 注意: 这里【只写进导出目录】, 绝不能调 write_season_marker ——
    #   那会把助手正在用的 data/season/season.json 改成这个赛季, 等于"导出一下就把现行数据换季了"。
    with open(os.path.join(dest, "season.json"), "w", encoding="utf-8") as f:
        json.dump({"season": season, "version": season,
                   "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "counts": {"chess": len(ent["chess"]), "spell": len(ent["spells"]),
                              "chess_this_season": len(ent["this_chess"]),
                              "spell_this_season": len(ent["this_spells"])}},
                  f, ensure_ascii=False, indent=2)
    written.append("season.json")
    with open(os.path.join(dest, "_导出说明.txt"), "w", encoding="utf-8") as f:
        f.write(
            "自走棋赛季数据导出包 (赛季 S%s)\n"
            "导出时间: %s\n\n"
            "内容:\n"
            "  TavernChessConfig.json/.sgs  原始配置(解密后/解密前)\n"
            "  ChessConf.csv / ChessSpellConf.csv   棋子表/锦囊表(制表符, 助手根目录用的就是它)\n"
            "  chess.json / cards.json / tavern_chess.json / tavern_spells.json / skills.json\n"
            "  lords.json / lord_skills.json\n"
            "        名字 / 技能 / 主公 / 主公技能表(助手 data/ 下的现行格式)\n"
            "  season.json   赛季标记\n"
            "  resc          解密用的 wasm(离线解密别人才需要)\n\n"
            "怎么用: 助手 → 赛季数据 → 『导入』选这个目录(或里面的 TavernChessConfig.json),\n"
            "        再点『推送到现行数据』/『替换现行数据』。\n"
            % (season, time.strftime("%Y-%m-%d %H:%M:%S")))
    written.append("_导出说明.txt")
    return written


def import_any(src, season=None, do_push=False, mode="merge", _do_backup=True):
    """导入: 认 .json(快照) / .sgs(加密配置) / 目录(导出包) / .csv(直接换根目录表)。

    返回 (说明文字, 赛季目录) —— 赛季号没给就沿用 src 里的/当前标记。
    """
    global _UI_LOG_OWNER
    src = os.path.abspath(src)
    if not os.path.exists(src):
        raise RuntimeError("找不到: %s" % src)
    # 导入同样是"覆盖现行数据"的操作 → 先整份备份, 认错了能退回
    if _do_backup:
        backup_current("import")
    # 目录 = 导出包 → 逐个认
    if os.path.isdir(src):
        got = []
        for fn in os.listdir(src):
            p = os.path.join(src, fn)
            if fn == "TavernChessConfig.json":
                import_any(p, season, _do_backup=False); got.append(fn)
            elif fn == "meta.json" and season:
                # 连同 meta.json 一起收: 里面记着资源路径段(推送时要用它算版本)
                dst = os.path.join(season_dir(str(season)), "meta.json")
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copyfile(p, dst)
                got.append(fn)
            elif fn.lower().endswith(".csv"):
                shutil.copyfile(p, ROOT_CHESS_CSV if "spell" not in fn.lower() else ROOT_SPELL_CSV)
                got.append(fn)
            elif fn in ("chess.json", "cards.json", "tavern_chess.json", "tavern_spells.json",
                        "skills.json", "lords.json", "lord_skills.json"):
                dst = os.path.join(DATA_DIR, fn)
                if os.path.exists(dst):
                    try:
                        shutil.copyfile(dst, dst + ".bak")
                    except Exception as e:
                        _ui_log("  (旧 %s 备份失败: %s)" % (fn, e))
                shutil.copyfile(p, dst)
                got.append(fn)
            elif fn == "season.json":
                try:
                    s = str((json.load(open(p, "r", encoding="utf-8")) or {}).get("season") or "")
                    if s:
                        write_season_marker(s, s)
                        got.append(fn + "(赛季=" + s + ")")
                except Exception as e:
                    _ui_log("  (season.json 读不了: %s)" % e)
        refresh_readers()
        return ("导入目录 %s: %s" % (os.path.basename(src), ", ".join(got) or "没认出来的文件"), None)

    low = src.lower()
    if low.endswith(".csv") or low.endswith(".tsv"):
        dst = ROOT_SPELL_CSV if "spell" in os.path.basename(src).lower() else ROOT_CHESS_CSV
        if os.path.exists(dst):
            try:
                shutil.copyfile(dst, dst + ".bak")
            except Exception as e:
                _ui_log("  (旧表备份失败: %s)" % e)
        shutil.copyfile(src, dst)
        rd = refresh_readers()
        return ("导入表格 %s → %s (当前赛季 S%s, %s 行)"
                % (os.path.basename(src), os.path.basename(dst), rd.get("season"), rd.get("chess_rows")), None)

    season = str(season or "")
    if low.endswith(".sgs"):
        if not season:
            season = os.path.basename(src).split(".")[0][-2:] or "10"
            season = "".join(ch for ch in season if ch.isdigit()) or "10"
        sdir = season_dir(season)
        resc = _ensure_resc(sdir, season)
        out = os.path.join(sdir, "TavernChessConfig.json")
        sgs_decrypt_util.decrypt_sgs_file(input_path=_P(src), wasm_path=_P(resc), output_path=_P(out))
        _check_json(out)
        return ("导入并解密 %s → %s" % (os.path.basename(src), out), sdir)

    # json: 是"配置快照"还是"名字映射"?
    with open(src, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and ("ChessConf" in data or "ChessSpellConf" in data):
        if not season:
            season = "10"
        sdir = season_dir(season)
        shutil.copyfile(src, os.path.join(sdir, "TavernChessConfig.json"))
        return ("导入配置快照 %s → 赛季目录 s%s" % (os.path.basename(src), season), sdir)
    if isinstance(data, dict) and os.path.basename(src) == "season.json" and data.get("season"):
        # 导出包里那份 season.json 被单独挑出来导入 → 当作"改当前赛季标记"
        s = str(data["season"])
        write_season_marker(s, s)
        refresh_readers()
        return ("导入赛季标记 season.json → 当前赛季 = S%s" % s, None)
    if isinstance(data, dict):
        # 名字映射: 按文件名对号入座
        name = os.path.basename(src)
        dst = os.path.join(DATA_DIR, name if name.endswith(".json") else "chess.json")
        if os.path.exists(dst):
            try:
                shutil.copyfile(dst, dst + ".bak")
            except Exception as e:
                _ui_log("  (旧文件备份失败: %s)" % e)
        shutil.copyfile(src, dst)
        refresh_readers()
        return ("导入名字映射 %s → data/%s (%d 条)" % (name, os.path.basename(dst), len(data)), None)
    raise RuntimeError("认不出这个 json 的用途(既不是 TavernChessConfig 也不是名字映射)")


_UI_LOG_OWNER = None


def main():
    ap = argparse.ArgumentParser(description="自走棋赛季配置: 抓取/解密/导出CSV/转JSON/推送/替换")
    ap.add_argument("cmd", choices=["all", "fetch", "csv", "json", "check", "update-check",
                                    "push", "replace", "export", "import", "view"],
                    help="要做什么 (push=推送到现行数据, replace=替换现行数据, update-check=只看官方有没有更新)")
    ap.add_argument("--season", default="10", help="赛季号(默认 10)")
    ap.add_argument("--version", default=None, help="官方资源路径段(默认=赛季号, 如 10 → /10/pc/...)")
    ap.add_argument("--resc", default=None, help="解密用的 resc 文件路径")
    ap.add_argument("--sgs", default=None, help="直接解密一个 TavernChessConfig.sgs")
    ap.add_argument("--from-json", dest="from_json", default=None, help="直接使用现成的 json(不联网)")
    ap.add_argument("--json-path", dest="json_path", default=None, help="csv/json 步骤用的源 json")
    ap.add_argument("--out", default=None, help="export: 导出目录")
    ap.add_argument("--file", default=None, help="import: 要导入的文件/目录")
    ap.add_argument("--kind", default="chess", help="view: chess|spell")
    args = ap.parse_args()

    if args.cmd == "check":
        return cmd_check(args)
    if args.cmd == "update-check":
        info = check_update(args.season, args.version)
        return info
    if args.cmd in ("fetch", "all"):
        cmd_fetch(args)
    if args.cmd in ("csv", "all"):
        cmd_csv(args)
    if args.cmd in ("json", "all"):
        cmd_json(args)
    if args.cmd == "push":
        push_to_current(args.season, "merge", args.json_path)
    if args.cmd == "replace":
        push_to_current(args.season, "replace", args.json_path)
    if args.cmd == "export":
        if not args.out:
            raise SystemExit("export 要给 --out 目录")
        ws = export_all(args.season, args.out)
        log("[导出] %s: %s" % (args.out, ", ".join(ws)))
    if args.cmd == "import":
        if not args.file:
            raise SystemExit("import 要给 --file 文件/目录")
        msg, _sd = import_any(args.file, args.season)
        log("[导入] " + msg)
    if args.cmd == "view":
        info = summary(args.season)
        log("[赛季 S%s] 目录 %s" % (info["season"], info["dir"]))
        for k, v in info["tables"].items():
            log("  · %-22s %d" % (k, v))
        for label, path, ex, size in info["files"]:
            log("  %s %-28s %s" % ("✓" if ex else "·", label, ("%d 字节" % size) if ex else "(没有)"))
        rows = view_rows(args.season, args.kind)
        log("[%s] 前 10 行:" % args.kind)
        for r in rows[:10]:
            log("  %s %s %s" % (r["id"], r["name"], r["season"]))
    log("\n完成。下一步: 重启助手 → 重新连接(会自动重推 棋子名/锦囊名/技能 三张映射)")
    return None


if __name__ == "__main__":
    main()

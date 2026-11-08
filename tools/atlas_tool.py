# -*- coding: utf-8 -*-
"""动态皮图集(atlas + png)小工具 —— 看部件 / 抠部件 / 把部件涂透明。

为什么需要它:
  游戏里 SgsSkeletonEffect 的图层在运行时改不动(渲染读的是另一套烤好的数据),
  但图集 png 是我们自己 data/dyn_skins/<no>/ 下的文件, 由本机 /dyn/ 提供。
  直接改像素 = 加载时那一块就是空的, 效果等价于"隐藏图层", 而且持久化。

用法(在 自走棋助手/ 目录下):
  python tools/atlas_tool.py list    <no> [frame]          列出所有部件 + 图集中的矩形
  python tools/atlas_tool.py sheet   <no> [frame]          生成一张缩略图索引(带名字), 用来"找"部件
  python tools/atlas_tool.py crop    <no> <部件名> [更多...]  把部件单独抠出来存 png
  python tools/atlas_tool.py blank   <no> <部件名> [更多...]  把图集里这几块涂成全透明(自动备份)
  python tools/atlas_tool.py status  <no> [frame]          当前已经涂掉了哪些
  python tools/atlas_tool.py restore <no> [frame]          从 .bak 还原图集

部件名支持前缀通配: 'hua*' = hua1..hua21, 'piaodai*' = 全部飘带。
blank 是累加的(每次都在当前 png 上再涂), restore 一次性全部还原。

frame 默认 daiji。
产物都在 data/dyn_skins/<no>/_preview/ 下。
"""

import os
import sys
import json
import shutil

from PIL import Image, ImageDraw, ImageFont

# 兜底不静默: 吞掉的异常要记一笔(见 core/silence.py)。
# 这里 tools/ 不在 core/ 里, 得把项目根加进 sys.path 才能 import core。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import silence as SILENCE

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data", "dyn_skins")


# ---------------------------------------------------------------- 解析 atlas

def parse_atlas(path):
    """解析 Spine atlas(新格式 xy/size, 兼容老格式 bounds), 返回 {name: rect}。

    rect = (x, y, w, h, rotate, orig_w, orig_h, off_x, off_y)
    """
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        lines = [ln.rstrip("\n") for ln in f]

    regions = {}
    cur = None
    i = 0
    # 跳过头部(size/format/filter/repeat/scale), 从第一个 region 名开始
    while i < len(lines):
        ln = lines[i]
        s = ln.strip()
        if not s:
            i += 1
            continue
        low = s.lower()
        if low.startswith(("size:", "format:", "filter:", "repeat:", "scale:")):
            i += 1
            continue
        if s.endswith(".png") or s.endswith(".jpg") or s.endswith(".webp"):
            i += 1
            continue
        break

    while i < len(lines):
        ln = lines[i]
        if ln and not ln[0].isspace() and ln.strip():
            cur = ln.strip()
            regions[cur] = {"rotate": False, "xy": (0, 0), "size": (0, 0),
                            "orig": (0, 0), "offset": (0, 0)}
            i += 1
            continue
        s = ln.strip()
        if not s or cur is None:
            i += 1
            continue
        low = s.lower()
        try:
            if low.startswith("rotate:"):
                v = s.split(":", 1)[1].strip()
                regions[cur]["rotate"] = (v == "true" or v.startswith("90"))
            elif low.startswith("xy:"):
                a, b = s.split(":", 1)[1].split(",")
                regions[cur]["xy"] = (int(a), int(b))
            elif low.startswith("size:"):
                a, b = s.split(":", 1)[1].split(",")
                regions[cur]["size"] = (int(a), int(b))
            elif low.startswith("orig:"):
                a, b = s.split(":", 1)[1].split(",")
                regions[cur]["orig"] = (int(a), int(b))
            elif low.startswith("offset:"):
                a, b = s.split(":", 1)[1].split(",")
                regions[cur]["offset"] = (int(a), int(b))
            elif low.startswith("bounds:"):
                p = s.split(":", 1)[1].split(",")
                x, y, w, h = (int(v) for v in p[:4])
                regions[cur]["xy"] = (x, y)
                regions[cur]["size"] = (w, h)
            elif low.startswith("offsets:"):
                p = s.split(":", 1)[1].split(",")
                l, t, r, b = (int(v) for v in p[:4])
                regions[cur]["orig"] = (r - l, b - t)
                regions[cur]["offset"] = (l, t)
        except Exception:
            # atlas 里某一行格式不对 → 跳过这一行。得记账, 否则"少了一块图集"
            # 只会表现为某个素材不显示, 谁也看不出是解析被吞了。
            SILENCE.note("atlas_tool.parse(%s)" % cur, "这一行解析不了, 跳过该区域")
        i += 1

    out = {}
    for name, d in regions.items():
        if not d["size"][0] or not d["size"][1]:
            continue
        ow, oh = d["orig"] if d["orig"][0] else d["size"]
        out[name] = (d["xy"][0], d["xy"][1], d["size"][0], d["size"][1],
                     d["rotate"], ow, oh, d["offset"][0], d["offset"][1])
    return out


def paths(no, frame="daiji"):
    d = os.path.join(DATA, str(no))
    return os.path.join(d, frame + ".atlas"), os.path.join(d, frame + ".png")


def preview_dir(no):
    p = os.path.join(DATA, str(no), "_preview")
    if not os.path.isdir(p):
        os.makedirs(p)
    return p


def load_font(size=13):
    for fp in ("C:/Windows/Fonts/consola.ttf", "C:/Windows/Fonts/consolas.ttf",
               "C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf"):
        if os.path.isfile(fp):
            try:
                return ImageFont.truetype(fp, size)
            except Exception:
                # 这个字体读不出来就试下一个 —— 属于正常降级路径, 但最后一个也失败
                # 时会退化成默认字体(导出的图文字变丑), 得能查到是哪一步。
                SILENCE.note("atlas_tool.load_font", "字体读不出来: %s" % fp)
    try:
        return ImageFont.load_default(size=size)
    except Exception:
        return ImageFont.load_default()


# ---------------------------------------------------------------- 已涂掉记录

def _log_path(no):
    return os.path.join(preview_dir(no), "blanked.json")


def _load_log(no):
    try:
        with open(_log_path(no), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save_log(no, items):
    try:
        with open(_log_path(no), "w", encoding="utf-8") as f:
            json.dump(sorted(set(items)), f, ensure_ascii=False, indent=1)
    except Exception:
        SILENCE.note("atlas_tool._save_log(%s)" % no, "下载记录写不进去, 下次会重复下载")


def expand(names, rs):
    """支持前缀通配: 'hua*' 展开成 hua1..hua21; 名字精确匹配的照原样。"""
    out = []
    for n in names:
        if "*" in n:
            pre = n.replace("*", "")
            out += [k for k in rs.keys() if k.startswith(pre)]
        else:
            out.append(n)
    seen, res = set(), []
    for x in out:
        if x not in seen:
            seen.add(x)
            res.append(x)
    return res


def cmd_status(no, frame):
    """当前这套图集已经把哪些部件涂掉了。"""
    ap, _ = paths(no, frame)
    if os.path.isfile(ap):
        rs = parse_atlas(ap)
    else:
        rs = {}
    done = _load_log(no)
    if not done:
        print("当前没有涂掉任何部件(图集是原样)")
        return 0
    print("已涂掉 %d 个: %s" % (len(done), ", ".join(done)))
    miss = [x for x in done if x not in rs]
    if miss:
        print("  (其中 atlas 里已找不到的: %s)" % ", ".join(miss))
    return 0


# ---------------------------------------------------------------- 子命令

def cmd_list(no, frame):
    ap, pp = paths(no, frame)
    if not os.path.isfile(ap):
        print("没有 atlas: %s" % ap)
        return 1
    if not os.path.isfile(pp):
        print("没有 png: %s" % pp)
        return 1
    rs = parse_atlas(ap)
    names = sorted(rs.keys())
    print("共 %d 个部件 (图集 %s)" % (len(names), os.path.basename(pp)))
    for i, n in enumerate(names):
        x, y, w, h, rot, ow, oh, ox, oy = rs[n]
        print("%3d  %-24s @(%4d,%4d) %4dx%-4d%s orig %dx%d" %
              (i, n, x, y, w, h, " R" if rot else "  ", ow, oh))
    return 0


def cmd_sheet(no, frame, cols=8, cell=132):
    ap, pp = paths(no, frame)
    if not os.path.isfile(ap) or not os.path.isfile(pp):
        print("缺 atlas/png")
        return 1
    rs = parse_atlas(ap)
    names = sorted(rs.keys())
    img = Image.open(pp).convert("RGBA")
    font = load_font(12)

    rows = (len(names) + cols - 1) // cols
    W = cols * cell
    H = rows * (cell + 18)
    canvas = Image.new("RGB", (W, H), (24, 24, 28))
    dr = ImageDraw.Draw(canvas)

    for i, n in enumerate(names):
        x, y, w, h, rot, ow, oh, ox, oy = rs[n]
        tile = img.crop((x, y, x + w, y + h))
        if rot:
            tile = tile.transpose(Image.Transpose.ROTATE_90)
        tile = tile.convert("RGBA")
        sc = min((cell - 8) / max(tile.width, 1), (cell - 8 - 16) / max(tile.height, 1), 1.0)
        if sc < 1.0:
            tile = tile.resize((max(1, int(tile.width * sc)), max(1, int(tile.height * sc))),
                               Image.LANCZOS)
        cx = (i % cols) * cell
        cy = (i // cols) * (cell + 18)
        # 棋盘底, 方便看透明区
        for a in range(0, cell, 12):
            for b in range(0, cell, 12):
                if ((a // 12) + (b // 12)) % 2 == 0:
                    dr.rectangle([cx + a, cy + b, cx + a + 11, cy + b + 11], fill=(58, 58, 66))
        canvas.paste(tile, (cx + (cell - tile.width) // 2, cy + 4), tile)
        dr.text((cx + 3, cy + cell - 12), n[:22], fill=(210, 210, 215), font=font)

    out = os.path.join(preview_dir(no), frame + "_sheet.png")
    canvas.save(out)
    print("索引图: %s  (%d 个部件)" % (out, len(names)))
    return 0


def cmd_crop(no, frame, names):
    ap, pp = paths(no, frame)
    if not os.path.isfile(ap) or not os.path.isfile(pp):
        print("缺 atlas/png")
        return 1
    rs = parse_atlas(ap)
    img = Image.open(pp).convert("RGBA")
    pd = preview_dir(no)
    ok = 0
    for n in expand(names, rs):
        if n not in rs:
            print("  没有这个部件: %s" % n)
            continue
        x, y, w, h, rot, ow, oh, ox, oy = rs[n]
        tile = img.crop((x, y, x + w, y + h))
        if rot:
            tile = tile.transpose(Image.Transpose.ROTATE_90)
        # 还原到 orig 画布(带 offset), 这样位置关系和图里一致
        if ow and oh:
            canvas = Image.new("RGBA", (ow, oh), (0, 0, 0, 0))
            canvas.paste(tile, (ox, oy))
            tile = canvas
        safe = n.replace("/", "_").replace("\\", "_")
        out = os.path.join(pd, safe + ".png")
        tile.save(out)
        print("  抠出: %s  %dx%d -> %s" % (n, tile.width, tile.height, out))
        ok += 1
    print("完成 %d 个" % ok)
    return 0


def cmd_blank(no, frame, names):
    ap, pp = paths(no, frame)
    if not os.path.isfile(ap) or not os.path.isfile(pp):
        print("缺 atlas/png")
        return 1
    rs = parse_atlas(ap)
    bak = pp + ".bak"
    if not os.path.isfile(bak):
        shutil.copy2(pp, bak)
        print("已备份: %s" % os.path.basename(bak))

    img = Image.open(pp).convert("RGBA")
    dr = ImageDraw.Draw(img)
    done, hit = 0, []
    for n in expand(names, rs):
        if n not in rs:
            print("  没有这个部件: %s" % n)
            continue
        x, y, w, h = rs[n][:4]
        dr.rectangle([x, y, x + w - 1, y + h - 1], fill=(0, 0, 0, 0))
        print("  已涂透明: %-22s @(%d,%d) %dx%d" % (n, x, y, w, h))
        hit.append(n)
        done += 1
    if done:
        img.save(pp)
        _save_log(no, _load_log(no) + hit)
        print("图集已改写(累计涂掉 %d 个): %s" % (len(_load_log(no)), pp))
        print("还原: python tools/atlas_tool.py restore %s" % no)
    return 0


def cmd_restore(no, frame):
    ap, pp = paths(no, frame)
    bak = pp + ".bak"
    if not os.path.isfile(bak):
        print("没有备份: %s" % bak)
        return 1
    shutil.copy2(bak, pp)
    try:
        os.remove(_log_path(no))
    except Exception:
        SILENCE.note("atlas_tool.restore", "还原后没能删掉下载记录: %s" % no)
    print("已还原: %s" % pp)
    return 0


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1
    cmd = argv[0].lower()
    no = argv[1]
    frame = "daiji"
    rest = argv[2:]
    if rest and not rest[0].startswith("-") and cmd in ("list", "sheet") and len(rest) >= 1:
        if os.path.isfile(os.path.join(DATA, str(no), rest[0] + ".atlas")):
            frame = rest[0]
            rest = rest[1:]
    if cmd == "list":
        return cmd_list(no, frame)
    if cmd == "sheet":
        return cmd_sheet(no, frame)
    if cmd == "crop":
        if not rest:
            print("要给部件名")
            return 1
        return cmd_crop(no, frame, rest)
    if cmd == "blank":
        if not rest:
            print("要给部件名")
            return 1
        return cmd_blank(no, frame, rest)
    if cmd == "restore":
        return cmd_restore(no, frame)
    if cmd == "status":
        return cmd_status(no, frame)
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

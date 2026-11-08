# -*- coding: utf-8 -*-
"""v4.87★ 图集(atlas)取块 —— 用户原话:

    "自走棋图集, 好像是一个剪辑图, 里面有很多元素, 但是放的时候放一部分,
     取一部分就是一个图; 里面有很多"

官方有相当一部分界面资源是【图集(atlas)】: 永远成对出现 ——

    newStyleBaseUI.atlas        ← 纯文本清单
    newStyleBaseUI.png          ← 一整张"剪贴画"

  · png 那张是【一整张】: 里面密密麻麻摆着几十上百个小元素(底框 / 图标 / 进度条 / 角标…),
    游戏从不会单独请求其中某个元素;
  · atlas 那份是【清单】: 每个元素叫什么名字、在这张大图里的哪块矩形(x,y,w,h)、
    原始多大、有没有被转过 90° 存、透明边被裁掉了多少。

游戏画界面时就是按 atlas 里的矩形去【取一部分】来画的 —— 所以:

  ① 想拿官方图集里的某一个元素当素材 → 按矩形【裁出来】(crop), 存成一张独立 png;
  ② 想只换掉图集里的某一个元素       → 把你的图【贴回】那块矩形(paste), 导出一张新的
    整图 png, 再给整图建一条规则(游戏加载的就是整图, 换它才生效)。

以前这个项目只认"整张 png"(换图集 = 整张一起换), 完全没有"取一块"的能力 —— 这份模块补上:
解析清单 / 算矩形 / 裁块 / 贴块 / 下载 / 从诊断包里发现图集。
"""

import json
import os
import re

from core.config import DATA_DIR

try:
    from PIL import Image
except ImportError:                      # 没装 Pillow 也不能拖垮整个页面: 用到时再报错
    Image = None


# ---------------------------------------------------------------- 解析清单

def _pair(v):
    """"100, 40" → (100, 40); 解析不出来就 None(宁可返回空, 也不要把脏数据当坐标用)。"""
    nums = re.findall(r"-?\d+", str(v or ""))
    if len(nums) < 2:
        return None
    return (int(nums[0]), int(nums[1]))


def parse_atlas(text):
    """解析 .atlas 文本 → {"pages": [...], "frames": [...]}。

    格式是 libgdx / Spine 那一套(纯文本, 缩进表示属性):

        newStyleBaseUI.png          ← 页头(文件名, 顶格)
        size: 1024,1024             ← 页属性(顶格)
        format: RGBA8888
        filter: Linear,Linear
        repeat: none
        generalBaseUIChessGradeBg   ← 元素名(顶格)
          rotate: false             ← 元素属性(缩进)
          xy: 1, 1
          size: 100, 40
          orig: 100, 40
          offset: 0, 0
          index: -1

    判定规则: 顶格行 = 页头 / 元素名; 缩进(或缩进后带冒号)行 = 属性。
    第一个顶格行是页头, 之后每个顶格行都是一个元素名 —— 所以"页头后面紧跟的那几行属性"
    归页, 其余归元素。
    """
    pages = []
    cur_page = None
    cur_frame = None
    for raw in str(text or "").splitlines():
        if not raw.strip():
            continue
        if raw[:1] in (" ", "\t"):
            if ":" not in raw:
                continue
            k, v = raw.split(":", 1)
            k, v = k.strip(), v.strip()
            if cur_frame is not None:
                cur_frame[k] = v
            elif cur_page is not None:
                cur_page.setdefault("meta", {})[k] = v
            continue
        name = raw.strip()
        # v4.87★ 页属性(size:/format:/filter:/repeat:)也是【顶格】的, 只有元素的属性才缩进
        #   —— 第一版只按"缩不缩进"分, 于是这四行被当成了四个"元素", 列表里凭空多出
        #      "size: 1024,1024" 这种假元素(实测出来才看见)。顶格且带 "键: 值" 的一律归页属性。
        m = re.match(r"^([A-Za-z_][\w\-]*)\s*:\s*(.*)$", name)
        if m:
            if cur_page is not None:
                cur_page.setdefault("meta", {})[m.group(1)] = m.group(2)
            continue
        if cur_page is None:
            cur_page = {"file": name, "meta": {}, "frames": []}
            pages.append(cur_page)
            cur_frame = None
            continue
        # 容错: 有些清单一个文件里写了多页(多张 png)。顶格行若"看起来是图片文件名"且
        #   当前页还没攒到元素, 就当成新的一页 —— 免得把第二张 png 的名字当成元素名。
        if (not cur_page["frames"]) and name.lower().endswith(
                (".png", ".jpg", ".jpeg", ".webp")):
            cur_page = {"file": name, "meta": {}, "frames": []}
            pages.append(cur_page)
            cur_frame = None
            continue
        cur_frame = {"name": name, "page": cur_page["file"]}
        cur_page["frames"].append(cur_frame)

    frames = []
    for pg in pages:
        for fr in pg["frames"]:
            fr["page"] = pg["file"]
            frames.append(fr)
    return {"pages": pages, "frames": frames}


def rect_of(fr):
    """元素在图集大图里的矩形 (x, y, w, h)。

    rotate: true 表示这块在 png 里是【转过 90° 存】的 ——
      记录里的 size 写的是"摆正后的宽高", 所以在大图里实际占的格子是【宽高互换】。
      (不换的话裁出来就是错位的斜方块, 而且会越界。)
    """
    xy = _pair(fr.get("xy"))
    size = _pair(fr.get("size"))
    if not xy or not size:
        return None
    w, h = size
    rot = str(fr.get("rotate") or "").strip().lower() == "true"
    if rot:
        w, h = h, w
    return (xy[0], xy[1], w, h)


def frame_label(fr):
    """列表里显示的一行: 名字 + 尺寸 + 位置(有没有转、原始多大 —— 一眼能认出来)。"""
    r = rect_of(fr)
    if not r:
        return str(fr.get("name") or "?")
    rot = str(fr.get("rotate") or "").strip().lower() == "true"
    orig = _pair(fr.get("orig"))
    txt = "%s   %dx%d  @%d,%d" % (fr.get("name") or "?", r[2], r[3], r[0], r[1])
    if rot:
        txt += "  [图集里转过90°]"
    if orig and (orig[0] != r[2] or orig[1] != r[3]) and not rot:
        txt += "  (原始 %dx%d, 透明边被裁过)" % (orig[0], orig[1])
    return txt


# ---------------------------------------------------------------- 取块 / 贴块

def _need_pil():
    if Image is None:
        raise RuntimeError("缺少 Pillow(pip install pillow) —— 裁图/贴图要用它读写 png。")


def crop_out(png_path, rect, out_path, rotate_back=False):
    """把图集里 (x,y,w,h) 这一块裁出来存成独立 png, 返回 out_path。

    rotate_back: 这块在清单里标了 rotate: true(图集里是横躺/竖躺存的),
      裁出来后顺手【逆时针转 90°】摆正 —— 也就是你在游戏里看到的方向。
    """
    _need_pil()
    x, y, w, h = [int(v) for v in rect]
    im = Image.open(png_path)
    # 有些清单的矩形会略微越界(取整误差): 夹一下, 不然 PIL 直接抛异常整张都裁不出来
    W, H = im.size
    x2, y2 = min(W, x + w), min(H, y + h)
    x, y = max(0, x), max(0, y)
    piece = im.crop((x, y, x2, y2)).convert("RGBA")
    if rotate_back:
        piece = piece.rotate(90, expand=True)          # 逆时针 90° = 摆正
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    piece.save(out_path)
    return out_path


def paste_out(png_path, rect, src_path, out_path, rotate=False, cover=True):
    """把 src 这张图贴到图集的第 (x,y,w,h) 块上, 另存为一张【新的整图】, 返回 out_path。

    这是"只换图集里一个元素"的正解: 游戏加载的是整张 png, 所以要把整张导出一份新的,
    再给这张整图建规则。

    rotate: 目标块在清单里标了 rotate: true → 图先顺时针转 90° 再贴(跟着图集的存法)。
    cover:  True  = 整块盖掉(连透明区一起, 原元素彻底消失, 最干净);
            False = 只用不透明的部分去盖(原元素会透出来, 一般别用)。
    """
    _need_pil()
    x, y, w, h = [int(v) for v in rect]
    canvas = Image.open(png_path).convert("RGBA")
    src = Image.open(src_path).convert("RGBA")
    if rotate:
        src = src.transpose(Image.ROTATE_270)          # 顺时针 90°, 跟图集里那块的存法一致
    if src.size != (w, h):
        src = src.resize((w, h), Image.LANCZOS)
    if cover:
        # 先按块的形状铺一层全透明, 再贴 —— 这样"盖掉"是连透明区一起盖,
        #   原元素不会从新图的透明缝里透出来。
        layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        layer.paste(src, (x, y))
        canvas = Image.alpha_composite(canvas, layer)
    else:
        canvas.paste(src, (x, y), src)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    canvas.save(out_path)
    return out_path


def image_size(path):
    """(w, h) —— 读不出来就 (0, 0)(界面上显示为 "?")。"""
    try:
        _need_pil()
        return Image.open(path).size
    except Exception:
        return (0, 0)


# ---------------------------------------------------------------- 下载 / 发现

def download(url, out_path, timeout=(10, 60)):
    """下载一个文件(图集 png 或 .atlas 清单)到本地, 返回 out_path。

    超时是 (连接 10s, 两次数据之间 60s): 网络"半死"(连上了一直不吐数据)时不会永久挂起。
    """
    import requests
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with requests.get(url, stream=True, timeout=timeout) as r:
        r.raise_for_status()
        with open(out_path, "wb") as f:
            for chunk in r.iter_content(8192):
                if chunk:
                    f.write(chunk)
    return out_path


def _load_json(path, default):
    # v4.109★ 改走 core.utils.load_json_file —— 以前这里有一份自己的实现, 与
    #   dress_table / resource_rules / card_image 等处大同小异但细节不同(默认值处理),
    #   改一处漏五处。签名不动, 调用点零改动。
    from core.utils import load_json_file
    return load_json_file(path, default)


def discover_atlases(data_dir=None, limit=300):
    """从诊断包 / 官方资源表里找出【已经见过的图集】, 返回 [{"atlas","png","note"}]。

    图集在诊断包里是以 `xxx.atlas` 这个【清单文件】的身份出现的(游戏请求的是清单),
    真正的图是同名的 `xxx.png` —— 所以这里把 .atlas 换成 .png 才能下载到图。
    (以前那版代码把 atlas 内部的帧名当文件名拼 URL, 拼出一批 100% 404 的死地址;
     这里只认清单本身 + 它对应的整图。)
    """
    d = data_dir or DATA_DIR
    found = {}

    def _add(s, note):
        t = str(s or "").strip()
        if not t.lower().endswith(".atlas"):
            return
        png = t[: -len(".atlas")] + ".png"
        if png not in found:
            found[png] = {"atlas": t, "png": png, "note": note}

    diag = _load_json(os.path.join(d, "diagnostics.json"), [])
    for item in (diag if isinstance(diag, list) else []):
        full = (item or {}).get("full") or {}
        js = full.get("jsons")
        keys = []
        if isinstance(js, dict):
            keys = list(js)
        elif isinstance(js, list):
            keys = [(x.get("k") or x.get("url") or "")
                    for x in js if isinstance(x, dict)]
        for k in keys:
            _add(k, "诊断包")
        # 资源 URL 里也可能直接出现 .atlas(脚本 hook 到过的)
        for u in (full.get("resUrls") or []):
            _add(u, "诊断包")

    off = _load_json(os.path.join(d, "official_resources.json"), [])
    for it in (off if isinstance(off, list) else []):
        _add((it or {}).get("url") or (it or {}).get("pattern") or "", "官方资源表")

    out = sorted(found.values(), key=lambda x: x["png"])
    return out[:int(limit or 300)]


def side_files(png_path):
    """本地 png 旁边有没有同名的 .atlas 清单 —— 有就能直接列出里面的元素。"""
    base = str(png_path or "")
    for cand in (base[: -len(".png")] + ".atlas" if base.lower().endswith(".png") else "",
                 base + ".atlas"):
        if cand and os.path.isfile(cand):
            return cand
    return ""

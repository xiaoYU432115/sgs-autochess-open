# -*- coding: utf-8 -*-
"""公共卡池探测 —— 到底能不能从游戏页读到"某个棋子还剩几张"?

为什么要有这个模块
==================
用户原话: "这个卡池剩余张数, 不对, 因为是公共卡池, 并且我遣散后回到公共卡池, 别的玩家也能购买,
也会减少里面的数量"。

助手端 core/refresh_advisor.py 算概率时用的 pool_taken 只统计【自己买走的】
(core/protocol.py 只在 CRespShopBuyChess / CRespShopRecycleChess 两个【响应包】上写入,
响应包只回给本人), 所以:
  · 别人买走的张数看不到 → 估算的"池里还剩 N 张"偏乐观;
  · 由此产生的"别刷了, 概率太低"这类结论, 实际是【不可靠】的 —— 它只反映你自己买了多少。

要修这个偏差, 前提是先回答一个问题: 游戏客户端到底有没有"公共池剩余张数"这份数据?
  · 有 → 直接读, 问题解决;
  · 没有 → 就只能给"下界/估算", 并且必须【明确说明是估算】, 不能让用户以为它是实锤。

本模块做的是【第一个问题的取证 + 实测】:
  ① 取证(离线, 已做): 反编译源码 1.4MB(参考_sgs_forward_looking-main/source/
     all_tavernchess_classes.txt) + 赛季配置(TavernChessConfig.json) + 抓包结构
     (data/net_structures.json)。结论写在下面 _SOURCE_VERDICT 里, 每条都有出处。
  ② 实测(在线): 真机上再确认一遍 —— 因为反编译版本未必等于线上版本,
     而且"万一"这个词在游戏改动面前永远要留余地。

为什么探测代码写在这里(而不是加进油猴脚本)
==========================================
"能不能读到"这一刻还只是【实验】。实验要迭代很多次, 而每改一次油猴脚本, 用户就得
重新注入 + 刷新游戏页 —— 成本全压在用户身上。助手端本来就有"往游戏页 eval 一段 JS"
的通道(remote_ops.run_console_js, 与 F12 控制台等价), 所以:
  · 探测 → 走 eval 通道, 改一次跑一次, 不用动脚本;
  · 等真探到东西、确定要用 → 再把采集逻辑固化进油猴脚本。
这也是本模块【不 import 油猴脚本任何东西】的原因: 探针 JS 自成一体, 只认 window/Laya
这些浏览器侧的东西, 因此在旧版脚本上也照样能跑。

探针的两条硬规矩
================
  1. 【只读】。哪怕一个赋值都不做 —— 用户是在真机上跑, 探测不能把对局弄崩。
     (只往 window.__sgsPoolProbe 存一份原始报告, 那是我们自己的命名空间。)
  2. 【不许把"没找到"说成"不存在"】。分不清"这份数据不在"和"我没找对地方"的探测
     等于没有探测, 所以报告里必须同时给出: 找过哪些地方、每个地方长什么样、
     以及"据此能下的结论"和"据此不能下的结论"。
"""

# ---------------------------------------------------------------------------
# 离线取证结论(每条都可在源码/配置里复核, 不是推断)
# ---------------------------------------------------------------------------
# 结论一句话: 【客户端没有公共卡池数据】。发牌完全在服务器完成, 客户端只拿到
# "这一次刷新给我的这几张"(shopGoods)。
#
# 依据 1 —— TavernChessGameManager 上根本没有池字段
#   该类共 115 个自有属性, 逐个列出没有一个跟"池/剩余张数"沾边:
#   shopGoods / shopOpen / ShopCurLevel / ShopLevelUpCost / FreeRefreshEndTime /
#   selfInfo / playerList / teamInfo / battleEndMap ... (完整清单见 dev 里的分析记录)
#   全项目搜 AllChessNum / ChessLeftNum / RemainChess / PoolNum / CardPool → 各 0 次。
#   唯一名字带 Pool 的类是 TavernChessValuableBookPoolItem(图鉴条目池, 与对局无关);
#   配置表里的 IsInRandomPool / RandomPool 各只出现 1 次, 是"这张棋子能不能进随机池"的
#   【布尔标记】(TavernChessConfiger 解析 ChessConf 时用), 旁边没有张数。
#
# 依据 2 —— 刷新商店是发协议给服务器, 发牌不在客户端
#   ReqShopRefreshChess() 的实现只有一句实质动作:
#       this.proxy.SendProto(ProtoBufId.CMSG_CREQSHOPREFRESHCHESS, {tableID, isAuto, ...})
#   客户端不参与"抽哪几张"。
#
# 依据 3 —— 服务器回什么就显示什么
#   onRespShopRefreshChess(e) 里:
#       this.CoinNum = t.ProtoData.restCoin,
#       this.FreeRefreshEndTime = t.ProtoData.freeRefreshEndTime,
#       this.updateShopGoods(t.ProtoData.shopGoods, !0),
#   客户端把服务器给的 shopGoods 直接摆上去, 自己没有任何"扣池"的动作。
#
# 依据 4 —— 服务器的包本身也不含剩余张数
#   data/net_structures.json 里一条真实 shopGoods 长这样:
#       {"goodsID":386, "chessID":21007021, "hp":2, "attack":2, "source":1,
#        "totalHp":2, "totalAttack":2, "skills":[{"skillID":870211}],
#        "minionTypes":["CHESSMTYuanShi"]}
#   全部字段都是"这一张牌自己的属性", 没有任何"还剩几张"。
#
# 依据 5 —— 【更正】配置表里【有】"每种共几张", 缺的只是实时消耗量
#   ⚠ 这一条原先写错了, 2026-09-14 更正。原话是"配置表里没有数量列, 连分母都没有" ——
#   那是只翻了 ChessConf 就下的结论。实际上分母表一直在, 它叫 ChessRankConf:
#       费用      1    2    3    4    5    6
#       每种张数  18   15   13    11   9    7     (字段名 TotalCount)
#   按 s10 的 184 种池内棋子算, 池子总量 = 2078 张。
#   所以【分母是有的】, 缺的是"被别人买走多少"这个实时消耗量(依据 1~4 已确认拿不到)。
#   这两件事差别很大, 不能混为一谈:
#     有分母 → 至少能算"在没人买过的池子里"的概率, 它是个【上界】, 够用来做决策;
#     没分母 → 什么都算不了。
#   (客户端源码里 TotalCount 命中 0 处, 说明它给服务器/别的端用; 但数值量级和常识吻合,
#    可作规则常量使用 —— 完整的取舍写在 tools/export_pool_tables.py 里。)
#   ⚠ 用它必须按 SeasonID 过滤: ChessConf 3346 条是【10 个赛季累积】的, 不过滤直接按费用
#     分组会算出 1600 种 / 18362 张 —— 数字大得离谱, 但看着又挺像真的。
#   完整导出: python tools/export_pool_tables.py → 参考_配置表/发牌概率与卡池_s10.md
#
# 依据 6 —— CNtfChessShopMsg 这个"广播"包也帮不上忙
#   onNotifyChessShopMsg 里第一句就是 i == this.SelfUserID 才处理 —— 服务器虽然是广播,
#   但客户端【只看自己那份】; 而且它带的字段是 ShopChessCard(商店棋子卡), 不是池计数。
#
# 那么"没有池"之后, 还能做什么(这才是用户真正关心的)
# ---------------------------------------------------
#   A. 下界: 己方 + 所有看得见的对手阵容里数到的张数 = "已经离开池子的【至少】这么多张"。
#      油猴脚本 v4.67 的「全场已见」口径就是这条(where=all)。
#      ⚠ 它是下界: 对手的备战区(手牌)看不到, 别人买了又遣散回池的也看不到。
#   B. 更值得做的是【换地基】: 助手端现在按"每个棋子 base 张数 - pool_taken"算概率,
#      而 base 张数本来就是拍的。游戏配置表 ChessShopConf 给了【精确的发牌权重】:
#          等级1(3格) 1费100
#          等级2(4格) 1费50 / 2费50
#          等级3(4格) 1费25 / 2费35 / 3费40
#          等级4(5格) 1费10 / 2费20 / 3费35 / 4费35
#          等级5(5格) 1费7  / 2费15 / 3费28 / 4费30 / 5费20
#          等级6(6格) 2费100 / 3费250 / 4费300 / 5费200 / 6费150 (+神将4, 总权重1000)
#      也就是"这个等级刷一张牌, 落在各费用档的概率"是客户端就有的【实锤数据】。
#      用它算"我还差几张能三连"比用 pool_taken 靠谱得多 —— 因为后者缺的是
#      "别的玩家买走了多少"这一大块, 而前者根本不依赖它。
#   C. 真要看池子的消耗, 唯一诚实的说法是"我这局已经见到过 X 张该棋子"(下界),
#      而不是"池里还剩 Y 张"。措辞上必须区分, 否则又是在给用户一个假的确定感。
#
# → 本模块先把 ② 实测跑出来: 万一线上版本和反编译版本不一样, 就以实测为准。
_SOURCE_VERDICT = "客户端无公共卡池数据(发牌在服务器); 可用的替代是『全场已见』下界 + 配置表发牌权重"

# ---------------------------------------------------------------------------
# 探针 JS
# ---------------------------------------------------------------------------
# 一段自包含的表达式(不是语句) —— 脚本端是 eval(c.code), 表达式才有返回值。
# 全程只读: 除了把自己的报告写进 window.__sgsPoolProbe, 不碰游戏里任何对象。
PROBE_JS = r"""
(function () {
    var R = {
        ok: false, probe: 'pool', ver: 1, ts: Date.now(),
        env: {}, steps: [], warn: [],
        nameHits: [],    // manager 上「名字像池」的属性
        mapHits: [],     // manager 上「形状像 {棋子ID: 张数}」的属性 —— 这才是有力的证据
        arrHits: [],     // 数组形式、元素带 chessID+数量字段的
        shop: null,      // 商店发牌数据(看它有没有藏"剩余")
        cfg: null,       // 配置表: 发牌权重 + 随机池名单规模
        proto: [],       // 协议缓存里含池语义的字段名
        limitHit: [],    // 哪些遍历被上限截断了(截断了就必须说, 否则"没找到"不可信)
        verdict: ''
    };
    function step(s) { try { R.steps.push(s); } catch (e) { } }
    function warn(s) { try { R.warn.push(s); } catch (e) { } }

    // ---------- 0. 环境 ----------
    try {
        R.env.hasLaya = !!(window.Laya && window.Laya.stage);
        R.env.scriptVer = (typeof window.SCRIPT_VER !== 'undefined') ? String(window.SCRIPT_VER) : null;
        R.env.sgsBridge = !!window.__sgsBridge;
    } catch (e) { }
    if (!R.env.hasLaya) {
        R.warn.push('拿不到 window.Laya.stage —— 页面没加载完 / 不是游戏页。'
            + '请在【游戏页面】的控制台发起这次探测。');
        R.verdict = 'no-env';
        return JSON.stringify(R);
    }

    // ---------- 1. 找场景 → manager ----------
    // 自己实现搜索(不依赖油猴脚本内部函数): 探针要能在旧版脚本上跑。
    function findScene() {
        var st = window.Laya && window.Laya.stage;
        if (!st) return null;
        var q = [st], head = 0, guard = 0;
        while (head < q.length) {
            if (guard++ > 6000) { R.limitHit.push('场景树遍历到 6000 个节点就停了'); break; }
            var n = q[head++];
            if (!n) continue;
            try {
                if (n.constructor && n.constructor.name === 'TavernChessGameScene') return n;
            } catch (e) { }
            var kids = null;
            try { kids = n._children || n.children; } catch (e) { }
            if (kids && kids.length) {
                for (var i = 0; i < kids.length; i++) {
                    if (kids[i]) q.push(kids[i]);
                }
            }
        }
        return null;
    }

    var scene = findScene();
    R.env.scene = !!scene;
    R.env.sceneCls = scene ? 'TavernChessGameScene' : null;

    var mgr = null, mgrSrc = '';
    try { if (scene && scene.manager) { mgr = scene.manager; mgrSrc = 'scene.manager'; } } catch (e) { }
    if (!mgr) {
        try {
            var G = window.TavernChessGameManager;
            if (G && typeof G.GetInstance === 'function') { mgr = G.GetInstance(); mgrSrc = 'TavernChessGameManager.GetInstance()'; }
            else if (G && typeof G === 'object' && !G.prototype) { mgr = G; mgrSrc = 'window.TavernChessGameManager'; }
        } catch (e) { }
    }
    if (!mgr) {
        R.warn.push('拿不到 manager(不在对局中?)。场景找到了但 manager 为空时, 请先进入一局自走棋再探测。');
        R.verdict = 'no-manager';
        return JSON.stringify(R);
    }
    R.env.mgrSrc = mgrSrc;
    try { R.env.mgrCls = (mgr.constructor && mgr.constructor.name) || ''; } catch (e) { }

    // ---------- 2. manager 属性普查 ----------
    var POOL_RE = /pool|池|remain|left|rest|stock|surplus|balance|inventory|reserve/i;
    // ⚠ 这里【故意不】把 xxxNum / xxxCount / xxxTotal 也算"名字像池":
    //   自走棋里这类名字一大堆(CoinNum / RoundNum / HpNum / TotalNum / ShopCurLevel),
    //   绝大多数是金币/回合/血量, 收进来只会把真正的线索淹掉 —— 用户看到 20 个候选,
    //   反而看不出哪个值得看。而且名字【本来也不构成证据】:
    //   唯一的证据是形状像 {棋子ID: 张数}(见下面的 asIdNumMap)。
    //   所以分两个桶: nameHits = 线索, mapHits = 证据; 报告里也分开说, 不许混。

    function shapeOf(v) {
        if (v === null || v === undefined) return 'null';
        var t = typeof v;
        if (t === 'number' || t === 'string' || t === 'boolean') return t;
        if (t === 'function') return 'fn';
        if (Array.isArray(v)) return 'arr[' + v.length + ']';
        try { return 'obj{' + Object.keys(v).length + '}'; } catch (e) { return 'obj'; }
    }

    // 「像 {棋子ID: 张数} 的映射」判定 —— 这是本探针的核心判据。
    //   为什么用形状而不是名字: 名字可以随便起(ChessPool / CardLeft / xxxDic 都可能),
    //   但"一片数字键 + 一片小整数"这种形状, 在一个"某棋子还剩几张"的表里几乎必然成立,
    //   而在别的地方(金币/回合/血量)几乎不可能成立 —— 那些是标量, 不是映射。
    function asIdNumMap(v) {
        if (!v) return null;
        var t = typeof v;
        if (t !== 'object' || Array.isArray(v)) return null;
        var keys;
        try { keys = Object.keys(v); } catch (e) { return null; }
        if (!keys || keys.length < 5) return null;      // 规模太小, 不像池子
        var scan = Math.min(keys.length, 400), idKey = 0, intVal = 0, smallVal = 0, sample = [];
        for (var i = 0; i < scan; i++) {
            var k = keys[i];
            if (/^\d{3,12}$/.test(k)) idKey++;
            var x = v[k];
            if (typeof x === 'number' && isFinite(x) && Math.floor(x) === x) {
                intVal++;
                if (x >= 0 && x <= 999) smallVal++;
                if (sample.length < 4) sample.push(k + ':' + x);
            }
        }
        return {
            n: keys.length,
            idRatio: idKey / scan,          // 键像 ID 的比例
            intRatio: intVal / scan,        // 值是整数的比例
            smallRatio: smallVal / scan,    // 值是小整数(0~999)的比例 —— 张数不会太大
            sample: sample
        };
    }

    var keys = [];
    try { keys = Object.keys(mgr); } catch (e) { }
    if (keys.length > 400) {
        R.limitHit.push('manager 有 ' + keys.length + ' 个属性, 只看前 400 个');
        keys = keys.slice(0, 400);
    }
    R.env.mgrProps = keys.length;

    for (var ki = 0; ki < keys.length; ki++) {
        var key = keys[ki], val = null;
        try { val = mgr[key]; } catch (e) { continue; }   // 有的属性是 getter, 取值会抛
        if (val === null || val === undefined) continue;

        // (a) 名字像池 → 线索
        if (POOL_RE.test(key)) {
            R.nameHits.push({ k: key, shape: shapeOf(val),
                              v: (typeof val === 'number' || typeof val === 'string') ? String(val).slice(0, 30) : '' });
        }

        // (b) 形状像 {ID: 张数} → 证据
        var m = asIdNumMap(val);
        if (m && m.idRatio >= 0.9 && m.intRatio >= 0.9 && m.smallRatio >= 0.9) {
            R.mapHits.push({ k: key, n: m.n, sample: m.sample });
        }

        // (c) 数组形式: 元素里同时有 chessID 和数量味道的字段
        if (Array.isArray(val) && val.length >= 5 && val.length <= 2000 && val[0] && typeof val[0] === 'object') {
            try {
                var fk = Object.keys(val[0]);
                var hasId = fk.some(function (f) { return /chessid|goodsid|cardid|^id$/i.test(f); });
                var hasNum = fk.some(function (f) { return /num|count|cnt|left|remain|total|stock/i.test(f); });
                if (hasId && hasNum) {
                    R.arrHits.push({ k: key, len: val.length, fields: fk.slice(0, 14),
                                     one: JSON.stringify(val[0]).slice(0, 200) });
                }
            } catch (e) { }
        }
    }
    step('已普查 manager 的 ' + keys.length + ' 个属性');

    // ---------- 3. 商店发牌数据(看它有没有藏"剩余") ----------
    try {
        var sg = null;
        try { sg = mgr.ShopGoods || mgr.shopGoods || null; } catch (e) { }
        if (!sg) {
            try { sg = (mgr.SelfInfo && mgr.SelfInfo.ShopGoods) || null; } catch (e) { }
        }
        if (sg && sg.length) {
            var f0 = {}, sf = null;
            try { sf = Object.keys(sg[0]); } catch (e) { }
            if (sf) {
                for (var fi = 0; fi < sf.length; fi++) {
                    f0[sf[fi]] = (typeof sg[0][sf[fi]]);
                }
            }
            var sus = [];
            for (var si = 0; si < sf.length; si++) {
                if (POOL_RE.test(sf[si]) || /remain|left|stock|surplus/i.test(sf[si])) sus.push(sf[si]);
            }
            R.shop = {
                n: sg.length,
                fields: f0,
                sus: sus,
                one: JSON.stringify(sg[0]).slice(0, 300)
            };
        } else {
            R.shop = { n: 0, note: '当前商店是空的(不在招募阶段 / 没刷新过), 这一次看不到 shopGoods 的字段' };
        }
    } catch (e) {
        R.shop = { err: String((e && e.message) || e) };
    }
    step('已取商店发牌数据');

    // ---------- 4. 配置表: 发牌权重 + 随机池名单 ----------
    // 为什么这里【必须】单独列出来: 池子读不到已经是定局, 但发牌权重是能读到的实锤,
    //   它决定了"这个等级能不能刷出我要的那张" —— 这才是概率估算真正该用的地基。
    try {
        var C = window.TavernChessConfiger, cfgI = null;
        if (C) {
            try { cfgI = (typeof C.GetInstance === 'function') ? C.GetInstance() : C; } catch (e) { }
        }
        if (!cfgI) {
            R.cfg = { err: '拿不到 TavernChessConfiger.GetInstance()(配置表可能叫别的名字)' };
        } else {
            var out = {};
            // (a) 商店等级 → 各费用的发牌权重
            var sd = null;
            try { sd = cfgI.shopDic || cfgI.ShopDic || null; } catch (e) { }
            // Laya.Dictionary 与普通对象两种形态都要兼容:
            //   Dictionary 的键在 .keys(数组) 里, 值走 .get(k) 或 .map[..] / ._map[..];
            //   普通对象就直接 Object.keys。
            // ⚠ 必须 if/else 二选一, 不能两段都跑: Laya.Dictionary 的 .keys 自己也是可枚举属性,
            //   两段都跑会把同一批记录加两遍 —— 表现是"配置里共 31 张棋子"这种看着像真数据、
            //   其实是重复计数的数字(本探针的自检 T7 抓到的就是这个)。
            // ⚠ cap 必须给够: 棋子配置有 300+ 条, 一上来砍到 30 会把"随机池规模"整个报错,
            //   还会让人以为池子就这么大。超了就往 limitHit 记一笔 —— 截断了必须说。
            function dicEntries(d, cap, what) {
                var arr = [];
                if (!d) return arr;
                try {
                    if (Array.isArray(d)) {
                        if (d.length > cap) R.limitHit.push(what + ' 有 ' + d.length + ' 条, 只取前 ' + cap + ' 条');
                        return d.slice(0, cap);
                    }
                    if (Array.isArray(d.keys) && typeof d.get === 'function') {
                        var n = d.keys.length;
                        if (n > cap) R.limitHit.push(what + ' 有 ' + n + ' 条, 只取前 ' + cap + ' 条');
                        for (var i = 0; i < n && i < cap; i++) {
                            var vv = null;
                            try { vv = d.get(d.keys[i]); } catch (e) { }
                            if (vv && typeof vv === 'object') arr.push(vv);
                        }
                        return arr;      // ← 二选一: 这里就【不能】再跑下面的 Object.keys
                    }
                    var ok = Object.keys(d);
                    if (ok.length > cap) R.limitHit.push(what + ' 有 ' + ok.length + ' 条, 只取前 ' + cap + ' 条');
                    for (var j = 0; j < ok.length && j < cap; j++) {
                        var o2 = null;
                        try { o2 = d[ok[j]]; } catch (e) { }
                        if (o2 && typeof o2 === 'object') arr.push(o2);
                    }
                } catch (e) { }
                return arr;
            }
            var shops = dicEntries(sd, 30, '发牌权重表');
            if (shops.length) {
                out.shopRows = [];
                for (var r = 0; r < shops.length && r < 12; r++) {
                    var o = shops[r], row = {};
                    try {
                        var ok2 = Object.keys(o);
                        for (var q = 0; q < ok2.length; q++) {
                            var f = ok2[q];
                            if (/rank|column|chance|cost|percentage/i.test(f)) row[f] = o[f];
                        }
                    } catch (e) { }
                    if (Object.keys(row).length) out.shopRows.push(row);
                }
            } else {
                out.shopNote = '拿不到 shopDic(发牌权重表) —— 它可能就是 TavernChessConfig.json 里的 ChessShopConf';
            }
            // (b) 随机池名单: 有多少张棋子"可能被刷出来", 以及按费用档的分布
            var cd = null;
            try { cd = cfgI.chessDic || cfgI.ChessDic || null; } catch (e) { }
            var recs = dicEntries(cd, 3000, '棋子配置表');
            if (recs.length) {
                var inPool = 0, byRank = {}, total = 0, hasNumField = 0, numFields = {};
                for (var z = 0; z < recs.length; z++) {
                    var rec = recs[z];
                    if (!rec || typeof rec !== 'object') continue;
                    total++;
                    // 顺手记一下: 棋子记录里有没有任何"数量"味道的字段(有 → 分母就有了)
                    try {
                        var rk = Object.keys(rec);
                        for (var r2 = 0; r2 < rk.length; r2++) {
                            // ⚠ 只认【数值型】的: IsInRandomPool 这种名字里带 Pool 的布尔标记
                            //   也会命中这条正则, 但它不是"张数" —— 报出去等于给用户一个假希望
                            //   ("棋子记录里有数量字段!" 结果点开是个 true/false)。
                            //   离线取证里它也确实是布尔标记(全源码只出现 1 次, 见本文件顶部)。
                            if (/num|count|cnt|total|pool/i.test(rk[r2])
                                && typeof rec[rk[r2]] === 'number') {
                                hasNumField++;
                                numFields[rk[r2]] = 1;
                            }
                        }
                    } catch (e) { }
                    var ok3 = false;
                    try {
                        ok3 = (rec.IsInRandomPool === 1 || rec.IsInRandomPool === true
                               || rec.IsInRandomPool === '1');
                    } catch (e) { }
                    if (ok3) {
                        inPool++;
                        var rk2 = String((rec.ChessRank !== undefined ? rec.ChessRank : rec.chessRank));
                        byRank[rk2] = (byRank[rk2] || 0) + 1;
                    }
                }
                out.chessTotal = total;
                out.inRandomPool = inPool;
                out.byRank = byRank;
                out.rankLikeFields = Object.keys(numFields).slice(0, 12);
                out.numFieldHits = hasNumField;
            } else {
                out.chessNote = '拿不到 chessDic(棋子配置字典)';
            }
            // (c) 每种棋子在池里的总张数 —— ChessRankConf.TotalCount
            //   这一段是后来补的(见文件顶部"依据 5"的更正): 分母一直在这儿,
            //   1费18 / 2费15 / 3费13 / 4费11 / 5费9 / 6费7, 只是我先前只翻了 ChessConf 没看见。
            //   在真机上读它有两个用处:
            //     ① 确认线上版本和离线快照一致(反编译的版本未必等于正在跑的版本);
            //     ② 顺手算出这一季的池子总量, 供"在无人买过的池子里"的概率估算当分母。
            var rd = null;
            try { rd = cfgI.chessRankDic || cfgI.ChessRankDic || null; } catch (e) { }
            var ranks = dicEntries(rd, 30, '费用档表');
            if (ranks.length) {
                out.rankRows = [];
                for (var y = 0; y < ranks.length && y < 8; y++) {
                    var rr = ranks[y], row2 = {};
                    try {
                        var okr = Object.keys(rr);
                        for (var q2 = 0; q2 < okr.length; q2++) {
                            var f2 = okr[q2];
                            if (/rank|count|shown|resouce|resource/i.test(f2)) row2[f2] = rr[f2];
                        }
                    } catch (e) { }
                    if (Object.keys(row2).length) out.rankRows.push(row2);
                }
                // ⚠ 这里【故意不】顺手算"池子总量"。它要用"该档种数 × 每种张数"才算得出,
                //   而种数来自上面 (b) —— (b) 是【全表】数字(ChessConf 攒了 10 个赛季)。
                //   拿全表种数去乘本季张数, 会得到 18362 这种荒谬值, 偏偏它看着又很像真的
                //   (我一开始就是这么算错的)。本季的池子总量得先知道"现在是第几季"才能算,
                //   那是 Python 侧 tools/export_pool_tables.py 的活(它按 SeasonID 过滤)。
                //   探针只回答"客户端到底有没有这张表", 不越界给数字。
            } else {
                out.rankNote = '拿不到 chessRankDic(费用档表) —— 它可能就是 TavernChessConfig.json 里的 ChessRankConf';
            }
            R.cfg = out;
        }
    } catch (e) {
        R.cfg = { err: String((e && e.message) || e) };
    }
    step('已取配置表');

    // ---------- 5. 协议缓存里有没有池味道的字段名 ----------
    // 反编译源码里没有池字段, 但【实际跑的版本】发的包可能多带了东西 ——
    //   这一条是专门给"万一线上和反编译不一样"留的后路。
    try {
        var bag = {}, seen = 0, nodes = 0, found = [];
        try { bag.a = window.__sgsPidResp || null; } catch (e) { }
        try { bag.b = window.__sgsPidMap || null; } catch (e) { }
        try { bag.c = window.__sgsChessDbg || null; } catch (e) { }
        var visited = [];
        function walk(o, depth) {
            if (!o || depth > 6) return;
            if (nodes++ > 3000) return;
            for (var vi = 0; vi < visited.length; vi++) { if (visited[vi] === o) return; }
            if (typeof o !== 'object') return;
            visited.push(o);
            var kk;
            try { kk = Object.keys(o); } catch (e) { return; }
            for (var i = 0; i < kk.length; i++) {
                var k = kk[i];
                if (POOL_RE.test(k) && found.length < 40) found.push(k);
                seen++;
                var v = null;
                try { v = o[k]; } catch (e) { continue; }
                if (v && typeof v === 'object') walk(v, depth + 1);
            }
        }
        walk(bag, 0);
        R.proto = found;
        R.env.protoNodes = nodes;
        if (nodes > 3000) R.limitHit.push('协议缓存扫描到 3000 个节点就停了');
    } catch (e) { }
    step('已扫协议缓存');

    R.ok = true;
    R.verdict = R.mapHits.length ? 'found-map' : (R.arrHits.length ? 'found-array' : 'none');
    try { window.__sgsPoolProbe = R; } catch (e) { }
    return JSON.stringify(R);
})()
"""

# 探针结果里各项的中文名(报告用)
_SHAPE_CN = {
    "number": "数值", "string": "文本", "boolean": "开关", "fn": "函数",
    "null": "空", "object": "对象", "arr[0]": "空数组",
}


def parse(raw):
    """把脚本端回传的字符串解析成报告 dict。

    容错优先: 失败时返回 {"_err": 说明, "_raw": 前 300 字符} —— 因为探针最常见的结果
    就是"没回包/回了个报错", 而一个会抛异常的解析函数会让这两种情况都看不出所以然。
    """
    if raw is None:
        return {"_err": "无回包(游戏页未连接 / 超时 / 当前不在游戏页面)"}
    if not isinstance(raw, str):
        raw = str(raw)
    s = raw.strip()
    if not s:
        return {"_err": "空回包", "_raw": raw[:300]}
    if s.startswith("ERR:"):
        return {"_err": s[4:].strip(), "_raw": raw[:300]}
    import json
    try:
        d = json.loads(s)
    except Exception as e:
        return {"_err": "回包不是 JSON(%s)" % e, "_raw": s[:300]}
    if not isinstance(d, dict):
        return {"_err": "回包不是对象", "_raw": s[:300]}
    return d


def format_report(rep):
    """把报告翻译成"人话" —— 返回日志行列表。

    写作原则(整个项目的教训): 结论必须带上【它靠什么得出】, 以及【它不能证明什么】。
    探针类工具最害人的形态是"什么都没找到"和"我没找对地方"长得一模一样。
    """
    L = []
    if not isinstance(rep, dict):
        return ["⚠ 探测结果格式不对: %r" % (rep,)]
    if rep.get("_err"):
        L.append("✗ 探测失败: %s" % rep["_err"])
        raw = rep.get("_raw")
        if raw:
            L.append("  原始回包: %s" % raw.replace("\n", " ")[:200])
        L.append("  → 检查: ① 游戏页是否开着且脚本已注入(角标变绿); ② 是否在游戏画面(不是大厅);")
        L.append("          ③ 助手端『快捷键』页按钮会不会走的是别的端口。")
        return L

    env = rep.get("env") or {}
    # ---- 1. 环境 ----
    if not env.get("hasLaya"):
        L.append("✗ 页面里没有 Laya —— 这不是游戏页(请在游戏画面发起探测)")
    elif not env.get("mgrSrc"):
        L.append("• 场景: %s;  但没拿到 manager(多半不在对局中)" % ("已找到" if env.get("scene") else "未找到"))
    else:
        L.append("• 目标: %s → %s (%d 个属性)"
                 % (env.get("mgrSrc"), env.get("mgrCls") or "?", env.get("mgrProps") or 0))

    # ---- 2. 池证据: 分两桶说, 别让线索冒充证据 ----
    maps = rep.get("mapHits") or []
    arrs = rep.get("arrHits") or []
    names = rep.get("nameHits") or []

    if maps:
        L.append("★ 形状像『{棋子ID: 剩余张数}』的字段 —— 共 %d 个(这才是有力的证据):" % len(maps))
        for it in maps[:8]:
            L.append("    · %s = {%d 项} 取样 %s"
                     % (it.get("k"), it.get("n") or 0, it.get("sample")))
        L.append("  → 请把上面这几行发给开发者: 若有, 卡池就能直接读, 『全场已见』那套下界可以退役。")
    else:
        L.append("• 没有发现形状像『{棋子ID: 剩余张数}』的字段(这是本次探测最关键的一条)")

    if arrs:
        L.append("• 另有 %d 个数组字段同时带『棋子ID + 数量』味道(可能是逐个棋子的统计表):" % len(arrs))
        for it in arrs[:5]:
            L.append("    · %s (%d 项) 字段=%s" % (it.get("k"), it.get("len") or 0, it.get("fields")))

    if names:
        L.append("• 名字像池的属性(仅线索, 里面大多数其实是金币/回合/血量): %s"
                 % ", ".join("%s(%s)" % (x.get("k"), x.get("shape")) for x in names[:10]))

    # ---- 3. 商店发牌数据 ----
    shop = rep.get("shop") or {}
    if shop.get("err"):
        L.append("• 商店数据: 读取失败 %s" % shop["err"])
    elif shop.get("n"):
        L.append("• 商店发牌 %d 张, 字段: %s"
                 % (shop["n"], ", ".join(sorted(shop.get("fields") or {}))))
        if shop.get("sus"):
            L.append("    ★ 其中含池/剩余语义的字段: %s" % ", ".join(shop["sus"]))
        else:
            L.append("    (没有任何『剩余/池』字段 —— 与离线取证一致: 服务器只告诉自己这次发哪几张)")
    elif shop:
        L.append("• 商店数据: %s" % (shop.get("note") or "空"))

    # ---- 4. 配置表 ----
    cfg = rep.get("cfg") or {}
    if cfg.get("err"):
        L.append("• 配置表: %s" % cfg.get("err"))
    else:
        rows = cfg.get("shopRows") or []
        if rows:
            L.append("• 发牌权重表(等级 → 各费用权重), 共 %d 级:" % len(rows))
            for r in rows[:8]:
                try:
                    cs = " ".join("%d费=%s" % (i, r.get("RollingChance%d" % i))
                                  for i in range(1, 7) if r.get("RollingChance%d" % i))
                except Exception:
                    cs = ""
                L.append("    · 等级%s (%s格) %s" % (r.get("ShopRank"), r.get("ShopColumn"), cs))
            L.append("  → 这是【能精确拿到的】数据, 算概率该以它为准(见 core/pool_probe.py 顶部说明)")
        elif cfg.get("shopNote"):
            L.append("• 发牌权重表: %s" % cfg.get("shopNote"))
        if cfg.get("rankRows"):
            # 每种张数 —— 这就是【分母】。原先的报告里说"连分母都没有"是错的,
            # 见本文件顶部「依据 5」的更正。这里让探针在真机上把它读出来。
            rr = ["%s费×%s张" % (r.get("Rank"), r.get("TotalCount"))
                  for r in (cfg.get("rankRows") or [])]
            L.append("• 每种棋子在池里的张数: %s" % "  ".join(rr[:8]))
            L.append("  → 分母有了(1费18/2费15/3费13/4费11/5费9/6费7 左右); "
                     "本季池子总量 = 各档『种数×张数』之和, 见 参考_配置表/发牌概率与卡池_s10.md")
        elif cfg.get("rankNote"):
            L.append("• 每种张数表: %s" % cfg.get("rankNote"))
        if cfg.get("inRandomPool") is not None:
            L.append("• 棋子配置表(全表): 共 %s 条记录, 标了『可进随机池』的 %s 条, 按费用分布 %s"
                     % (cfg.get("chessTotal"), cfg.get("inRandomPool"), cfg.get("byRank")))
            L.append("    ⚠ 这是【全表】数字: ChessConf 攒了历届赛季(每条带 SeasonID), "
                     "拿它当本季棋子数会多算好几倍 —— 算本季要用工具按赛季过滤")
            if cfg.get("numFieldHits"):
                L.append("    ★ 棋子记录里另有数量味道的字段: %s"
                         % ", ".join(cfg.get("rankLikeFields") or []))
        elif cfg.get("chessNote"):
            L.append("• 棋子配置表: %s" % cfg.get("chessNote"))

    # ---- 5. 协议缓存 ----
    proto = rep.get("proto") or []
    if proto:
        L.append("• 协议样本里含池语义的字段名: %s" % ", ".join(sorted(set(proto))[:20]))
        L.append("    (反编译源码里查不到池字段, 这些名字值得逐个确认)")
    else:
        L.append("• 协议样本缓存里也没有池语义字段名")

    # ---- 6. 截断声明(有截断就必须说, 否则"没找到"不可信) ----
    lim = rep.get("limitHit") or []
    if lim:
        L.append("⚠ 本次有遍历被上限截断, 所以『没找到』只代表『在这些上限内没找到』: %s"
                 % "; ".join(lim))

    # ---- 7. 结论 ----
    v = rep.get("verdict")
    if v == "found-map":
        L.append("")
        L.append("★ 结论: 探到了疑似公共卡池(见上面 mapHits)。下一步是把它的字段名发我, 我去核字段语义。")
    elif v == "found-array":
        L.append("")
        L.append("★ 结论: 没探到『{ID:张数}』形状的池, 但有带数量的数组字段 —— 可能是逐个棋子的统计, 需要人工确认。")
    elif v == "none":
        L.append("")
        L.append("■ 结论: 客户端没有公共卡池的剩余张数 —— 与离线取证一致。")
        L.append("   依据(反编译源码 1.4MB + 赛季配置 + 抓包结构, 逐条可复核):")
        L.append("     ① TavernChessGameManager 的 115 个属性里没有池字段;")
        L.append("        全项目搜 AllChessNum/ChessLeftNum/RemainChess/PoolNum/CardPool 各 0 次;")
        L.append("     ② 刷新商店 = SendProto(CMSG_CREQSHOPREFRESHCHESS) 发给服务器, 抽牌不在客户端;")
        L.append("     ③ 收到响应后 updateShopGoods(服务器给的 shopGoods) 直接显示, 客户端不扣池;")
        L.append("     ④ 服务器那包本身也没有『还剩几张』(见 data/net_structures.json 的 shopGoods);")
        L.append("     ⑤ 配置表里【有】『每种共几张』(ChessRankConf.TotalCount: "
                 "1费18/2费15/3费13/4费11/5费9/6费7)")
        L.append("        ※ 修正: 分母是有的, 缺的只是『被别人买走多少』这个实时消耗量;")
        L.append("   → 所以『池里还剩 N 张』这句话在客户端【无法成立】; 能站得住的说法只有:")
        L.append("     · 下界: 『全场已见 X 张』(v4.67 的 where=all 口径, 己方+所有可见对手阵容);")
        L.append("     · 换地基: 配置表能精确给出『这个等级刷出各费用的概率』"
                 "(ChessShopConf 权重 + ChessRankConf 张数),")
        L.append("       它不依赖别人买了多少, 比原来那个 pool_taken 靠谱得多。"
                 "导出见 参考_配置表/。")
    return L


def summarize(rep):
    """给状态栏/单行日志用的一句话。"""
    if not isinstance(rep, dict):
        return "探测结果格式不对"
    if rep.get("_err"):
        return "探测失败: %s" % rep["_err"]
    v = rep.get("verdict")
    if v == "found-map":
        return "探到疑似卡池(%d 个候选字段)" % len(rep.get("mapHits") or [])
    if v == "found-array":
        return "没探到池映射, 但有带数量的数组字段"
    if v == "none":
        return "未探到公共卡池(与离线取证一致: 客户端没有这份数据)"
    if v == "no-env":
        return "不在游戏页(没有 Laya)"
    if v == "no-manager":
        return "不在对局中(拿不到 manager)"
    return "探测完成(结论未判定)"

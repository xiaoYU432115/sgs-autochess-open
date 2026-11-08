// 全能扩展 · 手动规则的纯逻辑层（可在 Node 里直接跑，见 dev/_verify_extpage.js）
//
// 为什么单独一个文件：
//   管理页和 service worker 都要用这套东西，而且它是最容易"写错但不报错"的部分 ——
//   匹配串填了个正则字符、文件名带了路径、目标不是 http 地址，这些错浏览器一个都不会说，
//   现象永远是"配了但不生效"。所以判定/合并/清洗全部收在这里，测试直接调函数。
//
// 口径（和助手「资源替换」页保持一致，用户两边切换不用重新理解）：
//   · 一条规则 = 「URL 里的一截(pattern)」→「换成的地址(target)」，包含匹配；
//   · 目标三种写法：本机图片(走助手 /img/) / 网络地址 / 官方立绘(也是网络地址)；
//   · 手动规则【优先于】助手下发的规则 —— 同一个 pattern 直接顶掉，
//     不同 pattern 但都命中时靠 DNR 优先级(200 > 100)分胜负。
(function (root) {
    'use strict';

    // 手写规则可用的类别。前三类是官方立绘的三个来源(点"官方立绘"页就能挑)，
    // 后面几类给手填的匹配串归类用 —— 只影响筛选显示，不影响匹配。
    //   OL大图: runtime/general/big/static|dynamic/ 那两条家族(如 500103=刘备某套皮肤、
    //   621401=神马超)。编号在主游戏皮肤表里、自走棋配置表没有，枚举不了 ——
    //   只能用户从 DevTools 抓到 URL 粘进来(见 normalizePatternInput)。
    var KINDS = ['棋子立绘', '武将牌面', '主公立绘', 'OL大图', '背景', '音效', '头像', '其他'];

    // 与 rules.js 的 PRIORITY_* 保持一致（那边是 DNR 提交用的真值，这里只是展示/排序）
    var PRIORITY = { server: 100, manual: 200 };

    // ---------------------------------------------------------------- 小工具
    function isHttpUrl(u) {
        return /^https?:\/\//i.test(String(u || ''));
    }

    // v1.2★ 匹配串输入的归一化: 允许直接粘完整官方网址。
    //   https://web.sanguosha.com/x/pc/res/assets/runtime/general/big/static/621401.png?v=7cb…
    //     → web.sanguosha.com/x/pc/res/assets/runtime/general/big/static/621401.png
    //   剥协议 + 剥 ?query / #fragment(?v=7cb… 这类缓存参数不进匹配串, 否则版本一变就失配)。
    //   域名【保留】: pattern 是"URL 里的一截"包含匹配, 带域名照样命中, 用户少动一次手。
    function normalizePatternInput(p) {
        var s = String(p == null ? '' : p).trim();
        var m = /^https?:\/\//i.exec(s);
        if (m) s = s.slice(m[0].length);
        var qi = s.indexOf('?');
        if (qi >= 0) s = s.slice(0, qi);
        var hi = s.indexOf('#');
        if (hi >= 0) s = s.slice(0, hi);
        return s.trim();
    }

    // 是不是"从网址粘来的"形态 —— 这种输入里的 . ? # 都是文件名/缓存参数的一部分,
    //   是【字面量】该有的字符, 不能按"写了正则符号"拒掉。
    function looksLikeUrlPaste(p) {
        var s = String(p || '');
        return /^https?:\/\//i.test(s) || /\?v=/.test(s) ||
               /\.(png|jpe?g|webp|gif|mp3|atlas|json|plist)([?#]|$)/i.test(s);
    }

    // 本机文件名 → 只留 basename，挡掉路径穿越/分隔符/控制字符。
    //   返回 null = 这个名字不能用（调用方把原因说给用户，别默默改写）。
    function localFileName(name) {
        var s = String(name == null ? '' : name).trim().replace(/\\/g, '/');
        if (!s) return null;
        s = s.split('/').pop();                      // 只要最后一段
        if (!s || s === '.' || s === '..') return null;
        if (/[\x00-\x1f]/.test(s)) return null;      // 控制字符
        if (/[<>:"|?*]/.test(s)) return null;        // Windows 文件名非法字符
        return s;
    }

    function imgTarget(base, name) {
        var b = String(base || '').replace(/\/+$/, '');
        var f = localFileName(name);
        if (!b || !f) return '';
        return b + '/img/' + encodeURIComponent(f);
    }

    function baseUrlFromPort(port) {
        var p = parseInt(port, 10);
        if (isNaN(p) || p < 1 || p > 65535) return '';
        return 'http://127.0.0.1:' + p;
    }

    // ---------------------------------------------------------------- 单条规则
    // 一条手写规则的最小整理：pattern/target 必须非空，target 必须 http(s)。
    //   返回 null = 不可用（缺关键信息，调用方负责提示）。
    function normalizeEntry(e) {
        if (!e || typeof e !== 'object') return null;
        var pat = String(e.pattern == null ? '' : e.pattern).trim();
        var tgt = String(e.target == null ? '' : e.target).trim();
        if (!pat || !tgt || !isHttpUrl(tgt)) return null;
        return {
            pattern: pat,
            target: tgt,
            kind: KINDS.indexOf(e.kind) >= 0 ? e.kind : '其他',
            note: String(e.note == null ? '' : e.note).slice(0, 200),
            regex: e.regex === true,               // N3: 逐条可选正则匹配(默认字面量)
            enabled: e.enabled !== false           // 缺省 = 启用
        };
    }

    // 表单草稿 → 能用的规则。这是"用户填错了要说人话"的那一层。
    //   d = {pattern, mode:'local'|'url'|'official', value, base, kind, note}
    //   返回 {ok, entry?, error?}
    function validateDraft(d) {
        d = d || {};
        var raw = String(d.pattern == null ? '' : d.pattern).trim();
        var pat = normalizePatternInput(raw);       // 粘完整网址 → 归一化成匹配串
        var regex = d.regex === true;               // N3: 逐条可选正则模式
        if (!pat) return { ok: false, error: '匹配串不能为空 —— 它是"URL 里的一截"，比如 tavernChess/piece/luxun' };
        if (regex) {
            // 正则模式: 不套字面量的规矩, 但要能编译
            try { new RegExp(pat); }
            catch (e) { return { ok: false, error: '正则无效：' + (e && e.message ? e.message : e) }; }
        } else {
            if (pat.length < 3) return { ok: false, error: '匹配串太短(至少 3 个字符) —— 太短会把不相关的资源也换掉' };
            // 网址形态的输入(粘来的官方 URL)里 . ? 都是真字符, 不按"写了正则符号"拒;
            //   只有手写匹配串才提醒正则符号的事。
            if (!looksLikeUrlPaste(raw) && /[\^$\\.\*\+\?\(\)\[\]\{\}\|]/.test(pat) && pat.indexOf('\\') < 0) {
                // 不是禁止，而是提醒：我们做的是字面量包含匹配，这些符号会被当普通字符
                return { ok: false, error: '匹配串里别写正则符号(^ $ . * 等) —— 这里是【字面量】包含匹配，写 . 就是匹配一个点（要真正则就勾上面的“正则匹配”）' };
            }
        }

        var mode = d.mode === 'local' ? 'local' : (d.mode === 'official' ? 'official' : 'url');
        var val = String(d.value == null ? '' : d.value).trim();
        if (mode === 'local') {
            var f = localFileName(val);
            if (!f) return { ok: false, error: '文件名不能用 —— 只写文件名本身(如 luxun.png)，不要带路径' };
            var b = String(d.base || '').replace(/\/+$/, '');
            if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(b)) {
                return { ok: false, error: '本机服务地址不对 —— 先在「总览」里连上助手(端口 1~65535)' };
            }
            var url = imgTarget(b, f);
            return {
                ok: true,
                entry: normalizeEntry({
                    pattern: pat, target: url, kind: d.kind, note: d.note, regex: regex, enabled: true
                }),
                hint: '记得把 ' + f + ' 放进助手的 我的图/ 目录，否则这张图会 404'
            };
        }
        if (!val) return { ok: false, error: '目标地址不能为空' };
        if (!isHttpUrl(val)) {
            return {
                ok: false,
                error: '目标必须是 http(s) 地址 —— file:// 本地路径浏览器不让跳，请用「本机图片」模式走助手的 /img/'
            };
        }
        return {
            ok: true,
            entry: normalizeEntry({ pattern: pat, target: val, kind: d.kind, note: d.note, regex: regex, enabled: true })
        };
    }

    // ---------------------------------------------------------------- 合并
    // serverRules = /rules 解析出来的 [{pattern,url,kind,name}]
    // manualEntries = chrome.storage 里的手写规则（enabled=false 的只显示、不下发）
    // 返回 [{pattern,url,kind,name,source,priority,enabled}] —— 手动的在前(去重时先者胜)。
    function mergeRules(serverRules, manualEntries) {
        var out = [];
        var man = Array.isArray(manualEntries) ? manualEntries : [];
        var srv = Array.isArray(serverRules) ? serverRules : [];

        for (var i = 0; i < man.length; i++) {
            var e = normalizeEntry(man[i]);
            if (!e) continue;
            out.push({
                pattern: e.pattern, url: e.target, kind: e.kind,
                name: e.note || '(手动)', note: e.note,
                source: 'manual', priority: PRIORITY.manual, enabled: e.enabled,
                regex: e.regex === true
            });
        }
        for (var k = 0; k < srv.length; k++) {
            var r = srv[k] || {};
            var pat = String(r.pattern == null ? '' : r.pattern).trim();
            var url = String(r.url == null ? '' : r.url).trim();
            if (!pat || !url) continue;
            out.push({
                pattern: pat, url: url, kind: String(r.kind || '') || '其他',
                name: String(r.name || ''), note: '',
                source: 'server', priority: PRIORITY.server, enabled: true
            });
        }
        return out;
    }

    // 手写规则里互相撞车的 pattern —— 页面拿去标红，比"配了两条只有一条生效"友好得多
    function findDupPatterns(entries) {
        var seen = {};
        var dup = {};
        var arr = Array.isArray(entries) ? entries : [];
        for (var i = 0; i < arr.length; i++) {
            var p = String((arr[i] && arr[i].pattern) || '').trim();
            if (!p) continue;
            if (seen[p]) dup[p] = true;
            else seen[p] = true;
        }
        return dup;
    }

    // 实际会下发的手动规则条数（enabled=false 与非法条目都不算）
    function activeManual(entries) {
        var n = 0;
        var arr = Array.isArray(entries) ? entries : [];
        for (var i = 0; i < arr.length; i++) {
            if (normalizeEntry(arr[i]) && arr[i].enabled !== false) n++;
        }
        return n;
    }

    // ---------------------------------------------------------------- 筛选/搜索
    function filterRules(list, opt) {
        opt = opt || {};
        var q = String(opt.q == null ? '' : opt.q).trim().toLowerCase();
        var src = opt.source || '';           // '' | 'manual' | 'server'
        var kind = opt.kind || '';
        var arr = Array.isArray(list) ? list : [];
        var out = [];
        for (var i = 0; i < arr.length; i++) {
            var r = arr[i] || {};
            if (src && r.source !== src) continue;
            if (kind && (r.kind || '') !== kind) continue;
            if (q) {
                var hay = ((r.pattern || '') + ' ' + (r.url || '') + ' ' +
                    (r.name || '') + ' ' + (r.note || '')).toLowerCase();
                if (hay.indexOf(q) < 0) continue;
            }
            out.push(r);
        }
        return out;
    }

    // 官方立绘清单(/skins 的 skins 数组)的搜索。limit 防止一次渲染 581 个节点把页面卡死。
    function searchSkins(skins, opt) {
        opt = opt || {};
        var q = String(opt.q == null ? '' : opt.q).trim().toLowerCase();
        var kind = opt.kind || '';
        var limit = parseInt(opt.limit, 10) || 200;
        var arr = Array.isArray(skins) ? skins : [];
        var out = [];
        for (var i = 0; i < arr.length && out.length < limit; i++) {
            var s = arr[i] || {};
            if (kind && s.kind !== kind) continue;
            if (q) {
                var hay = ((s.name || '') + ' ' + (s.skin || '')).toLowerCase();
                if (hay.indexOf(q) < 0) continue;
            }
            out.push(s);
        }
        return out;
    }

    // 挑中的官方立绘 → 一条手写规则的草稿（pattern 留空让用户确认/补全）
    function skinToDraft(skin, opt) {
        opt = opt || {};
        if (!skin || !skin.url) return { ok: false, error: '没有选中有效立绘' };
        var pat = String(opt.pattern == null ? '' : opt.pattern).trim();
        if (!pat) {
            // 立绘资源名本身就是最好认的一截 URL —— 默认拿它当匹配串，够准也够短
            pat = String(skin.skin || '').trim();
        }
        if (!pat) return { ok: false, error: '这条立绘连资源名都没有，填不了匹配串' };
        return {
            ok: true,
            draft: {
                pattern: pat,
                mode: 'official',
                value: skin.url,
                kind: skin.kind || '其他',
                note: (skin.name || '') + ' · ' + (skin.skin || '') + (skin.current ? '（本赛季在用）' : '')
            }
        };
    }

    // B4/N3: 这条 pattern 会不会命中某个 URL。默认字面量包含(和 DNR 的
    //   regexFilter=escapeRegex(pattern) 同义)；opts.regex=true 时按正则匹配。
    //   顺带把"为什么没命中"说清楚（本机地址被 DNR 排除 / 不含该串 / 正则无效）。
    function testPatternHit(pattern, url, opts) {
        var pat = String(pattern == null ? '' : pattern).trim();
        var u = String(url == null ? '' : url).trim();
        var regex = !!(opts && opts.regex);
        if (!pat) return { ok: false, error: '匹配串是空的' };
        if (!u) return { ok: false, error: 'URL 是空的' };
        var host = '';
        try { host = new URL(u).hostname; } catch (e) { host = ''; }
        // 本机地址不参与重定向(和 rules.js 的 EXCLUDED_DOMAINS 一致)
        if (host === '127.0.0.1' || host === 'localhost') {
            return { ok: true, hit: false, note: '这是本机地址(' + host + ')，DNR 排除了它，永远不会命中' };
        }
        if (regex) {
            var re = null;
            try { re = new RegExp(pat); }
            catch (e) { return { ok: false, error: '正则无效：' + (e && e.message ? e.message : e) }; }
            return { ok: true, hit: re.test(u), note: re.test(u) ? '' : '这个 URL 不匹配该正则' };
        }
        var hit = u.indexOf(pat) >= 0;
        return {
            ok: true, hit: hit,
            warn: pat.length < 3 ? '匹配串不足 3 字符，实际不会被采用(太短)' : '',
            note: hit ? '' : '这个 URL 里不含「' + pat + '」这一截'
        };
    }

    // ---------------------------------------------------------------- 导入/导出
    var IMPORT_TYPE = 'sgs-ext-manual';
    var IMPORT_VER = 1;

    function exportJson(entries) {
        var out = [];
        var arr = Array.isArray(entries) ? entries : [];
        for (var i = 0; i < arr.length; i++) {
            var e = normalizeEntry(arr[i]);
            if (e) out.push(e);
        }
        return JSON.stringify({ type: IMPORT_TYPE, version: IMPORT_VER, count: out.length, entries: out },
            null, 2);
    }

    function importJson(text) {
        var obj;
        try {
            obj = JSON.parse(String(text == null ? '' : text));
        } catch (e) {
            return { ok: false, error: '不是合法 JSON：' + e.message };
        }
        var arr = obj && (obj.entries || obj.rules);
        if (!Array.isArray(arr)) {
            return { ok: false, error: '结构不对 —— 需要 {"entries":[...]}（可用「导出」生成一份看格式）' };
        }
        if (obj.type && obj.type !== IMPORT_TYPE) {
            return { ok: false, error: '文件类型不对(' + obj.type + ') —— 这不是本扩展的手动规则文件' };
        }
        var out = [];
        var bad = 0;
        for (var i = 0; i < arr.length; i++) {
            var e = normalizeEntry(arr[i]);
            if (e) out.push(e); else bad++;
        }
        return { ok: true, entries: out, skipped: bad, count: out.length };
    }

    function summaryText(merged, stat) {
        var m = Array.isArray(merged) ? merged : [];
        var man = 0, srv = 0;
        for (var i = 0; i < m.length; i++) {
            if (m[i].source === 'manual' && m[i].enabled !== false) man++;
            else if (m[i].source === 'server') srv++;
        }
        var s = '共 ' + m.length + ' 条（手动 ' + man + ' · 助手 ' + srv + '）';
        if (stat && stat.error) s += ' · 助手连不上：' + stat.error;
        return s;
    }

    var api = {
        KINDS: KINDS,
        PRIORITY: PRIORITY,
        IMPORT_TYPE: IMPORT_TYPE,
        isHttpUrl: isHttpUrl,
        normalizePatternInput: normalizePatternInput,
        looksLikeUrlPaste: looksLikeUrlPaste,
        localFileName: localFileName,
        imgTarget: imgTarget,
        baseUrlFromPort: baseUrlFromPort,
        normalizeEntry: normalizeEntry,
        validateDraft: validateDraft,
        mergeRules: mergeRules,
        findDupPatterns: findDupPatterns,
        activeManual: activeManual,
        testPatternHit: testPatternHit,
        filterRules: filterRules,
        searchSkins: searchSkins,
        skinToDraft: skinToDraft,
        exportJson: exportJson,
        importJson: importJson,
        summaryText: summaryText
    };

    root.SGSManual = api;
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof self !== 'undefined' ? self : globalThis);

// 资源重定向扩展 · 纯逻辑层（可在 Node 里直接跑，见 dev/_verify_ext.py）
//
// 为什么把这段单独放一个文件：
//   service worker 里跑的东西没法在 Node 里直接测，而"规则怎么造"恰恰是最容易错的一步
//   （正则转义、防套娃、条数上限、ID 唯一）。所以造规则这件事只写在【这里】，
//   background.js 只负责"拉 → 调用 → 提交"，测试则直接调用这里的函数。
//
// 权限说明（为什么用 declarativeNetRequest 而不是 webRequest）：
//   Manifest V3 【不再支持】阻塞式 webRequest —— 也就是 GoRes/Redirector 老版本用的那条路。
//   MV3 的官方替代是 declarativeNetRequest（下称 DNR）：把规则【声明】给浏览器，
//   由浏览器自己在网络层执行，扩展不需要参与每一次请求。所以这里做的是同样的事：
//   声明一批 "URL 命中正则 → redirect 到目标" 的规则。
//
//   ⚠ 「重定向到 http://127.0.0.1/…」不会被 mixed-content 拦掉：
//     127.0.0.1 / localhost 在浏览器眼里是 potentially trustworthy origin（等同 https），
//     所以 https 的游戏页可以安全地加载它。这正是本机能当图床的原因。
(function (root) {
    'use strict';

    // 助手桥接端口（config.json → bridge.port）。用户可以改，所以不在代码里拼成
    // 'http://127.0.0.1:数字' 这种字面量 —— 那样"改端口"看起来就做不到。
    var DEFAULT_PORT = 8765;

    // DNR 动态规则上限：Chrome 保证至少 5000 条动态规则，但【正则】规则单独限流
    // （MAX_NUMBER_OF_REGEX_RULES，保证值 1000）。本季规则实测 313 条，留足余量。
    var MAX_RULES = 900;

    var GAME_HOSTS = ['web.sanguosha.com', 'www.sanguosha.com'];

    // 本机地址【不参与】重定向 —— 否则规则目标自己会被规则再改写一次（套娃），
    // 图反而加载不出来。油猴脚本那边有个 isLocalUrl() 是同一个意思。
    var EXCLUDED_DOMAINS = ['127.0.0.1', 'localhost'];

    // 永远不碰顶层文档：本扩展只换资源，不换页面。少一条判定就少一种"点进游戏变白屏"。
    var EXCLUDED_RESOURCE_TYPES = ['main_frame', 'sub_frame'];

    var RULE_ID_BASE = 1000;

    // 两条规则同时命中同一个 URL 时，DNR 取【优先级高的那条】。
    //   助手下发的规则 100；用户在扩展管理页手写的规则 200 ——
    //   "我自己加的应该盖过助手给的" 是最符合直觉的口径（同名 pattern 更是靠
    //   mergeRules 去重就已经赢了，这里的优先级是给"两个不同的匹配串恰好都命中"兜底）。
    var PRIORITY_SERVER = 100;
    var PRIORITY_MANUAL = 200;

    function rulesUrl(port) {
        var p = parseInt(port, 10) || DEFAULT_PORT;
        return 'http://127.0.0.1:' + p + '/rules';
    }

    // 只允许去本机助手取规则。端口可改，但 host 必须是本机 ——
    // 免得"端口"被填成一个外部地址，扩展就变成了任意 URL 抓取器。
    function isLocalPort(port) {
        var p = parseInt(port, 10);
        return !isNaN(p) && p >= 1 && p <= 65535;
    }

    // 正则里的字面量转义。pattern 是"URL 里的一截"，要的是【包含】匹配，
    // 所以转义成字面量最准 —— 我们不需要让用户写正则（写错了就是永远不命中）。
    function escapeRegex(s) {
        return String(s == null ? '' : s).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    }

    function isHttpUrl(u) {
        return /^https?:\/\//i.test(String(u || ''));
    }

    // /rules 的响应 → 规整后的对象。容错：回包不是 JSON / 缺字段都不抛。
    function parseRulesPayload(raw) {
        var obj = raw;
        if (typeof raw === 'string') {
            try {
                obj = JSON.parse(raw);
            } catch (e) {
                return { ok: false, error: '回包不是 JSON', season: '', rev: '', rules: [] };
            }
        }
        if (!obj || typeof obj !== 'object') {
            return { ok: false, error: '回包不是对象', season: '', rev: '', rules: [] };
        }
        var arr = Array.isArray(obj.rules) ? obj.rules : [];
        var out = [];
        for (var i = 0; i < arr.length; i++) {
            var r = arr[i] || {};
            var pat = String(r.pattern == null ? '' : r.pattern).trim();
            var url = String(r.url == null ? '' : r.url).trim();
            if (!pat || !url) continue;
            out.push({
                pattern: pat,
                url: url,
                kind: String(r.kind == null ? '' : r.kind),
                name: String(r.name == null ? '' : r.name)
            });
        }
        return {
            ok: true,
            season: String(obj.season == null ? '' : obj.season),
            rev: String(obj.rev == null ? '' : obj.rev),
            count: out.length,
            rules: out
        };
    }

    // 规则清单 → DNR 规则数组。返回 {rules, skipped, errors}
    //   skipped 分三种，都记下来（不然用户只会看到"条数对不上"）：
    //     no_url      —— 目标不是 http(s)（file:// 之类，浏览器不让跳）
    //     dup_pattern —— 同一截 URL 配了两条（DNR 里后一条永远不会命中）
    //     over_limit  —— 超出本扩展自愿设的上限
    function buildDnrRules(list, opts) {
        opts = opts || {};
        var base = parseInt(opts.idBase, 10) || RULE_ID_BASE;
        var max = parseInt(opts.max, 10) || MAX_RULES;
        var defPri = parseInt(opts.priority, 10) || PRIORITY_SERVER;
        var rules = [];
        var skipped = { no_url: 0, dup_pattern: 0, over_limit: 0, bad_regex: 0 };
        var seen = {};

        var items = Array.isArray(list) ? list : [];
        for (var i = 0; i < items.length; i++) {
            var it = items[i] || {};
            var pat = String(it.pattern == null ? '' : it.pattern).trim();
            var url = String(it.url == null ? '' : it.url).trim();
            if (!pat) continue;
            if (!isHttpUrl(url)) { skipped.no_url++; continue; }
            // 去重保留【先出现的】那条 —— 所以 mergeRules 把用户手动规则排在前面，
            // 同一个匹配串就是"我写的盖过助手给的"。
            if (seen[pat]) { skipped.dup_pattern++; continue; }
            if (rules.length >= max) { skipped.over_limit++; continue; }
            // N3: 逐条可选正则。默认字面量(escapeRegex)；regex 模式直接用 pattern 当正则，
            //     但要先验证能编译，编不过就跳过并计数(别让一条坏正则把整批提交搞挂)。
            var filter;
            if (it.regex) {
                try { new RegExp(pat); filter = pat; }
                catch (e) { skipped.bad_regex++; continue; }
            } else {
                filter = escapeRegex(pat);
            }
            seen[pat] = true;
            var pr = parseInt(it.priority, 10);
            if (isNaN(pr) || pr <= 0) pr = defPri;
            rules.push({
                id: base + rules.length,
                priority: pr,
                action: { type: 'redirect', redirect: { url: url } },
                condition: {
                    regexFilter: filter,
                    excludedRequestDomains: EXCLUDED_DOMAINS.slice(),
                    excludedResourceTypes: EXCLUDED_RESOURCE_TYPES.slice()
                }
            });
        }
        return { rules: rules, skipped: skipped };
    }

    // 旧规则 → 新规则，算出 updateDynamicRules 的两个参数。
    //   一条规则改了目标地址，id 不变、内容变 —— 所以必须"先删后加"，
    //   光 addRules 会因为 id 冲突而整批失败。
    function diffRules(appliedIds, nextRules) {
        var ids = [];
        var old = Array.isArray(appliedIds) ? appliedIds : [];
        for (var i = 0; i < old.length; i++) {
            var n = parseInt(old[i], 10);
            if (!isNaN(n)) ids.push(n);
        }
        var next = Array.isArray(nextRules) ? nextRules : [];
        for (var k = 0; k < next.length; k++) {
            // 保险：万一旧 id 表丢了，把新 id 也一起删（重复 remove 是幂等的）
            if (ids.indexOf(next[k].id) < 0) ids.push(next[k].id);
        }
        return { removeRuleIds: ids, addRules: next };
    }

    function summaryText(payload, built) {
        if (!payload || !payload.ok) return '没拿到规则（' + ((payload && payload.error) || '未知原因') + '）';
        var s = payload.season ? ('赛季 ' + payload.season + ' · ') : '';
        s += built.rules.length + ' 条生效';
        var t = built.skipped;
        var parts = [];
        if (t.no_url) parts.push('目标不是 http 地址 ' + t.no_url + ' 条');
        if (t.dup_pattern) parts.push('重复匹配串 ' + t.dup_pattern + ' 条');
        if (t.over_limit) parts.push('超出上限 ' + t.over_limit + ' 条');
        if (t.bad_regex) parts.push('正则无效 ' + t.bad_regex + ' 条');
        if (parts.length) s += '（' + parts.join('，') + '）';
        return s;
    }

    var api = {
        DEFAULT_PORT: DEFAULT_PORT,
        MAX_RULES: MAX_RULES,
        GAME_HOSTS: GAME_HOSTS,
        EXCLUDED_DOMAINS: EXCLUDED_DOMAINS,
        PRIORITY_SERVER: PRIORITY_SERVER,
        PRIORITY_MANUAL: PRIORITY_MANUAL,
        rulesUrl: rulesUrl,
        isLocalPort: isLocalPort,
        escapeRegex: escapeRegex,
        isHttpUrl: isHttpUrl,
        parseRulesPayload: parseRulesPayload,
        buildDnrRules: buildDnrRules,
        diffRules: diffRules,
        summaryText: summaryText
    };

    root.SGSExtRules = api;
    // Node 侧（dev/_verify_ext.py 用 vm 跑本文件）也能拿到同一份
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof self !== 'undefined' ? self : globalThis);

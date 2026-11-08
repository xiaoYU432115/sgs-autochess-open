// 全能扩展 · 用户脚本管理的纯逻辑层（可在 Node 里直接跑，见 dev/_verify_extscripts.js）
//
// 这里实现的是"油猴那个格子"的地基：MV3 的 chrome.userScripts API（Chrome 120+，
// Tampermonkey 用的就是它）—— 可以把【任意代码】注册进页面、逐个启停、按 @match 注入。
// 相比之下 chrome.scripting.registerContentScripts 只能引用扩展自带文件，装不了用户粘的代码。
//
// 为什么判定全收在这一个文件：@match 写错格式、头注释少个 =、名字取不出来 ——
// 这些错浏览器要么静默拒绝要么抛一个没人看的错，现象是"装了但页面里没有"。
// 所以解析/校验/注册计划全部在这里做成纯函数，测试直接调。
//
// 内置脚本（sgs_bridge 本体）不参与 userScripts 注册：它走 manifest 的 content_scripts
// （任何 Chrome 111+ 都可靠），在本页里【只读展示】。要停用它只能停用整个扩展 ——
// 这一点如实写在界面上，不假装能给个开关。
(function (root) {
    'use strict';

    var BUILTIN_ID = 'sgs-builtin-bridge';
    var DEFAULT_MATCHES = ['*://web.sanguosha.com/*', '*://www.sanguosha.com/*'];
    var ID_PREFIX = 'sgs-user-';
    var MAX_CODE = 200 * 1024 * 1024;        // 200MB：游戏脚本一般 1~几 MB；这个上限只挡明显粘错的超大数据（不再挡正常大脚本）

    // ---------------------------------------------------------------- match 模式
    // Chrome 的 match pattern 语法：<scheme>://<host><path>
    //   scheme: http / https / * （* 只代表 http 或 https，不含 file/ftp）
    //   host:   字面量，或整段 *，或 "*." 前缀；不能在中间写 *
    //   path:   必须以 / 开头；这里允许 * 通配
    // 返回 {ok, matches, error} —— 错了把【为什么】说清楚，不给用户一个空报错。
    function validateMatchPattern(p) {
        var s = String(p == null ? '' : p).trim();
        if (!s) return { ok: false, error: '匹配模式是空的' };
        var m = /^(\*|https?):\/\/([^/]*)(\/.*)$/.exec(s);
        if (!m) {
            return { ok: false, error: '格式要像 *://web.sanguosha.com/*（协议://主机/路径）' };
        }
        var scheme = m[1], host = m[2], path = m[3];
        if (host === '') return { ok: false, error: '主机部分是空的' };
        if (host !== '*' && host.indexOf('*') >= 0) {
            if (host.indexOf('*.') === 0) {
                var rest = host.slice(2);
                if (!rest || rest.indexOf('*') >= 0) {
                    return { ok: false, error: '主机里的 * 只能是 "*." 开头（匹配所有子域）或整段 "*"' };
                }
            } else {
                return { ok: false, error: '主机里的 * 只能是 "*." 开头（匹配所有子域）或整段 "*"' };
            }
        }
        if (path.indexOf('/') !== 0) return { ok: false, error: '路径要以 / 开头' };
        return { ok: true, pattern: scheme + '://' + host + path };
    }

    // 一组 @match → 去重、逐条校验。allowEmpty=true 时返回默认游戏域（带 note 提示）。
    function normalizeMatches(list, allowEmpty) {
        var out = [], seen = {}, errs = [];
        var arr = Array.isArray(list) ? list : [];
        for (var i = 0; i < arr.length; i++) {
            var raw = String(arr[i] == null ? '' : arr[i]).trim();
            if (!raw || seen[raw]) continue;
            var v = validateMatchPattern(raw);
            if (!v.ok) { errs.push(raw + '：' + v.error); continue; }
            seen[v.pattern] = true;
            out.push(v.pattern);
        }
        if (!out.length) {
            if (errs.length) return { ok: false, error: errs.join('；') };
            if (allowEmpty) {
                return { ok: true, matches: DEFAULT_MATCHES.slice(), note: '没写 @match，默认注入游戏域名' };
            }
            return { ok: false, error: '至少要有一条有效的 @match' };
        }
        return { ok: true, matches: out };
    }

    // ---------------------------------------------------------------- @include / @exclude
    // 油猴的 @include/@exclude 是【通配符】(glob)，Chrome 的 matches/excludeMatches 是
    // 【match pattern】(只允许 "*." 前缀或整段 "*")。两边语法不一样，直接把 glob 塞进去
    // 浏览器会静默拒绝 —— 现象是"装了但页面里没有"。
    // 这里做保守转换：能转成合法 match pattern 就转，转不了就丢掉并给 warning（宁可少一条
    // 规则让人看见提示，也不要一条坏规则把整个注册搞挂）。
    function globToMatchPattern(g) {
        var s = String(g == null ? '' : g).trim();
        if (!s) return '';
        if (s === '*' || s === '*://*/*' || s === '<all_urls>') return '*://*/*';
        // 补协议：TM 允许 "example.com/*" 这种没写协议的写法
        if (s.indexOf('://') < 0) s = '*://' + s.replace(/^\/+/, '');
        var m = /^(\*|https?):\/\/([^/]*)(\/.*)?$/.exec(s);
        if (!m) return '';
        var scheme = m[1], host = m[2], path = m[3] || '/*';
        if (!host) return '';
        if (host !== '*' && host.indexOf('*') >= 0 && host.indexOf('*.') !== 0) {
            // 例: http://ba*d.example.com/* —— match pattern 认不了，丢弃
            return '';
        }
        if (path.indexOf('/') !== 0) path = '/' + path;
        return scheme + '://' + host + path;
    }

    // ---------------------------------------------------------------- 头解析
    // 解析 ==UserScript== 头（TM 的格式）。没有头不判死 —— 粘一段普通 JS 也能装，
    // 名字用文件名兜底，匹配用默认游戏域，界面会提示"建议补头注释"。
    //
    // v4.140★ 补齐油猴常用元数据（以前只认 name/version/description/match，
    //   别人写的脚本里 @include/@run-at/@require/@noframes 全被无视 —— 表现就是
    //   "脚本装上了但行为不对"）：
    //   @match @include @exclude   → matches / excludeMatches
    //   @run-at                    → document_start|document_end|document_idle
    //   @priority                  → 排序（界面列表按它排；浏览器不保证注入顺序）
    //   @noframes                  → 只在顶层框架跑
    //   @require                   → 注册前把远端库拉下来拼在正文前面
    //   @resource @grant @namespace @author @icon @updateURL @downloadURL @connect
    function parseHeader(code) {
        var src = String(code == null ? '' : code);
        var head = {
            name: '', version: '', description: '', namespace: '', author: '',
            matches: [], includes: [], excludes: [], requires: [], resources: {},
            grants: [], connects: [],
            runAt: '', priority: null, noframes: false,
            icon: '', updateURL: '', downloadURL: '', homepageURL: '',
            hasHeader: false
        };
        var m = /\/\/\s*==UserScript==([\s\S]*?)\/\/\s*==\/UserScript==/.exec(src);
        if (!m) return head;
        head.hasHeader = true;
        var lines = m[1].split(/\r?\n/);
        for (var i = 0; i < lines.length; i++) {
            // ⚠ 值部分是【可选】的: @noframes 这种开关型元数据后面没有值。
            //   早先写成 \s+(.*?) 强制要空格, 结果 @noframes 整行匹配不上 → 被无视,
            //   脚本照样往所有 iframe 里注入(表现出来就是"@noframes 写了没用")。
            var kv = /^\s*\/\/\s*@([\w-]+)(?::[a-zA-Z-]+)?(?:\s+(.*?))?\s*$/.exec(lines[i]);
            if (!kv) continue;
            var k = kv[1].toLowerCase(), v = kv[2] || '';
            switch (k) {
                case 'name': if (!head.name) head.name = v; break;
                case 'version': if (!head.version) head.version = v; break;
                case 'description': if (!head.description) head.description = v; break;
                case 'namespace': if (!head.namespace) head.namespace = v; break;
                case 'author': if (!head.author) head.author = v; break;
                case 'icon': if (!head.icon) head.icon = v; break;
                case 'updateurl': if (!head.updateURL) head.updateURL = v; break;
                case 'downloadurl': if (!head.downloadURL) head.downloadURL = v; break;
                case 'homepageurl': case 'homepage': if (!head.homepageURL) head.homepageURL = v; break;
                case 'match': head.matches.push(v); break;
                case 'include': head.includes.push(v); break;
                case 'exclude': head.excludes.push(v); break;
                case 'require': head.requires.push(v); break;
                case 'grant': head.grants.push(v); break;
                case 'connect': head.connects.push(v); break;
                case 'run-at': if (!head.runAt) head.runAt = v.toLowerCase(); break;
                case 'priority': if (head.priority === null) head.priority = parseInt(v, 10) || 0; break;
                case 'noframes': head.noframes = true; break;
                case 'resource': {
                    // @resource  name  url
                    var rv = /^(\S+)\s+(\S+)$/.exec(v);
                    if (rv) head.resources[rv[1]] = rv[2];
                    break;
                }
                default: break;
            }
        }
        return head;
    }

    // @run-at 的三种值 → Chrome 的 runAt。写错/没写统一按 document_start
    //   （本扩展的所有脚本都是"越早越好"那一类：要抢在游戏自己的代码前面挂 hook）。
    function normalizeRunAt(v) {
        var s = String(v == null ? '' : v).toLowerCase().replace(/[\s-]/g, '_');
        if (s === 'document_end' || s === 'document-end') return 'document_end';
        if (s === 'document_idle' || s === 'document-idle') return 'document_idle';
        return 'document_start';
    }

    function newId() {
        return ID_PREFIX + Date.now().toString(36) + '-' + Math.floor(Math.random() * 1e6).toString(36);
    }

    // ---------------------------------------------------------------- 条目
    // raw 允许的字段：id/name/version/description/code/matches/excludeMatches/runAt/
    //                 priority/allFrames/requires/resources/enabled/source/addedAt/builtin
    // 返回 {ok, script, error, warnings[]} —— 头没写好走 warnings 不走 error（能装就装）。
    function normalizeScript(raw) {
        if (!raw || typeof raw !== 'object') return { ok: false, error: '不是脚本对象' };
        var code = String(raw.code == null ? '' : raw.code);
        if (!code.trim()) return { ok: false, error: '脚本是空的' };
        if (code.length > MAX_CODE) {
            return { ok: false, error: '脚本过大(' + Math.round(code.length / 1024 / 1024) + 'MB > 200MB) —— 是不是粘错东西了' };
        }
        var head = parseHeader(code);

        // 匹配：显式 matches > 头里的 @match > 头里的 @include（转成 match pattern）
        var rawMatches = (raw.matches && raw.matches.length) ? raw.matches
            : (head.matches.length ? head.matches : []);
        var mv = normalizeMatches(rawMatches, true);
        if (!mv.ok && !head.includes.length) return { ok: false, error: mv.error };
        var matches = mv.ok ? mv.matches : [];
        var warnings = [];
        if (!mv.ok) warnings.push('@match 无效：' + mv.error);

        // @include → match pattern（转不了的丢弃并提示）
        head.includes.forEach(function (g) {
            var p = globToMatchPattern(g);
            if (p && matches.indexOf(p) < 0) matches.push(p);
            else if (!p) warnings.push('@include ' + g + ' 转不成浏览器认的匹配模式，已忽略');
        });
        if (!matches.length && !mv.ok) return { ok: false, error: mv.error };

        // @exclude → excludeMatches
        var excludes = [];
        (raw.excludeMatches && raw.excludeMatches.length ? raw.excludeMatches : head.excludes)
            .forEach(function (g) {
                var p = globToMatchPattern(g);
                if (p) { if (excludes.indexOf(p) < 0) excludes.push(p); }
                else warnings.push('@exclude ' + g + ' 转不成浏览器认的匹配模式，已忽略');
            });

        var name = String(raw.name || head.name || '').trim();
        if (!name) {
            // 兜底取名：第一行注释 → 文件名痕迹 → 时间
            var first = /^\s*(?:\/\/|#)?\s*(.{1,40})/.exec(code);
            name = (first ? first[1].trim() : '') || ('脚本 ' + new Date().toISOString().slice(0, 10));
        }
        if (name.length > 80) name = name.slice(0, 80);

        if (!head.hasHeader) warnings.push('没有 ==UserScript== 头注释（不影响运行，建议补 name/@match）');
        if (mv.note) warnings.push(mv.note);
        if (head.requires.length) warnings.push('带 ' + head.requires.length + ' 个 @require —— 注册时会先把这些库拉下来拼在正文前');

        return {
            ok: true,
            script: {
                id: String(raw.id || newId()),
                name: name,
                version: String(raw.version || head.version || ''),
                description: String(raw.description || head.description || ''),
                namespace: String(raw.namespace || head.namespace || ''),
                author: String(raw.author || head.author || ''),
                matches: matches,
                excludeMatches: excludes,
                runAt: normalizeRunAt(raw.runAt || head.runAt),
                priority: (raw.priority == null ? (head.priority || 0) : (parseInt(raw.priority, 10) || 0)),
                allFrames: raw.allFrames === false ? false : !head.noframes,
                requires: (raw.requires && raw.requires.length) ? raw.requires.slice() : head.requires.slice(),
                resources: (raw.resources && typeof raw.resources === 'object') ? raw.resources : head.resources,
                grants: head.grants.slice(),
                icon: String(head.icon || ''),
                updateURL: String(head.updateURL || ''),
                downloadURL: String(head.downloadURL || ''),
                code: code,
                enabled: raw.enabled !== false,
                builtin: !!raw.builtin,
                addedAt: raw.addedAt || 0
            },
            warnings: warnings
        };
    }

    // 注册前把 @require 的库拼到正文前面（油猴语义：库先于脚本执行）。
    // libs: [{url, code, error}] —— 拉不到的库不能让整条脚本注册失败，
    //   只能在正文里插一句注释说明，让"为什么这个脚本没生效"有处可查。
    function composeCode(script, libs) {
        var s = script || {};
        var parts = [];
        (libs || []).forEach(function (l) {
            if (l && l.code) {
                parts.push('/* @require ' + (l.url || '') + ' */\n' + l.code);
            } else {
                parts.push('/* @require ' + (l && l.url || '?') + ' 拉取失败: '
                    + ((l && l.error) || '未知') + ' —— 依赖它的功能会失效 */');
            }
        });
        parts.push(String(s.code || ''));
        return parts.join('\n');
    }

    // 界面列表排序：priority 大的先（油猴语义），同优先级按加入时间。
    //   ⚠ 浏览器【不保证】多个 userScripts 的注入顺序 —— 这里只影响列表显示与
    //   我们的注册顺序，脚本之间若真有依赖应该用 @require 表达，不要靠顺序。
    function sortScripts(arr) {
        var out = (Array.isArray(arr) ? arr : []).slice();
        out.sort(function (a, b) {
            // 内置(主脚本)永远排最前 —— 它是别的脚本的地基
            var pa = (a && a.builtin) ? 1e9 : (parseInt(a && a.priority, 10) || 0);
            var pb = (b && b.builtin) ? 1e9 : (parseInt(b && b.priority, 10) || 0);
            if (pa !== pb) return pb - pa;
            return (parseInt(a && a.addedAt, 10) || 0) - (parseInt(b && b.addedAt, 10) || 0);
        });
        return out;
    }

    // 种子：第一次用（storage 里没有 scripts）→ 建一条"主脚本"的只读条目占位。
    //   v4.81★ 主脚本【不再内置】：代码由助手 /ext/bridge.main.js 下发（扩展里不再有
    //   page/bridge.main.js），注入走 chrome.userScripts（和自添脚本同一条路）。
    //   所以这里存的仍然只是元信息 —— 代码从来不在这里。
    function seedScripts(existing) {
        if (Array.isArray(existing) && existing.length) return existing;
        return [builtinSeed()];
    }

    function builtinSeed() {
        return {
            id: BUILTIN_ID,
            name: '自走棋助手主脚本',
            version: '',
            description: '收发协议 / 剧本 / 看牌 / 换肤页面 hook。v4.81 起不随扩展内置 —— 由助手下发（改了源脚本在助手「资源替换」页点『更新全能扩展脚本』，扩展 ≤20 秒自动取走），离线用上次取到的那份。不能在页内单独停用 —— 要停请到扩展管理页(Chrome: chrome://extensions / Edge: edge://extensions) 暂停整个扩展。',
            matches: DEFAULT_MATCHES.slice(),
            enabled: true,
            builtin: true,
            addedAt: 0
        };
    }

    // ---------------------------------------------------------------- 注册计划
    // 期望状态 = 所有 enabled 且非 builtin 的脚本；现状 = 浏览器里已注册的 id 集合。
    // 注册内容变了（代码/匹配/开关）就要 unregister+register —— 简单粗暴但可靠：
    //   userScripts.update 的字段覆盖面随版本变过，先删后加是唯一不会"改了一半"的路子。
    function regPlan(scripts, registeredIds) {
        var want = {}, plan = { register: [], unregister: [] };
        var arr = Array.isArray(scripts) ? scripts : [];
        for (var i = 0; i < arr.length; i++) {
            var s = arr[i];
            // 内置脚本双保险：认 builtin 标志，也认约定 id —— 数据坏了也漏不进注册计划
            if (s.builtin || s.id === BUILTIN_ID || s.enabled === false) continue;
            want[s.id] = s;
        }
        var have = {};
        (Array.isArray(registeredIds) ? registeredIds : []).forEach(function (id) { have[id] = true; });
        Object.keys(want).forEach(function (id) {
            if (!have[id]) plan.register.push(id);
        });
        Object.keys(have).forEach(function (id) {
            if (id.indexOf(ID_PREFIX) === 0 && !want[id]) plan.unregister.push(id);
        });
        return plan;
    }

    // chrome.userScripts.register() 的一个条目。world:'MAIN' = 与页面同世界（油猴 @grant none 的等价物）。
    //
    // v4.140★ 以前 runAt/allFrames 是写死的 —— 脚本里写 @run-at document-idle 或
    //   @noframes 也照旧 document_start + 全框架跑，行为跟脚本作者写的不是一回事。
    //   现在按脚本元数据给。⚠ excludeMatches 空数组不要传 —— 有些 Chromium 版本
    //   见到空数组会当成"排除一切"，整条脚本静默不注入（这条踩过）。
    function userScriptEntry(script, codeOverride) {
        var s = script || {};
        var e = {
            id: String(s.id),
            matches: (s.matches && s.matches.length) ? s.matches.slice() : DEFAULT_MATCHES.slice(),
            js: [{ code: String(codeOverride == null ? (s.code || '') : codeOverride) }],
            runAt: normalizeRunAt(s.runAt),
            world: 'MAIN',
            allFrames: s.allFrames === false ? false : true
        };
        if (s.excludeMatches && s.excludeMatches.length) e.excludeMatches = s.excludeMatches.slice();
        return e;
    }

    // ---------------------------------------------------------------- 导入导出
    function exportJson(scripts) {
        var arr = (Array.isArray(scripts) ? scripts : []).filter(function (s) { return !s.builtin; })
            .map(function (s) {
                var o = {
                    name: s.name, version: s.version, description: s.description,
                    matches: s.matches, code: s.code, enabled: s.enabled !== false
                };
                // 元数据一起带走，别导出再导入就把 @require/@run-at/@priority 丢了
                if (s.excludeMatches && s.excludeMatches.length) o.excludeMatches = s.excludeMatches;
                if (s.runAt && s.runAt !== 'document_start') o.runAt = s.runAt;
                if (s.priority) o.priority = s.priority;
                if (s.allFrames === false) o.allFrames = false;
                if (s.requires && s.requires.length) o.requires = s.requires;
                if (s.namespace) o.namespace = s.namespace;
                if (s.author) o.author = s.author;
                return o;
            });
        return JSON.stringify({ type: 'sgs-ext-scripts', version: 2, count: arr.length, scripts: arr }, null, 2);
    }

    // 返回 {ok, entries, skipped}
    function importJson(text) {
        var obj;
        try { obj = JSON.parse(String(text == null ? '' : text)); }
        catch (e) { return { ok: false, error: '不是合法 JSON：' + e.message }; }
        if (!obj || obj.type !== 'sgs-ext-scripts' || !Array.isArray(obj.scripts)) {
            return { ok: false, error: '不是本扩展导出的脚本包（缺 type: sgs-ext-scripts）' };
        }
        var entries = [], skipped = 0;
        for (var i = 0; i < obj.scripts.length; i++) {
            var n = normalizeScript(obj.scripts[i]);
            if (n.ok) entries.push(n.script); else skipped++;
        }
        return { ok: true, entries: entries, skipped: skipped };
    }

    // ---------------------------------------------------------------- 更新判定（B1）
    // 老脚本 + 新抓到的正文 → 要不要更新。保留原 id / @match / 启用态 / 加入时间，
    // 只换代码(以及头里解析出的 name/version 等)。返回 {ok, hasUpdate, oldVersion,
    // newVersion, newScript, error}。newScript 已合并好保留字段，可直接替换进列表。
    function planUpdate(oldScript, newCode) {
        if (!oldScript || typeof oldScript !== 'object') return { ok: false, error: '没有老脚本' };
        var code = String(newCode == null ? '' : newCode);
        if (!code.trim()) return { ok: false, error: '抓到的是空内容' };
        var n = normalizeScript({
            id: oldScript.id,
            code: code,
            matches: (oldScript.matches && oldScript.matches.length) ? oldScript.matches : undefined,
            enabled: oldScript.enabled !== false
        });
        if (!n.ok) return { ok: false, error: n.error };
        var head = parseHeader(code);
        n.script.addedAt = oldScript.addedAt || 0;
        n.script.enabled = oldScript.enabled !== false;
        return {
            ok: true,
            hasUpdate: String(oldScript.code || '') !== code,
            oldVersion: String(oldScript.version || ''),
            newVersion: String(head.version || n.script.version || ''),
            newScript: n.script
        };
    }

    var api = {
        BUILTIN_ID: BUILTIN_ID,
        ID_PREFIX: ID_PREFIX,
        DEFAULT_MATCHES: DEFAULT_MATCHES,
        MAX_CODE: MAX_CODE,
        validateMatchPattern: validateMatchPattern,
        normalizeMatches: normalizeMatches,
        globToMatchPattern: globToMatchPattern,
        normalizeRunAt: normalizeRunAt,
        parseHeader: parseHeader,
        newId: newId,
        normalizeScript: normalizeScript,
        composeCode: composeCode,
        sortScripts: sortScripts,
        seedScripts: seedScripts,
        builtinSeed: builtinSeed,
        regPlan: regPlan,
        userScriptEntry: userScriptEntry,
        exportJson: exportJson,
        importJson: importJson,
        planUpdate: planUpdate
    };

    if (typeof module !== 'undefined' && module.exports) module.exports = api;   // Node 测试
    else root.SGSExtScripts = api;                                               // 扩展里挂 window/self
})(typeof self !== 'undefined' ? self : this);

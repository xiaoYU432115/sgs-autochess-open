// 全能扩展 · service worker（Manifest V3）
//
// 三件事：
//   ① 【网络层重定向】(GoRes 同款)：从助手拉 /rules → lib/rules.js 造 DNR 规则 → 提交。
//      MV3 里阻塞式 webRequest 已经没有了，declarativeNetRequest(DNR) 是官方替代：
//      扩展把"URL 命中 → redirect"声明给浏览器，由浏览器在网络层自己执行。
//   ② 【手动规则】：管理页里手写的规则存 chrome.storage.local，和助手规则合并后一起下发。
//      助手没开时手动规则照样生效（这是"全能"该有的样子 —— 不联网也能换图）。
//   ③ 【页面注入检查】：本扩展还带油猴脚本本体(page/bridge.main.js, world:MAIN)，
//      管理页的「检查游戏页」用 chrome.scripting 在当前标签页里探一下它真的在不在。
//
// ⚠ 绝不碰与判定有关的数据：只对【资源 URL】做重定向，不读不改任何协议包 / 游戏状态。
//   page/bridge.main.js 是唯一会读游戏数据的部分，它就是油猴脚本本体（同一份代码、
//   同一个 SCRIPT_VER），不是这里新写的逻辑。
importScripts('lib/rules.js', 'lib/manual.js', 'lib/scriptreg.js',
              'lib/bridgecode.js', 'lib/codestore.js');

var R = self.SGSExtRules;
var M = self.SGSManual;
var S = self.SGSExtScripts;      // 用户脚本管理（油猴那个格子的地基）
var B = self.SGSBridgeCode;      // v4.81★ 主脚本下发（扩展不再内置脚本）
var CS = self.SGSCodeStore;      // v4.140★ 大对象分片存储（绕开 storage 单项 5MB 上限）
var ALARM = 'sgs-ext-sync';

// ---------------------------------------------------------------- 小工具
// 统一走回调式（不用 Promise 版 API）：两套混用会在"既传回调又拿 promise"时
// 出现回调与 then 各跑一次的双触发，这里刻意只用一种。
function pget(keys) {
    return new Promise(function (resolve, reject) {
        chrome.storage.local.get(keys, function (res) {
            var e = chrome.runtime.lastError;
            if (e) reject(new Error(e.message)); else resolve(res || {});
        });
    });
}
function pset(obj) {
    return new Promise(function (resolve, reject) {
        chrome.storage.local.set(obj, function () {
            var e = chrome.runtime.lastError;
            if (e) reject(new Error(e.message)); else resolve();
        });
    });
}
function pdnr(opts) {
    return new Promise(function (resolve, reject) {
        chrome.declarativeNetRequest.updateDynamicRules(opts, function () {
            var e = chrome.runtime.lastError;
            if (e) reject(new Error(e.message)); else resolve();
        });
    });
}
function setBadge(text, color) {
    try {
        chrome.action.setBadgeText({ text: String(text == null ? '' : text) });
        if (color) chrome.action.setBadgeBackgroundColor({ color: color });
    } catch (e) { /* 徽标只是提示，失败不影响重定向 */ }
}

// v4.161★ 端口自动探测：助手和扩展的端口是【两把独立的钥匙】。
//   用户改了助手端口、却没在扩展里改 → 以前重定向/主脚本全失效(还找不到原因)。
//   这里: 当前端口连不上时, 自动扫描候选端口找真正在跑的那个, 命中后写回 storage
//   (下次直接用, 不会每次都扫)。候选只覆盖"默认档 8765+20n / 附属档 8766+20n",
//   不扫全端口(太慢) —— 多开/改端口基本都落在这两档上。
var _discoveredPort = null;
async function _portAlive(p) {
    try {
        var ac = new AbortController();
        var to = setTimeout(function () { ac.abort(); }, 250);
        var r = await fetch('http://127.0.0.1:' + p + '/ping', { cache: 'no-store', signal: ac.signal });
        clearTimeout(to);
        if (!r.ok) return false;
        var j = await r.json().catch(function () { return null; });
        return !!(j && (j.pid !== undefined || j.connected !== undefined));
    } catch (e) { return false; }
}
async function discoverPort() {
    if (_discoveredPort) return _discoveredPort;
    var cands = [];
    for (var n = 0; n <= 40; n++) { cands.push(8765 + n * 20); cands.push(8766 + n * 20); }
    cands = cands.filter(function (v, i) { return cands.indexOf(v) === i; });   // 去重
    for (var i = 0; i < cands.length; i++) {
        if (await _portAlive(cands[i])) { _discoveredPort = cands[i]; return cands[i]; }
    }
    return 0;
}
// 当前端口还活着就不动; 连不上再探测候选端口并写回 storage。返回是否切了端口。
async function ensurePort(cfg) {
    if (await _portAlive(cfg.port)) return false;
    var found = await discoverPort();
    if (found && found !== cfg.port) {
        await pset({ port: found });
        cfg.port = found;
        return true;
    }
    return false;
}

async function loadCfg() {
    var s = await pget(['port', 'enabled', 'appliedIds', 'appliedFp', 'autoSync', 'badge', 'scriptsEnabled']);
    return {
        port: R.isLocalPort(s.port) ? parseInt(s.port, 10) : R.DEFAULT_PORT,
        enabled: s.enabled !== false,           // 默认开（★ 只管网络重定向规则，不管脚本！）
        appliedIds: Array.isArray(s.appliedIds) ? s.appliedIds : [],
        appliedFp: String(s.appliedFp || ''),   // A1: 上次提交的规则指纹，没变就跳过 updateDynamicRules
        autoSync: s.autoSync !== false,         // 定时同步（关掉后只剩手动「立即同步」）
        badge: s.badge !== false,               // 图标角标数字
        // ★ 脚本注入总开关。跟上面的 enabled【不是一回事】:
        //   enabled=false 只撤 DNR 重定向规则, 页面里跑着的 userScripts 一条都不会撤 ——
        //   以前界面上那个「暂停」按下去, 用户以为全停了, 其实脚本照跑。这个开关才是真停脚本。
        scriptsEnabled: s.scriptsEnabled !== false
    };
}

async function loadManual() {
    var s = await pget(['manual']);
    return Array.isArray(s.manual) ? s.manual : [];
}

// ================================================================ 用户脚本管理
// 油猴那个格子：chrome.userScripts（Chrome 120+，TM 同款 API）。
//   · 注册进页面的代码可以任意（用户粘的/导入的 .user.js），world:'MAIN' = @grant none；
//   · 注册会跨浏览器重启保留，但【扩展重载/更新时会被清空】→ onInstalled 里重注册；
//   · 内容变了必须 unregister+register（update 的字段覆盖面随版本漂过，先删后加不会"改一半"）。
//   · 代码/匹配的指纹存 scriptFp —— 光比 id 查不出"改了代码没换 id"的编辑。
// ★ 指纹要把【所有会影响注册结果】的字段算进去: 只比 code+matches 的话,
//   改了 @run-at / @exclude / @noframes / @require 不会被认为"变了" → 不重注册,
//   页面里跑的还是旧行为。
function scriptHash(s) {
    var h = 5381;
    var src = String(s.code || '') + '\n'
        + (s.matches || []).join('|') + '\n'
        + (s.excludeMatches || []).join('|') + '\n'
        + String(s.runAt || '') + '|' + String(s.allFrames === false ? 0 : 1)
        + '|' + (s.requires || []).join('|');
    for (var i = 0; i < src.length; i++) { h = ((h << 5) + h + src.charCodeAt(i)) | 0; }
    return String(h);
}

// @require: 注册前把远端库拉下来拼在正文前(油猴语义: 库先于脚本执行)。
//   拉不到的库【不让整条脚本注册失败】—— 只在正文里留一句注释, 页面上能查到原因。
var _reqCache = {};
async function fetchRequires(s) {
    var urls = s.requires || [];
    if (!urls.length) return null;
    var out = [];
    for (var i = 0; i < urls.length; i++) {
        var u = urls[i];
        var hit = _reqCache[u];
        if (hit && Date.now() - hit.at < 30 * 60 * 1000) { out.push(hit.lib); continue; }
        var lib = { url: u, code: '', error: '' };
        try {
            var r = await fetch(u, { cache: 'no-store' });
            if (!r.ok) throw new Error('HTTP ' + r.status);
            lib.code = await r.text();
        } catch (e) {
            lib.error = String((e && e.message) || e);
        }
        _reqCache[u] = { at: Date.now(), lib: lib };
        out.push(lib);
    }
    return out;
}

// 扩展管理页的地址随浏览器变: Edge 只认 edge://(在 Edge 里敲 chrome://extensions
//   也能跳 —— 会被重定向, 但照着抄的人会以为"我是不是装错浏览器了")。
//   这套代码 Chrome/Edge 通用, 所以地址现算, 不写死。
function extPageUrl() {
    var ua = (typeof navigator !== 'undefined' && navigator.userAgent) || '';
    return /\sEdg\//.test(ua) ? 'edge://extensions' : 'chrome://extensions';
}

function userScriptsErr(e) {
    var msg = String((e && e.message) || e);
    if (/developer mode|开发者模式/i.test(msg)) {
        return '需要「开发者模式」：' + extPageUrl() + ' 里打开（浏览器对注入任意代码的硬性要求）';
    }
    // "No matching signature" 是【参数形状不对】(= 我们这边写错), 不是浏览器不支持 ——
    //   不点破的话, 用户会一路查到"开发者模式/浏览器版本"上去, 越查越远。
    if (/no matching signature/i.test(msg)) {
        return '注册参数形状不对（userScripts.register 第一个参数必须是【数组】）: ' + msg;
    }
    // 条目里带了浏览器不认的字段(典型: 新版 Chromium 127+ 把 world 换成 worldId) ——
    //   报出来好过一声不吭地不注册。
    if (/unrecognized property/i.test(msg)) {
        return '注册条目里有浏览器不认的字段（多半是 world —— 新版 Chromium 改用 worldId）: ' + msg;
    }
    return 'userScripts 不可用：' + msg;
}

function userScriptsReady() {
    if (!chrome.userScripts || typeof chrome.userScripts.register !== 'function') {
        // Chrome 120+ / Edge 120+ 才有这个 API(都是 Chromium 内核, 版本号对齐)
        return { ok: false, error: '需要 Chromium 内核 120+（Chrome / Edge 120 及以上）并在 '
            + extPageUrl() + ' 打开「开发者模式」(浏览器只在这个前提下才允许扩展注入任意代码)' };
    }
    return { ok: true };
}

async function pgetReg() {
    return new Promise(function (resolve, reject) {
        chrome.userScripts.getScripts(function (list) {
            var e = chrome.runtime.lastError;
            if (e) reject(new Error(e.message)); else resolve(list || []);
        });
    });
}
async function punreg(ids) {
    return new Promise(function (resolve, reject) {
        chrome.userScripts.unregister({ ids: ids }, function () {
            var e = chrome.runtime.lastError;
            if (e) reject(new Error(e.message)); else resolve();
        });
    });
}
async function pregister(entry) {
    // ⚠ 签名是 (array scripts, callback) —— 【必须是数组】。传单个条目对象, 浏览器给的
    //   错误是 "No matching signature": 它不会说"你少了个方括号", 于是排查方向全歪到
    //   "是不是浏览器不支持 / 开发者模式没开"上去(真踩过这一次)。
    //   所以在这一层统一包成数组: 调用方传单个条目还是数组都对, 不会再犯。
    var arr = Array.isArray(entry) ? entry : [entry];
    return new Promise(function (resolve, reject) {
        chrome.userScripts.register(arr, function () {
            var e = chrome.runtime.lastError;
            if (e) reject(new Error(e.message)); else resolve();
        });
    });
}

// 只读一个键(需要时连它的分片一起读)，不 pget(null) —— 后者会把 1.5MB 的主脚本
//   缓存(builtinCode)也一起捞出来，而 syncScripts 每分钟都要跑一次。
//
// ★ 每次都顺带探一下 key#0: 主键不是目录但 key#0 在, 说明上次是分片写的、主键后来
//   被一次小对象写入覆盖了(=孤儿片, 脚本会凭空消失)。只探一个键几乎没成本,
//   探到异常才退化成读全部把片捞回来 —— 异常状态偶发一次全量读可以接受。
async function loadStoreKey(key) {
    var head = await pget([key, key + CS.SEP + '0']);
    var v = head[key];
    var isDir = !!(v && typeof v === 'object' && !Array.isArray(v) && v[CS.MARK]);
    if (isDir) {
        var n = parseInt(v[CS.MARK], 10) || 0;
        var keys = [key];
        for (var i = 0; i < n; i++) keys.push(key + CS.SEP + i);
        return await pget(keys);
    }
    if (typeof head[key + CS.SEP + '0'] === 'string') return await pget(null);
    return head;   // 正常小对象：只有主键，一次读完
}

// ★ 读脚本必须"连片一起读": storage.local 有【单项 5MB】上限(不随 unlimitedStorage 解除),
//   脚本列表挤在一个键里很容易超 —— 超了的表现是保存没反应、读出来是旧的。
//   codestore 把大对象切成 1MB 的片存在 scripts#0..n, 这里把主键+片一起取回来拼。
//   ⚠ pget(null) 是"取全部" —— 不能只取 ['scripts'], 那样片永远读不到。
// 热路径缓存：alarm 每分钟跑 syncScripts → loadScripts，不缓存的话每分钟都要把
//   (含 7MB 锦囊袋的)整组从 storage 读出来再 JSON.parse + 哈希。SW 被回收后缓存自然
//   清空(下次冷启动读一次)；存活期内的写操作都经 'syncScripts'/'utilReRegister' 消息
//   入口，那两个 handler 先 invalidateScripts() 再跑，所以缓存不会读到旧脚本。
var _scriptsMem = null;
function invalidateScripts() { _scriptsMem = null; }
async function loadScripts() {
    if (_scriptsMem) return _scriptsMem;
    var store = await loadStoreKey('scripts');
    var arr = S.seedScripts(CS.readFrom(store, 'scripts'));
    if (arr && arr.__sgsChunkError) {
        // 片被清掉一半 —— 宁可当空列表重新种子, 也不要拿半截 JSON 去注册
        console.warn('[全能扩展] 脚本分片损坏: ' + arr.__sgsChunkError);
        arr = S.seedScripts(null);
    }
    // 孤儿片刚被救回来 → 立刻按"分片格式"写回去, 让主键重新变成目录。
    //   不写回的话每次都要靠全量扫描救一次, 而且孤儿片会一直占着空间。
    if (arr && arr.__sgsRecovered) {
        console.warn('[全能扩展] ' + arr.__sgsRecovered + ' → 已重新写回');
        delete arr.__sgsRecovered;
        if (Array.isArray(arr)) await saveScripts(arr);
    }
    var s = { scripts: arr };
    // ★ 走分片写(saveScripts)而不是 pset({scripts:arr})：后者把整组(含 7MB 锦囊袋)
    //   原样塞进单个键，绕开了 codestore 分片 —— 正是 v4.140「锦囊袋凭空消失」的根因
    //   (某些环境单项 5MB 上限生效时静默失败)。saveScripts 自动按 1MB 切分。
    if (!Array.isArray(s.scripts) || !s.scripts.length) await saveScripts(arr);   // 种子落盘一次
    // v4.81★ 主脚本条目的说明文字随版本变（1.2 及以前写的是"随扩展自动注入"），
    //   而 storage 里那份是【第一次落盘时的旧文案】—— 每次读都对齐一遍,
    //   否则界面上会写着"随扩展注入"、实际却是从助手拉的, 自相矛盾。
    var seed = S.builtinSeed(), dirty = false;
    for (var i = 0; i < arr.length; i++) {
        if (!arr[i] || !arr[i].builtin) continue;
        if (arr[i].description !== seed.description || arr[i].name !== seed.name) {
            arr[i].name = seed.name;
            arr[i].description = seed.description;
            dirty = true;
        }
    }
    // ★ 同样走分片写: 内置种子文案每次对齐时整组(含 7MB 锦囊袋)落盘,
    //   不能用 pset 单键写, 否则又会回到 v4.140 的丢脚本事故。
    if (dirty) await saveScripts(arr);
    _scriptsMem = arr;
    return arr;
}

// 写脚本走分片: ① 算这次的写入计划; ② 清掉上一次留下的多余尾巴片(片数变少时,
//   不清的话下次拼回来会多出一截旧数据); ③ 落盘。
async function saveScripts(arr) {
    var plan = CS.planWrite('scripts', arr);
    if (!plan.ok) return { ok: false, error: plan.error };
    // ★ 清理不依赖"目录里写的片数": 直接把 #plan.chunks 之后可能存在的片全列出来删。
    //   删不存在的键无害, 但漏删会留下孤儿片 —— 那正是"脚本凭空消失"的成因。
    var del = CS.staleChunks('scripts', plan.chunks);
    if (del.length) {
        await new Promise(function (resolve) { chrome.storage.local.remove(del, function () { resolve(); }); });
    }
    await pset(plan.set);
    return { ok: true, chunks: plan.chunks, bytes: plan.bytes };
}

// 让"期望的注册状态"真的在浏览器里。返回 {ok, registered, errors[]}
async function syncScripts() {
    var rd = userScriptsReady();
    if (!rd.ok) return { ok: false, error: rd.error, registered: 0, errors: [] };

    // ★ 总开关关了 → 一条都别注册, 已注册的全撤(并且清掉指纹, 免得下次开着时
    //   因为"指纹没变"被当成已注册而跳过 → 出现"开关打开了但页面没脚本"的怪事)。
    var cfg0 = await loadCfg();
    if (!cfg0.scriptsEnabled) {
        var regs0, ids0 = [];
        try { regs0 = await pgetReg(); } catch (e) { regs0 = []; }
        regs0.forEach(function (r) {
            if (r.id === B.BUILTIN_ID || r.id.indexOf(S.ID_PREFIX) === 0) ids0.push(r.id);
        });
        var err0 = [];
        if (ids0.length) {
            try { await punreg(ids0); } catch (e) { err0.push('注销失败: ' + userScriptsErr(e)); }
        }
        await pset({ scriptFp: {} });
        return { ok: err0.length === 0, error: err0.join('；'), registered: 0,
                 errors: err0, stopped: ids0.length };
    }

    var scripts = S.sortScripts(await loadScripts());
    var reg, fpStore;
    try {
        reg = await pgetReg();
        fpStore = (await pget(['scriptFp'])).scriptFp || {};
    } catch (e) {
        return { ok: false, error: userScriptsErr(e), registered: 0, errors: [] };
    }
    var have = {};
    reg.forEach(function (r) { have[r.id] = true; });

    // 期望集合：启用的用户脚本（builtin 走 manifest，不在这里）
    var want = {};
    scripts.forEach(function (s) {
        if (!s.builtin && s.enabled !== false) want[s.id] = scriptHash(s);
    });

    var toUnreg = Object.keys(have).filter(function (id) {
        return id.indexOf(S.ID_PREFIX) === 0 && !(id in want);
    });
    // ★ 不能只信指纹: 浏览器重启 / 扩展装更新会把 userScripts 注册【清空】,
    //   但 storage 里的指纹还留着 —— 只比对指纹就会得出"没变, 不用注册"的结论,
    //   于是脚本永久消失, 而且【不报错】(界面上只看到"脚本没生效")。
    //   真机实测过: 关了总开关再打开, 第一次同步注册 0 条、第二次才 1 条, 就是这个原因。
    //   所以【浏览器里实际没有的】一律要注册, 指纹只用来判断"内容变了要重装"。
    var toReg = Object.keys(want).filter(function (id) {
        return !have[id] || fpStore[id] !== want[id];
    });

    var errors = [];
    if (toUnreg.length) {
        try { await punreg(toUnreg); } catch (e) { errors.push('注销失败: ' + e.message); }
    }
    // 先并行注销(互不依赖), 再逐条注册 —— 注册要先把 @require 的库拉下来,
    //   这一步有网络, 并发起来容易把"哪个库拉不到"的报错搞乱, 保持串行。
    if (toReg.length) {
        await Promise.all(toReg.map(function (id) {
            return punreg([id]).catch(function () { /* 本来不在, 删失败无所谓 */ });
        }));
    }
    var failedIds = {};
    for (var i = 0; i < toReg.length; i++) {
        var s = null;
        for (var k = 0; k < scripts.length; k++) if (scripts[k].id === toReg[i]) s = scripts[k];
        if (!s) continue;
        try {
            var libs = await fetchRequires(s);
            var code = libs ? S.composeCode(s, libs) : String(s.code || '');
            await pregister(S.userScriptEntry(s, code));
            fpStore[s.id] = want[s.id];
        } catch (e) {
            failedIds[s.id] = true;
            errors.push(s.name + ': ' + userScriptsErr(e));
        }
    }
    await pset({ scriptFp: fpStore });
    // ★ 按精确 id 判失败(之前的 errors.some(x=>x.indexOf(id)>=0) 会把 id 被别的
    //   脚本名包含的也算成失败, 导致 registered 数虚低)。
    var registered = Object.keys(want).filter(function (id) {
        return !failedIds[id];
    }).length;
    return { ok: errors.length === 0, error: errors.join('；'), registered: registered, errors: errors };
}

// 列表只出去"能公开的"字段（代码很大，列表页不需要）
// B1 脚本检查更新：按 @downloadURL/@updateURL 抓最新正文，和库里这份比。
//   抓到的正文暂存 pendingUpdate(不走 sendMessage 回传，免得撞 4.5MB 单消息上限)，
//   用户确认后 scriptApplyUpdate 再取出来落库 + 重注册。
// B1 兜底: 抓第三方脚本正文。先直连(有主机权限时不受 CORS 限制)；连不上/被 CORS 挡/
//   用户没授权该站时, 改走助手 /ext/script-proxy 代拉(助手是 Python、无 CORS, 也免去给浏览器开站点权限)。
async function fetchScriptText(url, port) {
    var directErr = '';
    try {
        var resp = await fetch(url, { cache: 'no-store' });
        if (resp.ok) return { ok: true, text: await resp.text(), via: 'direct' };
        directErr = 'HTTP ' + resp.status;
    } catch (e) { directErr = (e && e.message) || String(e); }
    var base = M.baseUrlFromPort(port);
    if (!base) return { ok: false, error: '直连失败(' + directErr + ')，且没设助手端口、无法代拉' };
    try {
        var r2 = await fetch(base + '/ext/script-proxy?url=' + encodeURIComponent(url), { cache: 'no-store' });
        if (r2.ok) return { ok: true, text: await r2.text(), via: 'proxy' };
        var ej = '';
        try { var j = await r2.json(); ej = (j && (j.error || j.message)) || ''; } catch (e2) { }
        return { ok: false, error: '直连失败(' + directErr + ')；助手代拉也失败(HTTP ' + r2.status + (ej ? '：' + ej : '') + ')' };
    } catch (e) {
        return { ok: false, error: '直连失败(' + directErr + ')；助手代拉连不上(' + ((e && e.message) || e) + ')' };
    }
}

async function scriptCheckUpdate(id) {
    var scripts = await loadScripts();
    var s = null;
    for (var i = 0; i < scripts.length; i++) if (scripts[i].id === id) s = scripts[i];
    if (!s) return { ok: false, error: '找不到这条脚本' };
    if (s.builtin) return { ok: false, error: '内置主脚本不走这里 —— 去助手「资源替换」页点『更新全能扩展脚本』' };
    var url = s.downloadURL || s.updateURL;
    if (!url) return { ok: false, error: '这条脚本没有 @downloadURL / @updateURL，没法自动检查更新' };
    var cfg = await loadCfg();
    var got = await fetchScriptText(url, cfg.port);
    if (!got.ok) return { ok: false, error: got.error };
    var text = got.text;
    var plan = S.planUpdate(s, text);
    if (!plan.ok) return { ok: false, error: plan.error };
    if (!plan.hasUpdate) {
        return { ok: true, hasUpdate: false, oldVersion: plan.oldVersion, newVersion: plan.newVersion, url: url };
    }
    await pset({ pendingUpdate: { id: id, url: url, code: text, at: Date.now() } });
    return { ok: true, hasUpdate: true, oldVersion: plan.oldVersion, newVersion: plan.newVersion, url: url };
}

async function scriptApplyUpdate(id) {
    var pu = (await pget(['pendingUpdate'])).pendingUpdate;
    if (!pu || pu.id !== id) return { ok: false, error: '没有待更新的内容 —— 请先点「检查更新」' };
    var scripts = await loadScripts();
    var idx = -1;
    for (var i = 0; i < scripts.length; i++) if (scripts[i].id === id) idx = i;
    if (idx < 0) return { ok: false, error: '脚本不见了' };
    var plan = S.planUpdate(scripts[idx], pu.code);
    if (!plan.ok) { await pset({ pendingUpdate: null }); return { ok: false, error: plan.error }; }
    if (!plan.hasUpdate) { await pset({ pendingUpdate: null }); return { ok: true, hasUpdate: false }; }
    var arr = scripts.slice();
    arr[idx] = plan.newScript;
    var w = await saveScripts(arr);
    if (!w.ok) return { ok: false, error: w.error || '保存失败' };
    invalidateScripts();
    await pset({ pendingUpdate: null });
    var sr = await syncScripts();
    return { ok: true, hasUpdate: true, newVersion: plan.newVersion, registered: sr.registered };
}

// 列表只出去"能公开的"字段（代码很大，列表页不需要）
function publicScript(s) {
    return {
        id: s.id, name: s.name, version: s.version, description: s.description,
        matches: s.matches, enabled: s.enabled !== false, builtin: !!s.builtin,
        addedAt: s.addedAt || 0, codeSize: String(s.code || '').length,
        updateURL: String(s.updateURL || ''), downloadURL: String(s.downloadURL || '')
    };
}

async function listScripts() {
    var scripts = await loadScripts();
    var rd = userScriptsReady();
    return {
        ok: true,
        scripts: scripts.map(publicScript),
        userScriptsOk: rd.ok,
        userScriptsError: rd.ok ? '' : rd.error
    };
}

// ================================================================ 主脚本下发（v4.81★）
// 以前主脚本是 page/bridge.main.js，manifest 里当 content script 内置 —— 于是"扩展里
// 那份永远是打包那一刻的"：源脚本改了，扩展还是旧行为，而且【不报错】。
// 现在改成三条：① 常问 /ext/bridge.meta（几十字节）比 rev；② 变了才拉正文（1MB 多）；
//   ③ 存进 storage（离线用上次那份）并用 chrome.userScripts 注册进页面。
//   注册用 B.BUILTIN_ID —— syncScripts() 清"多余注册"只认 sgs-user- 前缀，不会误删它。
var BRIDGE_MIN_GAP_MS = 15000;      // 页面每 20 秒催一次，这里节流成最多 15 秒问一次
var _bridgeBusy = false;
var _bridgeLast = 0;

async function bridgeCache() {
    var s = await pget(['builtinCode', 'builtinRev', 'builtinVer', 'builtinAt', 'builtinRegRev']);
    return {
        code: s.builtinCode || '', rev: s.builtinRev || '', ver: s.builtinVer || '',
        at: s.builtinAt || 0, regRev: s.builtinRegRev || ''
    };
}

// HTTP 非 200 时把【助手写在正文里的原因】抠出来：状态码只说明"助手这边的点出错",
//   真原因在正文里(常见的一条: 源脚本 @version 与 SCRIPT_VER 不一致 → 下发直接 500)。
//   取不到就给占位 —— 宁可显示"(助手没说原因)", 也别让整句错误变成空白。
async function httpErrText(r) {
    var t = '';
    try { t = await r.text(); } catch (e) { t = ''; }
    try {
        var j = JSON.parse(t);
        if (j && (j.error || j.message)) return String(j.error || j.message);
    } catch (e2) { /* 不是 JSON(纯文本错误页/HTML) —— 走下面截断原文 */ }
    t = String(t || '').replace(/\s+/g, ' ').trim();
    return t ? t.slice(0, 200) : '(助手没说原因)';
}

async function bridgeFetchMeta(port) {
    var url = B.metaUrl(port);
    if (!url) return { ok: false, error: '端口没设好' };
    try {
        var r = await fetch(url, { cache: 'no-store' });
        // 500 的【正文里】才有真原因(例: "@version 与 SCRIPT_VER 不一致 —— 先把版本改齐
        //   再下发")。只报一个状态码的话, 用户看到的就是干巴巴一句"助手回了 HTTP 500",
        //   既不知道是脚本本身的问题还是助手坏了, 也无从下手 —— 所以把正文带上。
        if (!r.ok) return { ok: false, error: '助手回了 HTTP ' + r.status + '（' + B.META_PATH + '）'
            + '：' + (await httpErrText(r)) };
        return B.parseMeta(await r.json());
    } catch (e) {
        return { ok: false, error: '助手连不上（没开或端口不对）: ' + ((e && e.message) || e) };
    }
}

async function bridgeFetchCode(port) {
    var url = B.codeUrl(port);
    if (!url) return { ok: false, error: '端口没设好' };
    try {
        var r = await fetch(url, { cache: 'no-store' });
        if (!r.ok) return { ok: false, error: '助手回了 HTTP ' + r.status + '：' + (await httpErrText(r)) };
        var code = await r.text();
        var s = B.sanityCode(code);
        if (!s.ok) return { ok: false, error: s.error };
        return { ok: true, code: code };
    } catch (e) {
        return { ok: false, error: '取正文失败: ' + ((e && e.message) || e) };
    }
}

async function bridgeRegister(code) {
    var rd = userScriptsReady();
    if (!rd.ok) return { ok: false, error: rd.error };
    try {
        try { await punreg([B.BUILTIN_ID]); } catch (e) { /* 本来就没注册，删失败无所谓 */ }
        await pregister(B.builtinEntry(code));
        return { ok: true };
    } catch (e) {
        return { ok: false, error: userScriptsErr(e) };
    }
}

// 离线兜底：助手连不上且本地也没缓存时，用扩展内置的那份桥接正文注册进页面
//   （lib/bridge.bundle.js，由 core/ext_script.render_bridge_main 生成，与 /ext/bridge.main.js 同源）。
async function tryEmbeddedFallback(cache) {
    if (cache && cache.code) return null;            // 已有缓存(不论来源)就不必用内置
    var e = await B.embeddedCode();
    if (!e.ok) return { ok: false, embeddedError: e.error };
    var s = B.sanityCode(e.code);
    if (!s.ok) return { ok: false, embeddedError: s.error };
    var reg = await bridgeRegister(e.code);
    if (!reg.ok) return { ok: false, embeddedError: reg.error };
    var ver = B.codeVer(e.code) || '';
    await pset({ builtinCode: e.code, builtinRev: 'embedded', builtinVer: ver,
                 builtinAt: Date.now(), builtinRegRev: 'embedded' });
    await saveStat({ bridgeVer: ver, bridgeRev: 'embedded', bridgeAt: Date.now(),
                     bridgeError: '', bridgeRegistered: true, bridgeCached: true,
                     bridgeSize: e.code.length, bridgeEmbedded: true });
    return { ok: true, embedded: true, ver: ver, rev: 'embedded', reason: 'embedded' };
}

// force=true 忽略节流（界面上点「立即检查更新」/ 装扩展后第一次）
async function syncBridge(force) {
    var now = Date.now();
    if (_bridgeBusy) return { ok: false, skipped: true, error: '上一次还没跑完' };
    if (!force && now - _bridgeLast < BRIDGE_MIN_GAP_MS) {
        return { ok: true, skipped: true, reason: 'throttled' };
    }
    _bridgeBusy = true;
    _bridgeLast = now;
    try {
        var cfg = await loadCfg();
        await ensurePort(cfg);          // v4.161★ 端口连不上就自动找真在跑的(改端口自愈)
        // ★ 总开关关了 → 主脚本(桥接)也不许注册; 已经注册的要撤掉。
        //   不拦这里的话, syncScripts 刚注销完, 每分钟的 alarm 又把它装回去。
        if (!cfg.scriptsEnabled) {
            var regs1 = [];
            try { regs1 = await pgetReg(); } catch (e) { regs1 = []; }
            if (regs1.some(function (r) { return r.id === B.BUILTIN_ID; })) {
                try { await punreg([B.BUILTIN_ID]); } catch (e) { /* 撤不掉也别炸 */ }
            }
            await saveStat({ bridgeRegistered: false, bridgeError: '脚本总开关已关闭',
                             bridgeAt: Date.now() });
            return { ok: true, skipped: true, reason: 'scriptsDisabled' };
        }
        var cache = await bridgeCache();
        var m = await bridgeFetchMeta(cfg.port);
        if (!m.ok) {
            // 助手不在 —— 先试内置兜底（离线可用），不行再如实记错误
            await saveStat({ bridgeError: m.error, bridgeAt: Date.now(),
                             bridgeVer: cache.ver, bridgeRev: cache.rev });
            var fb = await tryEmbeddedFallback(cache);
            if (fb && fb.ok) return { ok: true, embedded: true, ver: fb.ver, rev: fb.rev, cached: false, reason: 'embedded' };
            return { ok: false, error: m.error, ver: cache.ver, rev: cache.rev, cached: !!cache.code };
        }
        var plan = B.planUpdate(cache, m, { force: !!force });
        if (!plan.needFetch && !plan.needRegister) {
            await saveStat({ bridgeError: '', bridgeVer: cache.ver, bridgeRev: cache.rev,
                             bridgeAt: cache.at, bridgeCached: true });
            return { ok: true, unchanged: true, ver: cache.ver, rev: cache.rev, cached: true };
        }
        if (plan.needFetch) {
            var got = await bridgeFetchCode(cfg.port);
            if (!got.ok) {
                await saveStat({ bridgeError: got.error, bridgeAt: Date.now(),
                                 bridgeVer: cache.ver, bridgeRev: cache.rev });
                var fb2 = await tryEmbeddedFallback(cache);
                if (fb2 && fb2.ok) return { ok: true, embedded: true, ver: fb2.ver, rev: fb2.rev, cached: false, reason: 'embedded' };
                return { ok: false, error: got.error, ver: cache.ver, rev: cache.rev, cached: !!cache.code };
            }
            cache.code = got.code;
            cache.rev = m.rev || cache.rev;
            cache.ver = B.codeVer(got.code) || m.ver || cache.ver;
            cache.at = Date.now();
            await pset({ builtinCode: cache.code, builtinRev: cache.rev,
                         builtinVer: cache.ver, builtinAt: cache.at });
        }
        var reg = await bridgeRegister(cache.code);
        if (reg.ok) await pset({ builtinRegRev: cache.rev });
        await saveStat({ bridgeVer: cache.ver, bridgeRev: cache.rev, bridgeAt: cache.at,
                         bridgeError: reg.ok ? '' : reg.error, bridgeRegistered: reg.ok,
                         bridgeCached: true, bridgeSize: cache.code.length });
        return {
            ok: reg.ok, error: reg.error || '', updated: plan.needFetch, registered: reg.ok,
            ver: cache.ver, rev: cache.rev, reason: plan.reason
        };
    } finally {
        _bridgeBusy = false;
    }
}

// 管理页要显示的就是这几项：现在跑的是哪一版、什么时候取的、注册上了没有、上次为什么没取到
async function bridgeStatus() {
    var c = await bridgeCache();
    var st = await readStat();
    return {
        ok: true, ver: c.ver, rev: c.rev, regRev: c.regRev, at: c.at,
        cached: !!c.code, size: String(c.code || '').length,
        registered: !!st.bridgeRegistered, error: st.bridgeError || '', checkedAt: st.bridgeAt || 0
    };
}

// 实用工具：连一下助手，量个延迟。只读 /rules，不碰任何协议数据。
async function utilTest() {
    var cfg = await loadCfg();
    var b = M.baseUrlFromPort(cfg.port);
    if (!b) return { ok: false, error: '端口没设好' };
    var t0 = Date.now();
    try {
        var resp = await fetch(b + '/rules', { cache: 'no-store' });
        if (!resp.ok) return { ok: false, error: 'HTTP ' + resp.status, ms: Date.now() - t0 };
        var obj = await resp.json();
        return {
            ok: true, ms: Date.now() - t0, season: obj.season || '',
            rev: obj.rev || '', rules: Array.isArray(obj.rules) ? obj.rules.length : 0
        };
    } catch (e) {
        return { ok: false, error: '连不上：' + (e && e.message ? e.message : e), ms: Date.now() - t0 };
    }
}

// N1 一键诊断：把"脚本/规则为什么没生效"的常见原因一次查清，输出人话报告。
async function diagnose() {
    var lines = [];
    var cfg = await loadCfg();
    await ensurePort(cfg);       // v4.161★ 端口连不上先自动找真在跑的, 诊断才准
    // ① 助手连接
    var t = await utilTest();
    if (t.ok) lines.push({ lv: 'ok', t: '助手连接正常（' + t.ms + 'ms，' + t.rules + ' 条规则，端口 ' + cfg.port + '）' });
    else lines.push({ lv: 'bad', t: '助手连不上：' + t.error + ' —— 检查助手是否运行、端口是否为 ' + cfg.port });
    // ② userScripts 可用性(主脚本也走它)
    var rd = userScriptsReady();
    if (!rd.ok) lines.push({ lv: 'bad', t: 'userScripts 不可用：' + rd.error });
    else lines.push({ lv: 'ok', t: 'userScripts 可用（Chromium 120+ / 已开开发者模式）' });
    // ③ 脚本总开关 + 主脚本
    if (!cfg.scriptsEnabled) lines.push({ lv: 'warn', t: '脚本总开关关着 —— 一个脚本都不注入（含主脚本）' });
    var bs = await bridgeStatus();
    if (cfg.scriptsEnabled) {
        if (bs.registered) lines.push({ lv: 'ok', t: '主脚本已注入（v' + (bs.ver || '?') + (bs.cached ? '，本地有缓存' : '') + '）' });
        else if (bs.cached) lines.push({ lv: 'warn', t: '主脚本取了但没注册进页面：' + (bs.error || '试「重新注入全部脚本」') });
        else lines.push({ lv: 'bad', t: '主脚本没取到：' + (bs.error || '助手没开，或没点『更新全能扩展脚本』') });
    }
    // ④ 重定向规则(实查)
    var liveN = 0;
    try {
        liveN = (await new Promise(function (res) {
            chrome.declarativeNetRequest.getDynamicRules(function (r) { res(r || []); });
        })).length;
    } catch (e) { /* 显示 0 */ }
    if (!cfg.enabled) lines.push({ lv: 'warn', t: '重定向规则已暂停（「暂停」只撤规则、不停脚本）' });
    else if (liveN > 0) lines.push({ lv: 'ok', t: '重定向规则生效中：' + liveN + ' 条' });
    else lines.push({ lv: 'bad', t: '0 条重定向规则 —— 助手没开且没手写规则，或同步失败' });
    // ⑤ 自添脚本
    var scripts = await loadScripts();
    var nUser = scripts.filter(function (s) { return !s.builtin && s.enabled !== false; }).length;
    lines.push({ lv: 'info', t: '自添脚本：' + nUser + ' 条启用' + (cfg.scriptsEnabled ? '' : '（总开关关，未注入）') });
    // ⑥ 游戏页实探
    var cp = await checkPage();
    if (cp.ok && cp.alive) lines.push({ lv: 'ok', t: '游戏页检测到主脚本（v' + (cp.ver || '?') + (cp.engine ? '，' + cp.engine : '') + '）' });
    else if (cp.ok) lines.push({ lv: 'warn', t: '找到游戏页但没检测到主脚本 —— 刷新一下游戏页' + (cp.note ? '（' + cp.note + '）' : '') });
    else lines.push({ lv: 'info', t: '没找到打开着的游戏页（' + cp.error + '）' });
    return { ok: true, lines: lines };
}

async function saveStat(patch) {
    var s = await pget(['stat']);
    var stat = s.stat && typeof s.stat === 'object' ? s.stat : {};
    Object.keys(patch).forEach(function (k) { stat[k] = patch[k]; });
    await pset({ stat: stat });
    return stat;
}

async function readStat() {
    var s = await pget(['stat']);
    return (s.stat && typeof s.stat === 'object') ? s.stat : {};
}

// ---------------------------------------------------------------- 主体
async function clearAll() {
    var cfg = await loadCfg();
    if (cfg.appliedIds.length) {
        await pdnr({ removeRuleIds: cfg.appliedIds.slice(), addRules: [] });
    }
    await pset({ appliedIds: [], appliedFp: '' });   // 指纹一起清，免得下次"没变"误跳过
}

// 拉助手规则 → 失败不抛，返回 {rules, error, season, rev}
// N4: SW 内存缓存上次解析好的助手规则，按 rev 判断要不要重新拉整包
var _srvRulesCache = { rev: '', season: '', rules: null };
async function fetchServerRules(port) {
    var base = M.baseUrlFromPort(port);
    // 先问版本卡(几十字节)，rev 没变就复用上次的规则，跳过整包 /rules。
    //   手动规则不在这里(它在 sync 里每次从 storage 现取并 merge)，所以缓存不会让手写规则变旧。
    if (base && _srvRulesCache.rules) {
        try {
            var mr = await fetch(base + '/rules.meta', { cache: 'no-store' });
            if (mr.ok) {
                var meta = await mr.json();
                if (meta && meta.rev && meta.rev === _srvRulesCache.rev) {
                    return { rules: _srvRulesCache.rules, error: '',
                             season: meta.season || _srvRulesCache.season, rev: meta.rev };
                }
            }
        } catch (e) { /* 问不到就老实拉全量 */ }
    }
    try {
        var resp = await fetch(R.rulesUrl(port), { cache: 'no-store' });
        if (!resp.ok) throw new Error('助手回了 HTTP ' + resp.status);
        var parsed = R.parseRulesPayload(await resp.text());
        if (!parsed.ok) return { rules: [], error: parsed.error, season: '', rev: '' };
        _srvRulesCache = { rev: parsed.rev, season: parsed.season, rules: parsed.rules };
        return { rules: parsed.rules, error: '', season: parsed.season, rev: parsed.rev };
    } catch (e) {
        return {
            rules: [],
            error: '助手连不上(没开或端口不对): ' + (e && e.message ? e.message : e),
            season: '', rev: ''
        };
    }
}

async function sync(reason) {
    var cfg = await loadCfg();

    // v4.161★ 端口自动探测: 当前端口连不上就试着找真正在跑的那个(用户改了助手端口
    //   却没在扩展里改时重定向会全失效)。命中后写回 storage, 下面 fetchServerRules
    //   用新端口拉到的规则(url 指向新端口的图片服务) → 重定向自动恢复。
    if (await ensurePort(cfg)) await saveStat({ portAutoSwitched: cfg.port, at: Date.now() });

    if (!cfg.enabled) {
        try { await clearAll(); } catch (e) { /* 已空则无可清 */ }
        if (cfg.badge) setBadge('off', '#5f5e5a'); else setBadge('', null);
        return { ok: true, paused: true, reason: reason };
    }

    // ① 手动规则先算出来 —— 它在本地，永远可用；助手挂了它就是唯一的规则来源
    var manual = await loadManual();

    // ② 助手规则（取不到不算致命：手动规则还能顶上）
    var srv = await fetchServerRules(cfg.port);

    // ③ 合并（手动在前 → 同 pattern 手动赢；enabled=false 的只是留着不下发）
    var merged = M.mergeRules(srv.rules, manual).filter(function (r) {
        return r.enabled !== false;
    });
    var built = R.buildDnrRules(merged, {});
    // ★ A1: 规则没变就别动 DNR。以前每次 sync 都 diffRules(appliedIds, built) →
    //   removeRuleIds=旧∪新、addRules=全部，于是一字节没变也每 20 秒 remove+add 全部规则。
    //   现在存一份 appliedFp(built.rules 的指纹)，指纹和条数都没变就整段跳过提交。
    var fp = JSON.stringify(built.rules);
    var unchanged = (cfg.appliedFp === fp) && (cfg.appliedIds.length === built.rules.length);
    if (!unchanged) {
        // ④ 提交（先删后加：同 id 改了目标地址时，光 add 会因 id 冲突整批失败）
        var diff = R.diffRules(cfg.appliedIds, built.rules);
        if (diff.removeRuleIds.length || diff.addRules.length) {
            try {
                await pdnr(diff);
            } catch (e) {
                var em = '提交规则失败: ' + (e && e.message ? e.message : e);
                await saveStat({ at: Date.now(), error: em, reason: reason, degraded: false });
                setBadge('!', '#a32d2d');
                return { ok: false, error: em, reason: reason };
            }
        }
        await pset({ appliedIds: built.rules.map(function (r) { return r.id; }), appliedFp: fp });
    }

    // 降级 = 助手连不上但仍提交了规则（全部来自手动/上次的助手规则还在仓库里也行）
    var degraded = !!srv.error;
    await saveStat({
        at: Date.now(),
        rev: srv.rev,
        season: srv.season,
        count: built.rules.length,
        manual: M.activeManual(manual),
        skipped: built.skipped,
        error: srv.error,
        degraded: degraded,
        reason: reason
    });
    if (built.rules.length === 0) {
        if (cfg.badge) setBadge('0', '#a32d2d');          // 一条都没有 = 该去看管理页了
    } else if (cfg.badge) {
        setBadge(built.rules.length, degraded ? '#a3762d' : '#185fa5');
    } else {
        setBadge('', null);                               // 设置里关了角标就清干净
    }
    return {
        ok: true, count: built.rules.length, manual: M.activeManual(manual),
        rev: srv.rev, season: srv.season, skipped: built.skipped,
        degraded: degraded, reason: reason
    };
}

async function status() {
    var cfg = await loadCfg();
    var stat = await readStat();
    var manual = await loadManual();
    var live = { count: 0, regexCount: 0 };
    try {
        var got = await new Promise(function (resolve, reject) {
            chrome.declarativeNetRequest.getDynamicRules(function (rules) {
                var e = chrome.runtime.lastError;
                if (e) reject(new Error(e.message)); else resolve(rules || []);
            });
        });
        live.count = got.length;
        live.regexCount = got.filter(function (r) { return r.condition && r.condition.regexFilter; }).length;
    } catch (e) { /* 取不到就如实显示 0，不编 */ }
    return {
        ok: true,
        enabled: cfg.enabled,               // 重定向规则开关
        scriptsEnabled: cfg.scriptsEnabled, // 脚本注入总开关(跟上面是两回事)
        port: cfg.port,
        base: M.baseUrlFromPort(cfg.port),
        rulesUrl: R.rulesUrl(cfg.port),
        autoSync: cfg.autoSync,
        badge: cfg.badge,
        applied: cfg.appliedIds.length,
        live: live,
        manualEntries: manual,
        injectedUser: (await loadScripts()).filter(function (s) { return !s.builtin && s.enabled !== false; }).length,
        stat: stat,
        bridge: await bridgeStatus(),          // v4.81★ 主脚本现在从助手下发，状态一起给管理页
        version: chrome.runtime.getManifest().version
    };
}

// 「检查游戏页」：当前标签页是不是游戏页、油猴本体进去了没有、版本多少。
//   探针必须跑在【页面主世界】(world:'MAIN') —— window.__sgsExtPage / window.SCRIPT_VER
//   是 page/bridge.main.js 挂在页面上的，隔离世界看不见。
//
//   ⚠ 用户是在【管理页】里点的按钮 —— "当前激活标签页"往往就是管理页自己
//   (chrome-extension://<ID>/options.html，hostname 长得像扩展 ID)。所以流程是:
//   当前标签页是游戏页 → 直接探；不是 → 去所有窗口里找开着的游戏页(优先激活的)
//   再探；实在一个都没有，才提示先开游戏。
function _tabHost(t) {
    try { return new URL(t.url || '').hostname; } catch (e) { return ''; }
}

async function checkPage() {
    function q(opts) {
        return new Promise(function (resolve) {
            chrome.tabs.query(opts, function (t) { resolve(t || []); });
        });
    }

    var cur = (await q({ active: true, currentWindow: true }))[0];
    var tab = null;
    var note = '';

    if (cur && R.GAME_HOSTS.indexOf(_tabHost(cur)) >= 0) {
        tab = cur;                                        // 正好用：用户切到了游戏页
    } else {
        // 管理页(或别的什么页)在前台 → 全窗口找游戏页。激活的排前面。
        var all = await q({});
        var cands = all.filter(function (t) { return R.GAME_HOSTS.indexOf(_tabHost(t)) >= 0; });
        cands.sort(function (a, b) { return (b.active ? 1 : 0) - (a.active ? 1 : 0); });
        tab = cands[0] || null;
        if (tab) note = '(自动选中了另一个开着游戏页的标签窗口)';
    }

    if (!tab || !tab.id) {
        var looksExt = cur && /^chrome-extension:\/\//i.test(cur.url || '');
        return {
            ok: false,
            error: looksExt
                ? '没找到打开着的游戏页 —— 你现在看的是管理页自己，先开一个游戏页(不用关管理页)再点'
                : '没找到打开着的游戏页 —— 先打开游戏再点「检查游戏页」'
        };
    }

    var host = _tabHost(tab);
    if (R.GAME_HOSTS.indexOf(host) < 0) {
        return { ok: false, error: '当前标签页不是游戏页(' + (host || '未知') + ') —— 先打开游戏' };
    }
    var probe = await new Promise(function (resolve) {
        chrome.scripting.executeScript({
            target: { tabId: tab.id },
            world: 'MAIN',
            func: function () {
                return {
                    alive: !!(window.__sgsExtPage && window.__sgsExtPage.ok),
                    ver: window.SCRIPT_VER || '',
                    // __installed 是 installResSwap 的存在标记（见源脚本"对外接口"段）
                    swap: !!(window.__sgsResSwap && window.__sgsResSwap.__installed),
                    engine: (typeof Laya !== 'undefined') ? 'laya' : ''
                };
            }
        }, function (out) {
            var e = chrome.runtime.lastError;
            if (e) resolve({ ok: false, error: e.message });
            else resolve({ ok: true, probe: (out && out[0] && out[0].result) || null });
        });
    });
    if (!probe.ok) return { ok: false, error: '注入探针失败: ' + probe.error };
    var p = probe.probe || {};
    return {
        ok: true,
        host: host,
        alive: !!p.alive,
        ver: p.ver || '(脚本还没跑到版本号)',
        swapOn: !!p.swap,
        engine: p.engine || '',
        note: note
    };
}

// 找当前开着的游戏页（供「检查游戏页」和「刷新游戏页」共用）：优先激活窗口的，
//   没有激活的就挑任意一个开着的。管理页自身(chrome-extension://)不算游戏页。
function findGameTab() {
    return new Promise(function (resolve) {
        chrome.tabs.query({}, function (all) {
            var cands = (all || []).filter(function (t) { return R.GAME_HOSTS.indexOf(_tabHost(t)) >= 0; });
            cands.sort(function (a, b) { return (b.active ? 1 : 0) - (a.active ? 1 : 0); });
            resolve(cands[0] || null);
        });
    });
}

// 「刷新游戏页」：停用脚本/主脚本后，让"停止"立即生效（userScripts.unregister 只挡下次
//   注入，已跑在当前页里的脚本要刷新才撤）。会重开对局，所以是手动按钮、不自动触发。
async function reloadGamePage() {
    var tab = await findGameTab();
    if (!tab || !tab.id) return { ok: false, error: '没找到打开着的游戏页 —— 先打开游戏再点' };
    return new Promise(function (resolve) {
        chrome.tabs.reload(tab.id, {}, function () {
            var e = chrome.runtime.lastError;
            if (e) resolve({ ok: false, error: e.message });
            else resolve({ ok: true, tabId: tab.id });
        });
    });
}

// ---------------------------------------------------------------- 事件接线
// 扩展装/更新后：userScripts 注册会被浏览器清空 → 必须在这里重注册（规则 sync 顺带）。
chrome.runtime.onInstalled.addListener(function () {
    chrome.alarms.create(ALARM, { periodInMinutes: 1 });
    syncScripts();
    syncBridge(true);          // v4.81★ 主脚本是拉来的 —— 装/更新后第一件事就是把它取回来
    sync('installed');
});

chrome.runtime.onStartup.addListener(function () {
    chrome.alarms.create(ALARM, { periodInMinutes: 1 });
    syncScripts();
    syncBridge(true);          // 浏览器重启后注册会被清空 → 用缓存里的那份重新注册
    sync('startup');
});

chrome.alarms.onAlarm.addListener(function (a) {
    // autoSync 关掉时 alarm 留着但只刷脚本注册（保住"启停脚本能生效"），不拉规则
    (async function () {
        var cfg = await loadCfg();
        // C1: 脚本总开关关着时，没必要每分钟列一遍已注册脚本(syncScripts 关态只做 getScripts+注销，
        //     而开关切换那一刻已经注销过了)。
        if (cfg.scriptsEnabled) syncScripts();
        syncBridge(false);     // 每分钟顺带看一眼主脚本有没有新版（有节流，不会每分钟真拉）
        if (cfg.autoSync) sync('alarm');
    })();
});

// 页面侧（content/agent.js / popup / options）催同步：service worker 闲下来会被回收，
// 靠这里的唤醒 + 每分钟的 alarm 兜底，规则才真的能"改完就生效"。
chrome.runtime.onMessage.addListener(function (msg, sender, sendResponse) {
    var t = msg && msg.type;
    (async function () {
        try {
            if (t === 'sync') {
                var rs = await sync('content');
                sendResponse(rs);
                // 页面每 20 秒催一次（content/agent.js）—— 顺带问一句主脚本有没有新版。
                //   不 await: 规则同步要立刻回包，脚本那 1MB 不该拖着弹窗转圈。
                //   这就是"助手里点『更新全能扩展脚本』≤20 秒自动生效"那一下。
                syncBridge(false);
            }
            else if (t === 'bridgeUpdate') sendResponse(await syncBridge(true));
            else if (t === 'bridgeStatus') sendResponse(await bridgeStatus());
            else if (t === 'status') sendResponse(await status());
            else if (t === 'checkPage') sendResponse(await checkPage());
            else if (t === 'diagnose') sendResponse(await diagnose());
            else if (t === 'reloadGame') sendResponse(await reloadGamePage());
            else if (t === 'manualChanged') sendResponse(await sync('manual'));
            else if (t === 'setPort') {
                var port = parseInt(msg.port, 10);
                if (!R.isLocalPort(port)) { sendResponse({ ok: false, error: '端口要在 1~65535 之间' }); return; }
                await clearAll();                       // 换端口后旧规则的 id 一并清掉
                await pset({ port: port, appliedIds: [] });
                await saveStat({ error: '' });
                sendResponse(await sync('port'));
            } else if (t === 'setEnabled') {
                await pset({ enabled: !!msg.enabled });
                sendResponse(await sync('toggle'));
            } else if (t === 'scriptList') {
                sendResponse(await listScripts());
            } else if (t === 'syncScripts') {
                // 管理页已把脚本(含代码)直接写进 chrome.storage.local（绕开 sendMessage
                // 4.5MB 单消息上限），这里只负责按 storage 现状重新注册进页面。
                invalidateScripts();          // 外面刚写过 storage → 丢掉内存缓存再读，别用旧的
                var sr = await syncScripts();
                sendResponse({ ok: sr.ok, registered: sr.registered,
                               regError: sr.ok ? '' : (sr.errors ? sr.errors.join('；') : (sr.error || '注入失败')) });
            } else if (t === 'scriptCheckUpdate') {
                sendResponse(await scriptCheckUpdate(String(msg.id || '')));
            } else if (t === 'scriptApplyUpdate') {
                sendResponse(await scriptApplyUpdate(String(msg.id || '')));
            } else if (t === 'utilTest') {
                sendResponse(await utilTest());
            } else if (t === 'utilReRegister') {
                await pset({ scriptFp: {} });            // 清指纹 → 全部按"变了"处理，强制重注册
                invalidateScripts();
                var rr = await syncScripts();
                sendResponse({ ok: rr.ok, error: rr.error, registered: rr.registered });
            } else if (t === 'sgsFetch') {
                // v4.153★ PNA 绕过(扩展侧): MAIN-world 脚本把本机请求转来, 让 background 代发
                //   (background 是扩展源、不受浏览器 PNA 拦)。仅接受 loopback, 返回 {status,headers,body}。
                (function () {
                    var _m = (msg && msg.method) || 'GET';
                    var _u = msg && msg.url;
                    if (!_u || !/^https?:\/\/(127\.0\.0\.1|localhost)(:\d+)?\//i.test(_u)) {
                        sendResponse({ __sgsErr: 'bad url' }); return;
                    }
                    fetch(_u, { method: _m,
                                headers: (msg && msg.headers) || {},
                                body: (msg && msg.body != null) ? msg.body : undefined })
                        .then(function (r) {
                            return r.text().then(function (txt) {
                                var _h = {};
                                r.headers.forEach(function (v, k) { _h[String(k).toLowerCase()] = v; });
                                sendResponse({ status: r.status, statusText: r.statusText || '',
                                               headers: _h, body: txt, url: r.url });
                            });
                        })
                        .catch(function (e) { sendResponse({ __sgsErr: String((e && e.message) || e) }); });
                })();
                return;
            } else if (t === 'setSetting') {
                // 设置页的开关（autoSync / badge / scriptsEnabled），键名白名单防乱写
                var key = (msg.key === 'autoSync' || msg.key === 'badge' ||
                           msg.key === 'scriptsEnabled') ? msg.key : '';
                if (!key) { sendResponse({ ok: false, error: '未知设置项' }); return; }
                await pset((function (o) { o[key] = !!msg.value; return o; })({}));
                // ★ 脚本开关改完必须【立刻】按新值同步一次: 否则关了要等一分钟的 alarm
                //   才生效, 用户会以为开关是坏的。
                if (key === 'scriptsEnabled') {
                    await syncScripts();
                    await syncBridge(true);
                }
                sendResponse({ ok: true });
            } else {
                sendResponse({ ok: false, error: '未知消息: ' + t });
            }
        } catch (e) {
            sendResponse({ ok: false, error: String((e && e.message) || e) });
        }
    })();
    return true;    // 异步回包
});

// B2 键盘快捷键：Alt+Shift+S 停用/启用脚本注入（可在 chrome://extensions/shortcuts 改键）
chrome.commands.onCommand.addListener(function (cmd) {
    if (cmd !== 'toggle-scripts') return;
    (async function () {
        var cfg = await loadCfg();
        await pset({ scriptsEnabled: !cfg.scriptsEnabled });
        await syncScripts();
        await syncBridge(true);      // 主脚本跟着一起停/启
    })();
});

// 打开游戏页就立刻同步一次（玩家刚开游戏 = 最需要规则是新的那一刻）
chrome.tabs.onUpdated.addListener(function (tabId, info, tab) {
    if (!info || !info.url) return;
    try {
        var u = new URL(info.url);
        if (R.GAME_HOSTS.indexOf(u.hostname) >= 0) {
            sync('tab');
            syncBridge(false);      // 玩家刚开游戏 = 最该确认"页面上跑的是不是最新那份"
        }
    } catch (e) { /* 非 http(s) 的 url 解析会抛，忽略 */ }
});

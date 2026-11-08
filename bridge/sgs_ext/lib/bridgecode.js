// 全能扩展 ·【主脚本下发】的纯逻辑层（可在 Node 里直接跑，见 dev/_verify_bridgecode.js）
//
// v4.81★ 主脚本【不再内置】进扩展。以前它是 page/bridge.main.js，由 manifest 当
// content script 注入 —— 代价是"扩展里那份永远是打包那一刻的"：源脚本改了，
// 扩展里那份还是旧的，而且【不报错】，只能靠重跑打包 + 浏览器里点"重新加载扩展"。
//
// 现在的链路：
//   助手 http://127.0.0.1:<端口>/ext/bridge.meta        → 版本卡（几十字节，常问）
//   助手 http://127.0.0.1:<端口>/ext/bridge.main.js      → 正文（1MB 多，变了才拉）
//   → 拉到就存 chrome.storage.local（离线用上次那份），再用 chrome.userScripts 注册进页面。
//
// 判定全收在这一个文件（和 lib/scriptreg.js 一个道理）：rev 对不上要不要重拉、
// 拉回来的是不是真脚本、注册条目长什么样 —— 这些错浏览器一个字都不会报，
// 现象只是"页面里没有脚本"。做成纯函数才能离线测。
(function (root) {
    'use strict';

    var BUILTIN_ID = 'sgs-builtin-bridge';
    var DEFAULT_MATCHES = ['*://web.sanguosha.com/*', '*://www.sanguosha.com/*'];
    var META_PATH = '/ext/bridge.meta';
    var CODE_PATH = '/ext/bridge.main.js';
    var EMBEDDED_PATH = 'lib/bridge.bundle.js';   // v4.81★ 离线兜底：扩展内置桥接正文（与 /ext/bridge.main.js 同源，由 core/ext_script.render_bridge_main 生成）
    var MIN_CODE = 20000;        // 主脚本 1MB 级；几 KB 一定是错误页/空响应，不当脚本用
    var CODE_MARK = '__sgsExtPage';   // render_bridge_main() 一定会加上的存活标记

    function isPort(p) {
        var n = parseInt(p, 10);
        return isFinite(n) && n > 0 && n < 65536;
    }

    function baseUrl(port) {
        return isPort(port) ? ('http://127.0.0.1:' + parseInt(port, 10)) : '';
    }

    function metaUrl(port) {
        var b = baseUrl(port);
        return b ? (b + META_PATH) : '';
    }

    function codeUrl(port) {
        var b = baseUrl(port);
        return b ? (b + CODE_PATH) : '';
    }

    // 版本卡 → {ok, ver, rev, size, error}。
    //   rev 是"内容指纹 + 发布时间戳"，只比字符串：不等就是有新版（内容没变但用户点了
    //   「更新」也算 —— 时间戳会变，这就是"我点了你就要去更新"那一下）。
    function parseMeta(obj) {
        if (!obj || typeof obj !== 'object') return { ok: false, error: '版本卡不是对象' };
        if (obj.ok === false) return { ok: false, error: String(obj.error || '助手说拿不到主脚本') };
        var rev = String(obj.rev || '');
        var ver = String(obj.ver || '');
        if (!rev && !ver) return { ok: false, error: '版本卡里既没有 rev 也没有版本（助手版本太旧？）' };
        return {
            ok: true,
            rev: rev,
            ver: ver,
            size: parseInt(obj.size, 10) || 0,
            publishedAt: parseInt(obj.publishedAt, 10) || 0,
            source: String(obj.source || '')
        };
    }

    // 从正文里抠版本号 —— 存的是代码，界面要显示的是"现在跑的是哪一版"。
    function codeVer(code) {
        var m = /const\s+SCRIPT_VER\s*=\s*'([^']+)'/.exec(String(code || ''));
        return m ? m[1] : '';
    }

    // 拉回来的东西到底是不是主脚本（助手没开时 127.0.0.1 上可能是别的服务的错误页）
    function sanityCode(code) {
        var c = String(code == null ? '' : code);
        if (c.length < MIN_CODE) {
            return { ok: false, error: '只有 ' + c.length + ' 字节，不是主脚本（助手端口上是不是跑了别的服务？）' };
        }
        if (c.indexOf(CODE_MARK) < 0) {
            return { ok: false, error: '正文里没有存活标记 ' + CODE_MARK + '，不像主脚本' };
        }
        return { ok: true };
    }

    // 要不要拉 / 要不要重新注册。cache = {code, rev, ver, regRev}
    //   regRev = 上一次【真的注册成功】那一版的 rev —— 代码存下来了但注册失败过，
    //   下次就不用再拉（省 1MB），只要补一次注册。
    function planUpdate(cache, m, opts) {
        var c = cache || {};
        var o = opts || {};
        var out = { needFetch: false, needRegister: false, reason: '' };
        if (!m || !m.ok) { out.error = (m && m.error) || '版本卡拿不到'; return out; }
        if (!c.code) { out.needFetch = true; out.reason = 'first'; return out; }
        if (o.force) { out.needFetch = true; out.reason = 'force'; return out; }
        if (m.rev && c.rev !== m.rev) { out.needFetch = true; out.reason = 'rev'; return out; }
        if (!m.rev && m.ver && c.ver && c.ver !== m.ver) { out.needFetch = true; out.reason = 'ver'; return out; }
        if (c.regRev !== c.rev) { out.needRegister = true; out.reason = 'reg'; return out; }
        out.reason = 'unchanged';
        return out;
    }

    // chrome.userScripts.register() 的一个条目（与 scriptreg.js 的 userScriptEntry 同款：
    // world:'MAIN' + document_start + allFrames ≈ 油猴 @grant none）。
    //   用 BUILTIN_ID（不是 sgs-user- 前缀）是刻意的：syncScripts() 清理"多余的注册"时
    //   只认 sgs-user- 前缀，内置这条不会被它误删。
    function builtinEntry(code) {
        return {
            id: BUILTIN_ID,
            matches: DEFAULT_MATCHES.slice(),
            js: [{ code: String(code || '') }],
            runAt: 'document_start',
            world: 'MAIN',
            allFrames: true
        };
    }

    // 离线兜底：扩展自带桥接正文（lib/bridge.bundle.js，gen 自 core/ext_script.render_bridge_main）。
    //   助手没开 / 端口不对时还能把脚本注入游戏页，不依赖本机助手先跑。
    async function embeddedCode() {
        try {
            var url = (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.getURL)
                ? chrome.runtime.getURL(EMBEDDED_PATH) : '';
            if (!url) return { ok: false, error: '扩展内找不到内置脚本(' + EMBEDDED_PATH + ')' };
            var r = await fetch(url, { cache: 'no-store' });
            if (!r.ok) return { ok: false, error: '内置脚本读取失败 HTTP ' + r.status };
            var code = await r.text();
            return { ok: true, code: code };
        } catch (e) {
            return { ok: false, error: '内置脚本读取异常: ' + ((e && e.message) || e) };
        }
    }

    var api = {
        BUILTIN_ID: BUILTIN_ID,
        DEFAULT_MATCHES: DEFAULT_MATCHES,
        META_PATH: META_PATH,
        CODE_PATH: CODE_PATH,
        EMBEDDED_PATH: EMBEDDED_PATH,
        MIN_CODE: MIN_CODE,
        isPort: isPort,
        baseUrl: baseUrl,
        metaUrl: metaUrl,
        codeUrl: codeUrl,
        embeddedCode: embeddedCode,
        parseMeta: parseMeta,
        codeVer: codeVer,
        sanityCode: sanityCode,
        planUpdate: planUpdate,
        builtinEntry: builtinEntry
    };

    if (typeof module !== 'undefined' && module.exports) module.exports = api;   // Node 测试
    else root.SGSBridgeCode = api;                                               // 扩展里挂 self
})(typeof self !== 'undefined' ? self : this);

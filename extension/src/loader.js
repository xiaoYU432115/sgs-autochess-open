/* 模块加载器 —— 在【主世界 MAIN】跑(document_start), 等价于油猴 @grant none。

   它只做四件事, 别的都交给模块:
     ① 建一个全局容器 window.__SGSEXT(模块注册表 + 错误账本 + 角标);
     ② 按清单把模块一个个取回来执行 —— 【每个模块独立 try/catch】;
     ③ 额外脚本(你自己加的 / 助手下发的)按同一套机制挂上来, 不用改本文件;
     ④ 角标显示各模块状态, 哪块红了直接写名字。

   ★ 为什么要有它(血泪教训):
     原来所有代码在【一个 2.3 万行的 IIFE】里, 顶层任何一句抛错都会把后面全部带走。
     实测案例: 角标那句 `(document.body||document.documentElement).appendChild(...)`
     在 document-start 时 body 还是 null → 一句抛错 → 动态皮肤 / 统计 / 剧本 全都没装,
     而 window.SCRIPT_VER 还有值, 看着"像装上了", 排查方向全是错的。
     现在模块各自隔离: 崩的那一块标红, 其他的照常干活。
*/
(function () {
    if (window.__SGSEXT && window.__SGSEXT.__on) return;

    var VER = '4.139';

    var EXT = {
        __on: true,
        ver: VER,
        modules: [],      // [{name, file, ok, err, ms}]
        errors: [],       // 出错的模块
        extra: [],        // 额外挂进来的脚本
        log: []
    };
    window.__SGSEXT = EXT;

    // ---------------------------------------------------------- 模块清单
    // 顺序 = 依赖顺序。required=false 的模块挂了也不影响主流程。
    // ★ 00-bridge 是现有的整套脚本(原样搬过来, 一个字没改), 单独隔离。
    var BUILTIN = [
        { name: 'bridge', file: 'modules/00-bridge.js', required: true },
        { name: 'health', file: 'modules/90-health.js', required: false }
    ];

    // ---------------------------------------------------------- 小工具
    function note(s) {
        try { EXT.log.push(String(s)); } catch (e) { }
    }

    function assetURL(file) {
        return file;   // 真实地址由隔离世界的 provider 用 chrome.runtime.getURL 算
    }

    // ★ 从隔离世界要源码: MAIN 世界拿不到 chrome.runtime(实测 undefined),
    //   所以这里只发个"我要这个文件", 由 provider.js 取回来 postMessage 给咱们。
    var pending = {};
    var providerReady = false;

    window.addEventListener('message', function (e) {
        var d = e.data;
        if (!d || typeof d !== 'object' || !d.__sgsExt) return;
        if (d.__sgsExt === 'ready') {
            providerReady = true;
            note('provider 就位');
            return;
        }
        if (d.__sgsExt !== 'src') return;
        var cb = pending[d.file];
        if (!cb) return;
        delete pending[d.file];
        cb(d.src ? { ok: true, src: d.src } : { ok: false, err: d.err || '取不到源码' });
    });

    function askSrc(file) {
        return new Promise(function (resolve) {
            pending[file] = resolve;
            var tries = 0;
            var send = function () {
                window.postMessage({ __sgsExt: 'need', file: file }, '*');
                if (++tries < 40) {
                    setTimeout(function () { if (pending[file]) send(); }, 150);
                } else {
                    delete pending[file];
                    resolve({ ok: false, err: 'provider 没回话(扩展没装好?)' });
                }
            };
            send();
        });
    }

    // 执行一段源码: 用 new Function 保证它在【函数作用域】里跑,
    // 顶层 const/let 不会污染全局, 但 window / document / Laya 照样能拿到。
    function runSource(src, name) {
        try {
            var fn = new Function(src);
            fn();
            return { ok: true };
        } catch (e) {
            return { ok: false, err: String((e && e.message) || e) };
        }
    }

    // 模块自己抛的错(不是加载失败)得靠全局 error 抓 —— 串行加载, 出事的就是当前这个
    var current = null;
    window.addEventListener('error', function (e) {
        if (!current) return;
        var msg = String((e && e.message) || 'error');
        current.ok = false;
        current.err = msg;
        EXT.errors.push({ module: current.name, err: msg });
        note('模块 ' + current.name + ' 运行时抛错: ' + msg);
    });

    function finishModule(m, res, t0) {
        m.ms = Date.now() - t0;
        if (res.ok && m.ok !== false) {
            m.ok = true;
            note('模块 ' + m.name + ' OK (' + m.ms + 'ms)');
        } else {
            m.ok = false;
            m.err = m.err || res.err || '未知';
            if (!EXT.errors.some(function (x) { return x.module === m.name; })) {
                EXT.errors.push({ module: m.name, err: m.err });
            }
            note('模块 ' + m.name + ' 崩: ' + m.err);
        }
        // ★ v4.140 修: 以前这里从没把模块登记进 EXT.modules, 于是角标永远显示
        //   "模块 0/0" —— 看着像没装, 实际上装了; 排查方向全被带偏。
        if (EXT.modules.indexOf(m) < 0) EXT.modules.push(m);
        current = null;
        return m;
    }

    function loadModule(m) {
        var t0 = Date.now();
        current = m;
        return askSrc(m.file).then(function (got) {
            // 取到了就执行; 执行时的抛错由上面那个 window error 监听记账
            var res = got.ok ? runSource(got.src, m.name) : got;
            return finishModule(m, res, t0);
        });
    }

    // 只执行(源码已经取回来了) —— 给"并行取、按序跑"那条路用
    function runModule(m) {
        var t0 = Date.now();
        if (!m._got) return finishModule(m, { ok: false, err: '源码没取到' }, t0);
        current = m;
        var res = m._got.ok ? runSource(m._got.src, m.name) : m._got;
        return finishModule(m, res, t0);
    }

    // ---------------------------------------------------------- 额外脚本
    // 你自己加的脚本: 两种来源, 都不用改本文件
    //   ① localStorage['sgsExtraScripts'] = JSON 数组 [{name, url}]
    //   ② 助手端(本机端口)的 /ext_scripts 下发 —— 助手界面上加的脚本自动到这儿
    function extraList() {
        var out = [];
        try {
            var raw = localStorage.getItem('sgsExtraScripts') || '';
            if (raw) {
                var a = JSON.parse(raw);
                if (Array.isArray(a)) {
                    for (var i = 0; i < a.length; i++) {
                        if (a[i] && a[i].url) {
                            out.push({ name: a[i].name || ('extra' + i), url: a[i].url, from: 'local' });
                        }
                    }
                }
            }
        } catch (e) { }
        try {
            var port = localStorage.getItem('sgsExtPort') || '';
            if (port) out.__port = port;
        } catch (e) { }
        return out;
    }

    function loadExtra() {
        var list = extraList();
        var port = list.__port;
        var jobs = [];

        list.forEach(function (x) {
            jobs.push(
                fetch(x.url, { cache: 'no-store' })
                    .then(function (r) { return r.text(); })
                    .then(function (src) {
                        var res = runSource(src, x.name);
                        var rec = { name: x.name, url: x.url, from: x.from, ok: res.ok, err: res.err || '' };
                        EXT.extra.push(rec);
                        if (!res.ok) EXT.errors.push({ module: 'extra:' + x.name, err: res.err });
                        note('额外脚本 ' + x.name + (res.ok ? ' OK' : ' 崩: ' + res.err));
                    })
                    .catch(function (e) {
                        EXT.extra.push({ name: x.name, url: x.url, from: x.from, ok: false, err: String(e) });
                        EXT.errors.push({ module: 'extra:' + x.name, err: String(e) });
                    })
            );
        });

        // 助手端下发的(端口存 localStorage, 没有就跳过)
        if (port) {
            jobs.push(
                fetch('http://127.0.0.1:' + port + '/ext_scripts', { cache: 'no-store' })
                    .then(function (r) { return r.json(); })
                    .then(function (j) {
                        var arr = (j && j.scripts) || [];
                        var sub = [];
                        arr.forEach(function (x) {
                            if (!x || !x.url) return;
                            sub.push(
                                fetch(x.url, { cache: 'no-store' })
                                    .then(function (r2) { return r2.text(); })
                                    .then(function (src) {
                                        var res = runSource(src, x.name);
                                        EXT.extra.push({ name: x.name, url: x.url, from: 'helper', ok: res.ok, err: res.err || '' });
                                        if (!res.ok) EXT.errors.push({ module: 'helper:' + x.name, err: res.err });
                                    })
                                    .catch(function (e) {
                                        EXT.errors.push({ module: 'helper:' + x.name, err: String(e) });
                                    })
                            );
                        });
                        return Promise.all(sub);
                    })
                    .catch(function () { /* 助手没开: 正常, 不记错 */ })
            );
        }
        return Promise.all(jobs);
    }

    // ---------------------------------------------------------- 角标
    function paint() {
        try {
            var host = document.body || document.documentElement;
            if (!host) { setTimeout(paint, 300); return; }
            var b = document.createElement('div');
            b.id = 'sgsExtBadge';
            b.style.cssText = 'position:fixed;right:4px;bottom:4px;z-index:2147483646;' +
                'font:11px monospace;padding:2px 7px;border-radius:4px;background:#000a;' +
                'color:#0f0;max-width:60vw;white-space:pre-wrap;pointer-events:none;';
            var bad = EXT.errors.length;
            var txt = '扩展 v' + VER + ' 模块 ' +
                EXT.modules.filter(function (m) { return m.ok; }).length + '/' + EXT.modules.length;
            if (EXT.extra.length) txt += ' + 额外' + EXT.extra.length;
            if (bad) {
                txt += '\n[红] ' + EXT.errors.map(function (e) { return e.module; }).join(', ');
                b.style.color = '#f66';
            }
            b.textContent = txt;
            host.appendChild(b);
        } catch (e) { }
    }

    // ---------------------------------------------------------- 启动
    // ★ v4.140 提速: 原来是"取一个 → 跑一个 → 再取下一个"的串行, 每个模块都要等一次
    //   跨世界 postMessage 往返(实测每个 20~60ms, 大模块更久)。现在改成
    //   【并行取源码 + 按清单顺序执行】—— 等待叠在一起, 执行顺序仍是清单顺序。
    function boot() {
        Promise.all(BUILTIN.map(function (m) {
            return askSrc(m.file).then(function (got) { m._got = got; return m; });
        })).then(function () {
            var chain = Promise.resolve();
            BUILTIN.forEach(function (m) {
                chain = chain.then(function () { return runModule(m); });
            });
            return chain;
        }).then(loadExtra)
            .then(paint)
            .catch(function (e) { note('boot 异常: ' + e); paint(); });
    }

    // 对外: 让别的脚本也能挂模块
    EXT.load = function (name, src) {
        var res = runSource(src, name);
        EXT.modules.push({ name: name, ok: res.ok, err: res.err || '', file: '(inline)' });
        if (!res.ok) EXT.errors.push({ module: name, err: res.err });
        paint();
        return res;
    };
    EXT.dump = function () {
        return JSON.stringify({
            ver: VER,
            modules: EXT.modules.map(function (m) { return { name: m.name, ok: m.ok, err: m.err, ms: m.ms }; }),
            extra: EXT.extra,
            errors: EXT.errors,
            log: EXT.log.slice(-30)
        }, null, 1);
    };

    boot();
})();

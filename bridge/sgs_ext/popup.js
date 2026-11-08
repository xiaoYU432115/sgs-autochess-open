// 全能扩展 · 弹窗逻辑
//   只做三件事：显示真实状态（不编数字）、改端口、暂停/启用 + 跳管理页。
//   所有数字都来自 service worker 的 status()，其中"生效规则"是向浏览器【实查】的。
(function () {
    'use strict';

    var $ = function (id) { return document.getElementById(id); };
    var lastPort = null;

    function send(msg) {
        return new Promise(function (resolve) {
            chrome.runtime.sendMessage(msg, function (res) {
                var e = chrome.runtime.lastError;
                if (e) resolve({ ok: false, error: e.message });
                else resolve(res || { ok: false, error: '没有回包' });
            });
        });
    }

    function hostUrl(port, path) {
        return 'http://127.0.0.1:' + (parseInt(port, 10) || '') + path;
    }

    function fmtTime(ms) {
        if (!ms) return '—';
        try {
            var d = new Date(ms);
            var p = function (n) { return (n < 10 ? '0' : '') + n; };
            return p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
        } catch (e) { return '—'; }
    }

    function fmtSkipped(s) {
        if (!s) return '无';
        var parts = [];
        if (s.no_url) parts.push('目标不是 http 地址 ' + s.no_url);
        if (s.dup_pattern) parts.push('重复匹配串 ' + s.dup_pattern);
        if (s.over_limit) parts.push('超出上限 ' + s.over_limit);
        if (s.bad_regex) parts.push('正则无效 ' + s.bad_regex);
        return parts.length ? parts.join('，') : '无';
    }

    function render(st) {
        var el = $('state');
        el.className = 'state';
        if (!st || !st.ok) {
            el.classList.add('bad');
            el.textContent = '读不到扩展状态' + (st && st.error ? '：' + st.error : '');
            return;
        }
        var stat = st.stat || {};
        var live = st.live || { count: 0 };
        var persisted = stat.count || 0;
        var nMan = 0;
        (st.manualEntries || []).forEach(function (e) {
            if (e && e.pattern && e.target && e.enabled !== false) nMan++;
        });

        if (!st.enabled) {
            el.classList.add('off');
            el.textContent = '已暂停 —— 规则已撤下，游戏用回官方原图';
        } else if (stat.degraded && stat.error) {
            el.classList.add('warn');
            el.textContent = '降级中：助手连不上，生效的是手动规则(' + live.count + ' 条)';
        } else if (live.count > 0) {
            el.classList.add('on');
            el.textContent = '生效中：' + live.count + ' 条重定向规则';
        } else {
            el.classList.add('off');
            el.textContent = '待同步 —— 还没有规则，点「立即同步」或先打开助手';
        }

        lastPort = st.port;
        $('port').value = st.port;
        $('rulesUrl').textContent = '规则来源：' + (st.rulesUrl || '—');
        $('count').textContent = live.count + ' 条' +
            (persisted && persisted !== live.count ? '（上次提交 ' + persisted + ' 条，对不上）' : '');
        $('manual').textContent = nMan + ' 条';
        $('rev').textContent = (stat.season ? stat.season + ' · ' : '') + (stat.rev || '—');
        $('at').textContent = fmtTime(stat.at);
        $('skipped').textContent = fmtSkipped(stat.skipped);
        // B3: 脚本注入状态 —— 主脚本注册上了没 + 几条用户脚本（总开关关了就显示已停）
        var inj = $('inject');
        if (inj) {
            inj.textContent = st.scriptsEnabled === false
                ? '已停（脚本总开关关）'
                : '主脚本' + ((st.bridge && st.bridge.registered) ? ' ✓' : ' ✗')
                  + ' + ' + (st.injectedUser || 0) + ' 条用户脚本';
        }
        $('err').hidden = !stat.error;
        $('err').textContent = stat.error || '';
        $('toggle').textContent = st.enabled ? '暂停' : '启用';
        // ★ 脚本总开关: 跟上面那个「暂停」是两回事(那个只撤重定向规则)
        var on = st.scriptsEnabled !== false;
        var ts = $('toggleScripts');
        if (ts) {
            ts.textContent = on ? '停脚本' : '启脚本';
            ts.title = on ? '撤销所有注入到游戏页的脚本(含主脚本)'
                          : '重新注入脚本(游戏页要刷新一次才生效)';
        }
        var sh = $('scriptsHint');
        if (sh) {
            sh.textContent = on
                ? '脚本注入：开（含主脚本/桥接）。旁边的「暂停」只管重定向规则，不停脚本。'
                : '脚本注入：已停。当前打开的游戏页要刷新一次才彻底停 —— 助手现在读不到游戏数据。';
        }
    }

    async function refresh() {
        render(await send({ type: 'status' }));
    }

    async function act(msg) {
        var r = await send(msg);
        await refresh();
        if (r && r.ok === false && r.error) {
            var el = $('err');
            el.hidden = false;
            el.textContent = r.error;
        }
    }

    $('sync').addEventListener('click', function () { act({ type: 'sync' }); });

    $('toggle').addEventListener('click', function () {
        // 用当前真实状态取反，不依赖按钮文字（文字可能是旧的）
        send({ type: 'status' }).then(function (st) {
            act({ type: 'setEnabled', enabled: !(st && st.enabled) });
        });
    });

    var _tsEl = $('toggleScripts');
    if (_tsEl) _tsEl.addEventListener('click', function () {
        send({ type: 'status' }).then(function (st) {
            act({ type: 'setSetting', key: 'scriptsEnabled',
                  value: !((st && st.scriptsEnabled) !== false) });
        });
    });

    // 刷新游戏页：停用脚本/主脚本后手动点一下，让"停止"立刻在当前游戏页生效
    //   （userScripts.unregister 只挡下次注入，已跑起来的要刷新才撤）。不自动触发。
    var _rgEl = $('reloadGame');
    if (_rgEl) _rgEl.addEventListener('click', async function () {
        var r = await send({ type: 'reloadGame' });
        if (r && r.ok) {
            var ok = $('err'); ok.hidden = true;
            if (window.close) window.close();     // 刷新已触发，收起弹窗让用户看到游戏页
        } else {
            var el = $('err');
            el.hidden = false;
            el.textContent = (r && r.error) || '刷新失败';
        }
    });

    $('savePort').addEventListener('click', function () {
        var v = parseInt($('port').value, 10);
        if (isNaN(v) || v < 1 || v > 65535) {
            var el = $('err');
            el.hidden = false;
            el.textContent = '端口要在 1~65535 之间';
            return;
        }
        act({ type: 'setPort', port: v });
    });

    $('port').addEventListener('keydown', function (ev) {
        if (ev.key === 'Enter') $('savePort').click();
    });

    $('openOptions').addEventListener('click', function () {
        chrome.runtime.openOptionsPage();
    });
    $('openRes').addEventListener('click', function () {
        chrome.tabs.create({ url: hostUrl(lastPort, '/res') });
    });
    $('openInstall').addEventListener('click', function () {
        chrome.tabs.create({ url: hostUrl(lastPort, '/install') });
    });

    refresh();
})();

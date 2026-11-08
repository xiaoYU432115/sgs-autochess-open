// 全能扩展 · 页面侧内容脚本（isolated world）
//
// 三件事：
//   ① 在 <html> 上打一个标记 data-sgs-res-ext=<版本>。油猴脚本(page/bridge.main.js 里的
//      installResSwap)会读它 —— 看到扩展在场就让页面层那套请求改写 hook 退到一边
//      （扩展在网络层覆盖更全，两层同开会做重复功）。DOM 属性是跨世界可见的。
//   ② 定期催 service worker 同步规则。MV3 的 service worker 闲下来会被回收，
//      没有页面这一催，规则更新最快也要等到下一次 alarm（每分钟）。
//   ③ 打上"页面注入"的存在标记（page/bridge.main.js 自己会挂 window.__sgsExtPage，
//      那个在主世界；本脚本这个在隔离世界 —— 管理页「检查游戏页」两个都看）。
//
// ⚠ 本脚本不读、不改任何游戏数据，只负责"报个到 + 催一下"。
(function () {
    'use strict';

    var MARK = 'data-sgs-res-ext';
    var MARK2 = 'data-sgs-ext-all';            // 全能版自己的标记（和轻量版区分得开）
    var SEEK_INTERVAL_MS = 20000;

    function stamp() {
        try {
            var el = document.documentElement;
            if (!el) return false;
            el.setAttribute(MARK, chrome.runtime.getManifest().version);
            el.setAttribute(MARK2, '1');
            return true;
        } catch (e) {
            return false;
        }
    }

    // document_start 时 documentElement 一般已经在了；万一没有，短重试几次就够
    if (!stamp()) {
        var tries = 0;
        var t = setInterval(function () {
            tries++;
            if (stamp() || tries > 40) clearInterval(t);
        }, 25);
    }

    function nudge(reason) {
        try {
            chrome.runtime.sendMessage({ type: 'sync', reason: reason }, function () {
                void chrome.runtime.lastError;      // 扩展刚重载时可能没人接，正常
            });
        } catch (e) { /* 同上 */ }
    }

    nudge('page');
    setInterval(function () {
        if (document.visibilityState === 'visible') nudge('tick');
    }, SEEK_INTERVAL_MS);
    document.addEventListener('visibilitychange', function () {
        if (document.visibilityState === 'visible') nudge('visible');
    });

    // v4.153★ PNA 绕过中继: 主世界(page/bridge.main.js, world:MAIN)够不到 chrome.runtime,
    //   由本 isolated-world 脚本代发 chrome.runtime.sendMessage -> background 的 sgsFetch 分支。
    //   DOM 事件在 isolated/main 两世界共通(同一个 window), 这是标准跨世界中继。
    window.addEventListener('__sgsFetchReq', function (e) {
        var d = (e && e.detail) || {};
        var id = d.id;
        if (id == null) return;
        function back(resp, err) {
            try {
                window.dispatchEvent(new CustomEvent('__sgsFetchRes', {
                    detail: { id: id, resp: resp, err: err || null }
                }));
            } catch (ex) { /* 主世界可能已卸载, 忽略 */ }
        }
        try {
            chrome.runtime.sendMessage({
                type: 'sgsFetch', method: d.method || 'GET', url: d.url,
                headers: d.headers || {}, body: (d.body != null ? d.body : null)
            }, function (resp) {
                var le = chrome.runtime.lastError;
                back(le ? null : resp, le ? le.message : null);
            });
        } catch (ex) {
            back(null, String((ex && ex.message) || ex));
        }
    });
})();

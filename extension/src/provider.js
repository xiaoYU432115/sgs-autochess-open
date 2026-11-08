/* 资源供应器 —— 跑在【隔离世界 ISOLATED】, 唯一目的是把扩展包里的模块源码递给主世界。

   为什么要分两个世界:
     · 主世界(MAIN)才能碰到游戏的 Laya / 全局对象 —— 但那里【没有 chrome.runtime】
       (实测 typeof chrome.runtime === 'undefined'), 取不到扩展包里的文件;
       当初直接在 MAIN 里拼 src, 结果变成 https://web.sanguosha.com/login/modules/00-bridge.js
       → 404 返回 HTML → "Unexpected token '<'"。
     · 隔离世界有完整的 chrome.* API, 但碰不到页面对象。
   所以: 隔离世界负责【取】, 主世界负责【执行】, 中间用 window.postMessage 传源码。
*/
(function () {
    function getURL(f) {
        try { return chrome.runtime.getURL(f); } catch (e) { return null; }
    }

    window.addEventListener('message', function (e) {
        var d = e.data;
        if (!d || d.__sgsExt !== 'need') return;
        var file = d.file;
        var u = getURL(file);
        if (!u) {
            window.postMessage({ __sgsExt: 'src', file: file, err: '取不到 chrome.runtime' }, '*');
            return;
        }
        fetch(u, { cache: 'no-store' })
            .then(function (r) {
                if (!r.ok) throw new Error('HTTP ' + r.status);
                return r.text();
            })
            .then(function (src) {
                window.postMessage({ __sgsExt: 'src', file: file, src: src }, '*');
            })
            .catch(function (err) {
                window.postMessage({ __sgsExt: 'src', file: file, err: String(err) }, '*');
            });
    });

    // 告诉主世界"我好了" —— 多喊几次, 免得主世界还没挂上监听
    var n = 0;
    var iv = setInterval(function () {
        window.postMessage({ __sgsExt: 'ready' }, '*');
        if (++n > 40) clearInterval(iv);
    }, 100);
})();

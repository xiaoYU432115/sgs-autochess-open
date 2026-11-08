/* 模块体检 —— 每 20 秒看一眼各模块的关键全局还在不在。

   为什么单独一块: 加载器只知道"当时有没有抛错", 管不了"跑着跑着挂了"
   (典型: 场景重建把节点换了、某个全局被覆盖)。这块盯的是【运行中的健康度】,
   发现问题直接写角标, 并且都塞进 window.__SGSEXT.health 里, 助手端能读。
*/
(function () {
    var EXT = window.__SGSEXT;
    if (!EXT) return;

    // 要盯的东西: [显示名, 取值的表达式]
    var WATCH = [
        ['桥接', function () { return typeof window.__sgs === 'object'; }],
        ['资源替换', function () { return typeof window.__sgsResSwap === 'object'; }],
        ['动态背景', function () { return typeof window.__sgsDynBg === 'object'; }],
        ['动态皮肤', function () { return typeof window.__sgsDynSk === 'object'; }],
        ['快捷键', function () { return typeof window.__sgsHotkey === 'object'; }],
        ['画布', function () { return !!document.querySelector('canvas'); }]
    ];

    EXT.health = {};

    function check() {
        var bad = [];
        for (var i = 0; i < WATCH.length; i++) {
            var nm = WATCH[i][0], ok = false;
            try { ok = !!WATCH[i][1](); } catch (e) { ok = false; }
            EXT.health[nm] = ok;
            if (!ok) bad.push(nm);
        }
        EXT.health.bad = bad;
        EXT.health.at = Date.now();
        return bad;
    }

    function paintHealth() {
        try {
            var b = document.getElementById('sgsExtBadge');
            var bad = EXT.health.bad || [];
            if (!b) return;
            var line = b.textContent.split('\n')[0];
            b.textContent = bad.length ? (line + '\n[不在] ' + bad.join(', ')) : line;
            b.style.color = bad.length ? '#fa0' : '#0f0';
        } catch (e) { }
    }

    setTimeout(function () {
        check();
        paintHealth();
        // 20 秒一次 —— 太勤没意义(模块不会秒挂), 太懒发现问题晚
        try { window.__sgsSetInterval ? window.__sgsSetInterval(tick, 20000) : setInterval(tick, 20000); } catch (e) { setInterval(tick, 20000); }
    }, 4000);

    function tick() { check(); paintHealth(); }

    EXT.checkNow = function () { return check(); };
})();

// 全能扩展 · 管理页逻辑
//   三个数据来源，页面上都要标清"这条是谁的"：
//     · 助手下发(/rules) —— 只读展示，改要去助手的「资源替换」页；
//     · 手写规则(chrome.storage) —— 可增删改启停，优先于助手；
//     · 官方立绘(/skins) —— 挑完带去「规则」页当手写规则的模板。
//   所有"合不合法"的判定都走 lib/manual.js（和 service worker 用同一份，别在这里再写一套）。
(function () {
    'use strict';

    var M = window.SGSManual;
    var SC = window.SGSExtScripts;
    var CS = window.SGSCodeStore;      // v4.140★ 大对象分片存储(和 service worker 用同一份)
    var $ = function (id) { return document.getElementById(id); };

    var st = {
        port: null,
        enabled: true,
        stat: {},
        manualEntries: [],       // 手写规则(原始条目)
        serverRules: [],         // 助手下发的规则(展示用)
        serverErr: '',
        skins: [],               // 官方立绘清单
        skinsLoaded: false,
        editIdx: -1,             // -1 = 新增；>=0 = 改第几条手写规则
        mode: 'local',
        scripts: [],             // 脚本清单(元信息, 无代码)
        scriptsLoaded: false,
        seId: null,              // 脚本编辑器正在改的 id(null=新增)
        market: []               // 助手 /scripts.json 的清单
    };

    // ---------------------------------------------------------------- 小工具
    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }

    function send(msg) {
        return new Promise(function (resolve) {
            chrome.runtime.sendMessage(msg, function (res) {
                var e = chrome.runtime.lastError;
                if (e) resolve({ ok: false, error: e.message });
                else resolve(res || { ok: false, error: '没有回包' });
            });
        });
    }

    function localGet(keys) {
        return new Promise(function (res) {
            chrome.storage.local.get(keys, function (o) { res(o || {}); });
        });
    }
    function localSet(obj) {
        return new Promise(function (res, rej) {
            chrome.storage.local.set(obj, function () {
                var e = chrome.runtime.lastError;
                if (e) rej(new Error(e.message)); else res();
            });
        });
    }
    // 只把一个键(需要时连它的分片)读出来，不 localGet(null) —— 后者会顺带把主脚本
    //   缓存那 1MB 多也捞出来，管理页一开就卡一下。
    // 同 background.js: 顺带探 key#0, 探到孤儿片就全量读一次把片捞回来,
    //   否则管理页也会"看不见"那 7MB 的脚本。
    async function readStoreKey(key) {
        var head = await localGet([key, key + CS.SEP + '0']);
        var v = head[key];
        var isDir = !!(v && typeof v === 'object' && !Array.isArray(v) && v[CS.MARK]);
        if (isDir) {
            var n = parseInt(v[CS.MARK], 10) || 0;
            var keys = [key];
            for (var i = 0; i < n; i++) keys.push(key + CS.SEP + i);
            return await localGet(keys);
        }
        if (typeof head[key + CS.SEP + '0'] === 'string') return await localGet(null);
        return head;
    }

    // 脚本(含代码)全部走 chrome.storage.local：绕开 sendMessage 4.5MB 单消息上限，
    // 大脚本也能存、也能回看/导出。增删改后只发一个轻量 {type:'syncScripts'} 让后台重注册。
    //
    // v4.140★ 再绕一层 storage.local 的【单项 5MB】上限：脚本多了以后这一个键就会超，
    //   超了的表现是"点保存没反应"。所以读写都走 codestore 的分片(>1MB 自动切)。
    //   读必须 localGet(null) 取全部 —— 只取 ['scripts'] 是取不到片的。
    async function readScripts() {
        var o = await readStoreKey('scripts');
        // ★ 守卫要用 CS.readFrom(codestore 的方法)，不是 SC.readFrom(SGSExtScripts 没这个方法)：
        //   以前写成 SC.readFrom 永远为假 → 分片库(加了锦囊袋这种大脚本就会分片)被读成
        //   目录对象 {__chunks,__bytes} 而不是数组 → 退回种子 → 停止/启用/删/编辑全报"找不到这个脚本"。
        var arr = (CS.readFrom) ? CS.readFrom(o, 'scripts') : o.scripts;
        if (arr && arr.__sgsChunkError) {
            toast('脚本分片损坏(' + arr.__sgsChunkError + ')，已按空列表处理', true);
            arr = null;
        }
        if (!Array.isArray(arr) || !arr.length) arr = SC.seedScripts(arr);
        return arr;
    }
    async function writeScripts(arr) {
        var plan = CS.planWrite('scripts', arr);
        if (!plan.ok) { toast('保存失败：' + plan.error, true); return { ok: false, error: plan.error }; }
        // 同 background.js: 清理不依赖目录片数, 直接把多余片全列出来删(漏删=孤儿片)
        var del = CS.staleChunks('scripts', plan.chunks);
        if (del.length) {
            await new Promise(function (res) { chrome.storage.local.remove(del, function () { res(); }); });
        }
        await localSet(plan.set);
        var r = await send({ type: 'syncScripts' });
        return r || {};
    }
    function findScript(arr, id) {
        for (var i = 0; i < arr.length; i++) if (arr[i].id === id) return arr[i];
        return null;
    }

    // N2 自动备份到助手：把 设置 + 手动规则(+可选脚本代码) POST 到助手 /ext/backup，轮转保存。
    //   治"脚本/规则凭空丢失"(v4.140 真发生过)。助手没开就静默跳过(备份是保险, 不挡主流程)。
    async function postBackup(payload) {
        try {
            var base = M.baseUrlFromPort(st.port);
            if (!base) return { ok: false, error: '端口没设好' };
            var r = await fetch(base + '/ext/backup', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            var j = await r.json().catch(function () { return null; });
            return (j && j.ok) ? { ok: true, file: j.file } : { ok: false, error: (j && j.error) || ('HTTP ' + r.status) };
        } catch (e) { return { ok: false, error: String((e && e.message) || e) }; }
    }
    function _settingsSnap() {
        return localGet(['port', 'enabled', 'autoSync', 'badge', 'scriptsEnabled']).then(function (cfg) {
            return { port: cfg.port, enabled: cfg.enabled !== false, autoSync: cfg.autoSync !== false,
                     badge: cfg.badge !== false, scriptsEnabled: cfg.scriptsEnabled !== false };
        });
    }
    async function backupLight(why) {   // 只 设置+规则(小, 规则每次保存都调)
        try { await postBackup({ type: 'sgs-ext-backup', why: why || '', at: Date.now(),
                 settings: await _settingsSnap(), manual: st.manualEntries || [] }); }
        catch (e) { /* 备份失败不挡主流程 */ }
    }
    async function backupFull(why) {    // 含自添脚本代码(结构变更/手动备份用)
        try { await postBackup({ type: 'sgs-ext-backup', why: why || '', at: Date.now(),
                 settings: await _settingsSnap(), manual: st.manualEntries || [], scripts: await readScripts() }); }
        catch (e) { /* 同上 */ }
    }

    var toastTimer = null;
    function toast(msg, bad) {
        var el = $('toast');
        el.hidden = false;
        el.className = 'toast' + (bad ? ' bad' : '');
        el.textContent = msg;
        clearTimeout(toastTimer);
        toastTimer = setTimeout(function () { el.hidden = true; }, bad ? 6000 : 3200);
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

    function base() { return M.baseUrlFromPort(st.port); }

    // ---------------------------------------------------------------- 标签页
    $('tabs').addEventListener('click', function (ev) {
        var btn = ev.target.closest('.tab');
        if (!btn) return;
        var name = btn.getAttribute('data-tab');
        var tabs = document.querySelectorAll('.tab');
        for (var i = 0; i < tabs.length; i++) {
            tabs[i].classList.toggle('on', tabs[i] === btn);
        }
        var panes = document.querySelectorAll('.pane');
        for (var k = 0; k < panes.length; k++) {
            panes[k].classList.toggle('on', panes[k].id === 'tab-' + name);
        }
        if (name === 'skins' && !st.skinsLoaded) loadSkins();
        else if (name === 'scripts' && !st.scriptsLoaded) refreshScripts();
        else if (name === 'market' && !st.market.length) loadMarket();
        else if (name === 'settings') renderSettings();
    });

    // ---------------------------------------------------------------- 总览
    function renderOverview(s) {
        var el = $('state');
        el.className = 'state';
        var stat = s.stat || {};
        var live = s.live || { count: 0 };

        if (!s.enabled) {
            el.classList.add('off');
            el.textContent = '已暂停 —— 规则已撤下，游戏用回官方原图';
        } else if (stat.degraded && stat.error) {
            el.classList.add('warn');
            el.textContent = '降级中：助手连不上，现在生效的是手动规则(' + live.count + ' 条)';
        } else if (live.count > 0) {
            el.classList.add('on');
            el.textContent = '生效中：' + live.count + ' 条重定向规则';
        } else {
            el.classList.add('off');
            el.textContent = '还没有生效的规则 —— 先连上助手，或去「规则」页手写一条';
        }

        var nMan = M.activeManual(st.manualEntries);
        $('ovCount').textContent = live.count + ' 条' +
            (stat.count && stat.count !== live.count ? '（上次提交 ' + stat.count + ' 条，对不上）' : '');
        $('ovManual').textContent = nMan + ' 条';
        $('ovRev').textContent = (stat.season ? stat.season + ' · ' : '') + (stat.rev || '—');
        $('ovAt').textContent = fmtTime(stat.at);
        $('ovSkipped').textContent = fmtSkipped(stat.skipped);
        $('err').hidden = !stat.error;
        $('err').textContent = stat.error || '';
        $('toggle').textContent = s.enabled ? '暂停' : '启用';

        st.port = s.port;
        $('port').value = s.port;
        $('rulesUrl').textContent = '规则来源：' + (s.rulesUrl || '—') +
            (s.base ? '　·　本机图片前缀：' + s.base + '/img/…' : '');
        $('ver').textContent = 'v' + (s.version || '');
    }

    async function refreshStatus() {
        var s = await send({ type: 'status' });
        if (!s || !s.ok) {
            $('state').className = 'state bad';
            $('state').textContent = '读不到扩展状态' + (s && s.error ? '：' + s.error : '');
            return;
        }
        st.enabled = s.enabled;
        st.stat = s.stat || {};
        st.autoSync = s.autoSync;
        st.badge = s.badge;
        st.manualEntries = Array.isArray(s.manualEntries) ? s.manualEntries : [];
        renderOverview(s);
        renderRules();
    }

    async function act(msg, okMsg) {
        var r = await send(msg);
        await refreshStatus();
        if (r && r.ok === false && r.error) toast(r.error, true);
        else if (okMsg) toast(okMsg);
        return r;
    }

    $('sync').addEventListener('click', function () { act({ type: 'sync' }, '已同步'); });
    $('toggle').addEventListener('click', function () {
        act({ type: 'setEnabled', enabled: !st.enabled });
    });
    $('savePort').addEventListener('click', function () {
        var v = parseInt($('port').value, 10);
        if (isNaN(v) || v < 1 || v > 65535) { toast('端口要在 1~65535 之间', true); return; }
        act({ type: 'setPort', port: v }, '端口已保存');
    });
    $('port').addEventListener('keydown', function (ev) {
        if (ev.key === 'Enter') $('savePort').click();
    });
    $('openRes').addEventListener('click', function () {
        if (!base()) { toast('先连上助手(端口没设好)', true); return; }
        chrome.tabs.create({ url: base() + '/res' });
    });
    $('openInstall').addEventListener('click', function () {
        if (!base()) { toast('先连上助手(端口没设好)', true); return; }
        chrome.tabs.create({ url: base() + '/install' });
    });
    $('gotoRules').addEventListener('click', function () {
        document.querySelector('.tab[data-tab="rules"]').click();
        openEditor(null);
    });

    // 「检查游戏页」：真的去当前标签页探一下脚本注入没有，不猜
    $('checkPage').addEventListener('click', async function () {
        var box = $('pageProbe');
        box.hidden = false;
        box.className = 'probe';
        box.textContent = '正在探当前标签页…';
        var r = await send({ type: 'checkPage' });
        if (!r || !r.ok) {
            box.className = 'probe bad';
            box.textContent = '✗ ' + (r && r.error ? r.error : '探针失败');
            return;
        }
        if (!r.alive) {
            box.className = 'probe bad';
            box.textContent = '✗ 游戏页(' + r.host + ')里没找到脚本 —— 试试刷新游戏页(注入在 document-start，刷新才补得上)';
            return;
        }
        box.className = 'probe';
        box.textContent = '✓ 脚本已在页面里 · 版本 ' + r.ver +
            (r.engine ? ' · 引擎 ' + r.engine : '') +
            (r.swapOn ? ' · 换肤 hook 在(会自己让位给网络层)' : ' · 换肤 hook 还没起来') +
            (r.note ? ' ' + r.note : '');
    });

    // N1 一键诊断：把助手/主脚本/规则/自添脚本/游戏页一次查清，逐行人话报告
    $('diagnoseBtn').addEventListener('click', async function () {
        var box = $('pageProbe');
        box.hidden = false;
        box.className = 'probe';
        box.textContent = '正在诊断…';
        var r = await send({ type: 'diagnose' });
        if (!r || !r.ok) { box.className = 'probe bad'; box.textContent = '✗ 诊断失败'; return; }
        var marks = { ok: '✓', bad: '✗', warn: '!', info: '·' };
        box.textContent = '';
        (r.lines || []).forEach(function (ln) {
            var d = document.createElement('div');
            d.className = 'dline ' + (ln.lv || 'info');
            d.textContent = (marks[ln.lv] || '·') + ' ' + ln.t;
            box.appendChild(d);
        });
    });

    // ---------------------------------------------------------------- 规则页
    function fillKindSelect(sel, withEmpty, extra) {
        var kinds = M.KINDS.slice();
        (extra || []).forEach(function (k) {
            if (k && kinds.indexOf(k) < 0) kinds.push(k);
        });
        sel.textContent = '';
        if (withEmpty) {
            var o = document.createElement('option');
            o.value = '';
            o.textContent = '全部类别';
            sel.appendChild(o);
        }
        kinds.forEach(function (k) {
            var o = document.createElement('option');
            o.value = k;
            o.textContent = k;
            sel.appendChild(o);
        });
        return kinds;
    }

    function mergedRules() {
        return M.mergeRules(st.serverRules, st.manualEntries);
    }

    function renderRules() {
        fillKindSelect($('fKind'), true, mergedRules().map(function (r) { return r.kind; }));
        var list = M.filterRules(mergedRules(), {
            q: $('q').value, source: $('fSource').value, kind: $('fKind').value
        });
        var dup = M.findDupPatterns(st.manualEntries);
        var box = $('lrows');
        box.textContent = '';

        var man = 0, srv = 0;
        mergedRules().forEach(function (r) {
            if (r.source === 'manual' && r.enabled !== false) man++;
            else if (r.source === 'server') srv++;
        });
        $('ruleSummary').textContent = M.summaryText(mergedRules(), st.serverErr ? { error: st.serverErr } : null) +
            ' · 当前筛选 ' + list.length + ' 条';

        if (!list.length) {
            var d = document.createElement('div');
            d.className = 'empty';
            d.textContent = '没有匹配的规则 —— 助手连不上时这里只会剩手写的；先点「刷新」或「＋ 添加规则」';
            box.appendChild(d);
            return;
        }

        list.forEach(function (r) {
            var row = document.createElement('div');
            row.className = 'lrow' + (r.enabled === false ? ' off' : '');

            var c1 = document.createElement('span');
            c1.className = 'pat';
            c1.textContent = r.pattern;
            row.appendChild(c1);

            var c2 = document.createElement('span');
            c2.className = 'tgt';
            c2.textContent = r.url;
            c2.title = r.url;
            row.appendChild(c2);

            var c3 = document.createElement('span');
            c3.textContent = r.kind || '其他';
            row.appendChild(c3);

            var c4 = document.createElement('span');
            var tg = document.createElement('span');
            tg.className = 'tag' + (r.source === 'manual' ? ' manual' : '');
            tg.textContent = r.source === 'manual' ? (r.enabled === false ? '手动·停用' : '手动') : '助手';
            c4.appendChild(tg);
            if (r.source === 'manual' && dup[r.pattern]) {
                var dtag = document.createElement('span');
                dtag.className = 'tag dup';
                dtag.textContent = '重复';
                dtag.title = '同一个匹配串写了两条 —— 只有先出现的那条会生效';
                c4.appendChild(dtag);
            }
            if (r.source === 'manual' && r.regex) {
                var rtag = document.createElement('span');
                rtag.className = 'tag';
                rtag.textContent = '正则';
                rtag.title = '这条按正则匹配(不是字面量包含)';
                c4.appendChild(rtag);
            }
            row.appendChild(c4);

            var c5 = document.createElement('span');
            c5.className = 'ops';
            if (r.source === 'manual') {
                var idx = manualIndexOf(r);
                c5.appendChild(miniBtn('编辑', function () { openEditor(idx); }));
                c5.appendChild(miniBtn(r.enabled === false ? '启用' : '停用', function () {
                    st.manualEntries[idx].enabled = !(st.manualEntries[idx].enabled !== false);
                    saveManual('已' + (st.manualEntries[idx].enabled !== false ? '启用' : '停用'));
                }));
                c5.appendChild(miniBtn('删', function () {
                    st.manualEntries.splice(idx, 1);
                    saveManual('已删除');
                }, 'danger'));
            } else {
                c5.appendChild(miniBtn('转手动', function () {
                    openEditor(null, {
                        pattern: r.pattern, mode: 'url', value: r.url,
                        kind: r.kind || '其他', note: r.name || ''
                    });
                }));
            }
            row.appendChild(c5);
            box.appendChild(row);
        });
    }

    function miniBtn(text, fn, cls) {
        var b = document.createElement('button');
        b.className = 'mini' + (cls ? ' ' + cls : '');
        b.textContent = text;
        b.addEventListener('click', fn);
        return b;
    }

    // 从"合并后的视图"找回手写数组的下标（同一 pattern 手动只会有一条，够准）
    function manualIndexOf(r) {
        for (var i = 0; i < st.manualEntries.length; i++) {
            var e = M.normalizeEntry(st.manualEntries[i]);
            if (e && e.pattern === r.pattern) return i;
        }
        return -1;
    }

    async function loadServerRules() {
        var b = base();
        if (!b) { st.serverRules = []; st.serverErr = '助手端口没设好'; renderRules(); return; }
        try {
            var resp = await fetch(b + '/rules', { cache: 'no-store' });
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            var obj = await resp.json();
            st.serverRules = Array.isArray(obj.rules) ? obj.rules : [];
            st.serverErr = '';
        } catch (e) {
            st.serverRules = [];
            st.serverErr = '取不到助手规则(' + e.message + ') —— 手写规则不受影响';
        }
        renderRules();
    }

    // ---------------- 编辑器 ----------------
    function setMode(mode) {
        st.mode = mode;
        var btns = $('eModes').querySelectorAll('.segbtn');
        for (var i = 0; i < btns.length; i++) {
            btns[i].classList.toggle('on', btns[i].getAttribute('data-mode') === mode);
        }
        $('eValue').placeholder = mode === 'local'
            ? '文件名，如 luxun.png（放在助手的 我的图/ 目录）'
            : '完整的 http(s) 地址';
    }

    $('eModes').addEventListener('click', function (ev) {
        var b = ev.target.closest('.segbtn');
        if (b) setMode(b.getAttribute('data-mode'));
    });

    function openEditor(idx, prefill) {
        st.editIdx = (typeof idx === 'number' && idx >= 0) ? idx : -1;
        var e = st.editIdx >= 0 ? M.normalizeEntry(st.manualEntries[st.editIdx]) : (prefill || null);
        $('editor').hidden = false;
        fillKindSelect($('eKind'), false);
        $('ePattern').value = e ? e.pattern : '';
        $('eNote').value = e ? (e.note || '') : '';
        $('eKind').value = e && e.kind ? e.kind : '其他';
        $('eRegex').checked = !!(e && e.regex);   // N3: 回填正则开关
        $('eHint').textContent = '';

        if (e && e.target) {
            var m = /\/img\/(.+)$/.exec(e.target);
            if (m) {
                setMode('local');
                var f = '';
                try { f = decodeURIComponent(m[1]); } catch (e2) { f = m[1]; }
                $('eValue').value = f;
            } else {
                setMode('url');
                $('eValue').value = e.target;
            }
        } else {
            setMode(prefill && prefill.mode ? prefill.mode : 'local');
            $('eValue').value = prefill ? (prefill.value || '') : '';
        }
        $('ePattern').focus();
    }

    function closeEditor() {
        $('editor').hidden = true;
        st.editIdx = -1;
    }

    $('eCancel').addEventListener('click', closeEditor);

    $('eSave').addEventListener('click', async function () {
        var d = M.validateDraft({
            pattern: $('ePattern').value,
            mode: st.mode,
            value: $('eValue').value,
            base: base(),
            kind: $('eKind').value,
            note: $('eNote').value,
            regex: $('eRegex').checked          // N3: 逐条正则开关
        });
        if (!d.ok) { $('eHint').className = 'hint warn'; $('eHint').textContent = d.error; return; }
        var entry = d.entry;
        if (st.editIdx >= 0) st.manualEntries[st.editIdx] = entry;
        else {
            // 同 pattern 已有手写规则 → 顶掉它（不然列表里出现两条只有一条生效）
            for (var i = 0; i < st.manualEntries.length; i++) {
                var e = M.normalizeEntry(st.manualEntries[i]);
                if (e && e.pattern === entry.pattern) {
                    st.manualEntries.splice(i, 1);
                    toast('同匹配串的旧规则被顶掉了');
                    break;
                }
            }
            st.manualEntries.push(entry);
        }
        await saveManual('已保存' + (d.hint ? '（' + d.hint + '）' : ''));
        closeEditor();
    });

    // B4: 命中测试 —— 拿当前匹配串 + 用户粘的 URL，按 DNR 同款字面量包含匹配判一下
    $('eTestBtn').addEventListener('click', function () {
        var r = M.testPatternHit($('ePattern').value, $('eTestUrl').value, { regex: $('eRegex').checked });
        var out = $('eTestOut');
        if (!r.ok) { out.className = 'hint warn'; out.textContent = r.error; return; }
        out.className = 'hint' + (r.hit ? '' : ' warn');
        var msg = r.hit ? '✓ 会命中' : '✗ 不命中';
        if (r.note) msg += ' —— ' + r.note;
        if (r.warn) msg += '（' + r.warn + '）';
        out.textContent = msg;
    });

    async function saveManual(okMsg) {
        try {
            await new Promise(function (resolve, reject) {
                chrome.storage.local.set({ manual: st.manualEntries }, function () {
                    var e = chrome.runtime.lastError;
                    if (e) reject(new Error(e.message)); else resolve();
                });
            });
        } catch (e) {
            toast('存不进去：' + e.message, true);
            return;
        }
        var r = await send({ type: 'manualChanged' });
        await refreshStatus();
        if (r && r.ok) toast(okMsg + ' · 已下发 ' + (r.count || 0) + ' 条');
        else toast(okMsg + (r && r.error ? '（下发失败：' + r.error + '）' : ''), !!(r && r.error));
        backupLight('manual');   // N2: 规则/设置变更 → 顺手备份到助手
    }

    $('addRule').addEventListener('click', function () { openEditor(null); });
    $('refreshRules').addEventListener('click', function () { loadServerRules(); });
    $('q').addEventListener('input', renderRules);
    $('fSource').addEventListener('change', renderRules);
    $('fKind').addEventListener('change', renderRules);

    // ---------------- 导入 / 导出 ----------------
    $('exportRules').addEventListener('click', function () {
        var text = M.exportJson(st.manualEntries);
        var n = JSON.parse(text).count;
        if (!n) { toast('没有可导出的手动规则', true); return; }
        var url = URL.createObjectURL(new Blob([text], { type: 'application/json' }));
        var a = document.createElement('a');
        a.href = url;
        a.download = 'sgs-ext-manual-rules.json';
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(function () { URL.revokeObjectURL(url); }, 4000);
        toast('已导出 ' + n + ' 条');
    });

    $('importRules').addEventListener('click', function () { $('importFile').click(); });
    $('importFile').addEventListener('change', function (ev) {
        var f = ev.target.files && ev.target.files[0];
        ev.target.value = '';
        if (!f) return;
        var fr = new FileReader();
        fr.onload = function () {
            var r = M.importJson(fr.result);
            if (!r.ok) { toast(r.error, true); return; }
            var have = {};
            st.manualEntries.forEach(function (e) {
                var n = M.normalizeEntry(e);
                if (n) have[n.pattern] = true;
            });
            var added = 0, dupCnt = 0;
            r.entries.forEach(function (e) {
                if (have[e.pattern]) { dupCnt++; return; }
                have[e.pattern] = true;
                st.manualEntries.push(e);
                added++;
            });
            saveManual('导入 ' + added + ' 条' +
                (dupCnt ? '，跳过重复 ' + dupCnt + ' 条' : '') +
                (r.skipped ? '，格式不对 ' + r.skipped + ' 条' : ''));
        };
        fr.readAsText(f, 'utf-8');
    });

    // ---------------------------------------------------------------- 官方立绘
    async function loadSkins() {
        var b = base();
        var box = $('sGrid');
        if (!b) {
            box.textContent = '';
            var d = document.createElement('div');
            d.className = 'empty';
            d.textContent = '先在「总览」里连上助手（端口没设好）';
            box.appendChild(d);
            $('sCount').textContent = '';
            return;
        }
        box.textContent = '';
        var wait = document.createElement('div');
        wait.className = 'empty';
        wait.textContent = '正在取官方立绘清单…';
        box.appendChild(wait);
        try {
            var resp = await fetch(b + '/skins', { cache: 'no-store' });
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            var obj = await resp.json();
            st.skins = Array.isArray(obj.skins) ? obj.skins : [];
            st.skinsLoaded = true;
        } catch (e) {
            st.skins = [];
            box.textContent = '';
            var d2 = document.createElement('div');
            d2.className = 'empty';
            d2.textContent = '取不到清单(' + e.message + ') —— 助手没开或版本太旧(需要 v4.71+)';
            box.appendChild(d2);
            $('sCount').textContent = '';
            return;
        }
        renderSkins();
    }

    function renderSkins() {
        var box = $('sGrid');
        box.textContent = '';
        var list = M.searchSkins(st.skins, { q: $('sQ').value, kind: $('sKind').value, limit: 240 });
        fillKindSelect($('sKind'), true, st.skins.map(function (s) { return s.kind; }));
        $('sCount').textContent = st.skins.length ? ('共 ' + st.skins.length + ' 套 · 当前显示 ' + list.length + ' 套') : '';
        if (!list.length) {
            var d = document.createElement('div');
            d.className = 'empty';
            d.textContent = st.skins.length ? '没有匹配的立绘' : '清单是空的 —— 点「刷新」';
            box.appendChild(d);
            return;
        }
        list.forEach(function (s) {
            var card = document.createElement('div');
            card.className = 'card';

            var nm = document.createElement('div');
            nm.className = 'nm';
            nm.innerHTML = '<b>' + esc(s.name) + '</b> <span class="tag">' + esc(s.kind) + '</span>' +
                (s.current ? ' <span class="tag cur">本赛季在用</span>' : '');
            card.appendChild(nm);

            var sk = document.createElement('div');
            sk.className = 'sk';
            sk.textContent = s.skin + '　→　' + s.url;
            sk.title = s.url;
            card.appendChild(sk);

            var ft = document.createElement('div');
            ft.className = 'ft';
            ft.appendChild(miniBtn('用这条', function () {
                var d = M.skinToDraft(s, {});
                if (!d.ok) { toast(d.error, true); return; }
                document.querySelector('.tab[data-tab="rules"]').click();
                openEditor(null, d.draft);
                toast('已带好目标地址 —— 确认匹配串后点「保存并生效」');
            }, 'primary'));
            ft.appendChild(miniBtn('复制地址', function () {
                navigator.clipboard.writeText(s.url).then(function () { toast('已复制'); },
                    function () { toast('复制失败', true); });
            }));
            card.appendChild(ft);
            box.appendChild(card);
        });
    }

    $('sQ').addEventListener('input', renderSkins);
    $('sKind').addEventListener('change', renderSkins);
    $('refreshSkins').addEventListener('click', loadSkins);

    // ================================================================ 脚本管理
    // v4.81★ 主脚本【不内置】了 —— 它由助手 /ext/bridge.main.js 下发、用 chrome.userScripts
    //   注入。界面上必须能看见三件事, 否则"游戏里行为不对"时根本判断不了是脚本旧了、
    //   没拉到、还是没注册上: 现在跑的是哪一版 / 什么时候取的 / 注册上了没有。
    function bridgeRow() {
        var br = st.bridge || null;
        var row = document.createElement('div');
        row.className = 'srow';

        var head = document.createElement('div');
        head.className = 'nm';
        head.innerHTML = '<b>自走棋助手主脚本</b>'
            + (br && br.ver ? ' <span class="tag">v' + esc(br.ver) + '</span>'
                : ' <span class="tag dup">还没取到</span>')
            + ' <span class="tag cur">助手下发·不内置</span>'
            + (br && br.cached ? '' : ' <span class="tag dup">本地无缓存</span>')
            + (br && br.cached && !br.registered ? ' <span class="tag dup">没注册进页面</span>' : '');
        row.appendChild(head);

        var mt = document.createElement('div');
        mt.className = 'sk';
        var when = (br && br.at) ? new Date(br.at).toLocaleString() : '';
        mt.textContent = (br && br.cached)
            ? ('取到时间: ' + when + (br.size ? (' · ' + Math.round(parseInt(br.size, 10) / 1024) + 'KB') : '')
                + (br.error ? (' · ⚠ ' + br.error) : '')
                + ' —— 换了新版后【刷新游戏页】才是新的')
            : ('还没从助手取到脚本 —— 打开助手 →「资源替换」→ 点「更新全能扩展脚本」, '
                + '或点下面的「立即检查更新」' + (br && br.error ? ('（上次: ' + br.error + '）') : ''));
        row.appendChild(mt);

        var ft = document.createElement('div');
        ft.className = 'ft';
        ft.appendChild(miniBtn('立即检查更新', async function () {
            var r = await send({ type: 'bridgeUpdate' });
            if (!r || r.ok === false) toast((r && r.error) ? r.error : '更新失败', true);
            else if (r.skipped) toast('刚刚查过, 稍等几秒再点', true);
            else toast(r.updated ? ('已换成 v' + (r.ver || '?') + ' —— 刷新游戏页生效') : '已经是最新版');
            await refreshScripts();
        }, 'primary'));
        row.appendChild(ft);
        return row;
    }

    function renderScripts() {
        var hint = $('usHint');
        var box = $('scriptRows');
        box.textContent = '';

        if (!st.userScriptsOk) {
            hint.className = 'hint warn';
            // 主脚本现在也走 userScripts（从助手拉回来后用它注入）—— 不可用时不是
            // "只是不能加脚本", 而是主脚本压根进不了游戏页。这句话必须说清楚。
            hint.textContent = '⚠ ' + st.userScriptsError
                + ' —— 主脚本现在也走这个 API, 所以它不可用时【主脚本也进不了游戏页】。';
        } else {
            hint.className = 'hint';
            hint.textContent = '自己添加的脚本走 chrome.userScripts 注入（Tampermonkey 同款 API）—— 可启停、编辑、删除。'
                + '主脚本【不内置】: 由助手下发最新版（离线用上次那份）—— 改了源脚本就去助手「资源替换」页点『更新全能扩展脚本』。';
        }
        box.appendChild(bridgeRow());

        st.scripts.forEach(function (s) {
            var row = document.createElement('div');
            row.className = 'srow' + (s.enabled === false ? ' off' : '');

            var head = document.createElement('div');
            head.className = 'nm';
            // 主脚本的版本号不在脚本清单里（它不存代码、也不存版本）—— 从下发状态里取
            var sver = s.builtin ? ((st.bridge && st.bridge.ver) || '') : s.version;
            head.innerHTML = '<b>' + esc(s.name) + '</b>' +
                (sver ? ' <span class="tag">v' + esc(sver) + '</span>' : '') +
                (s.builtin ? ' <span class="tag cur">主脚本·助手下发</span>' : '') +
                (s.enabled === false ? ' <span class="tag dup">已停用</span>' : '');
            row.appendChild(head);

            if (s.description) {
                var de = document.createElement('div');
                de.className = 'sk';
                de.textContent = s.description;
                row.appendChild(de);
            }
            var mt = document.createElement('div');
            mt.className = 'sk';
            mt.textContent = '注入到：' + (s.matches || []).join('  ');
            row.appendChild(mt);

            var ft = document.createElement('div');
            ft.className = 'ft';
            if (!s.builtin) {
                ft.appendChild(miniBtn(s.enabled === false ? '启用' : '停用', async function () {
                    var arr = await readScripts();
                    var hit = findScript(arr, s.id);
                    if (!hit) { toast('找不到这个脚本', true); return; }
                    hit.enabled = hit.enabled === false;
                    var r = await writeScripts(arr);
                    if (r && r.regError) toast('已保存，但注入失败：' + r.regError, true);
                    else toast(hit.enabled === false
                        ? '已停用 —— 刷新游戏页后才会从页面里撤掉'
                        : '已启用 —— 刷新游戏页后才会重新注入');
                    await refreshScripts();
                }));
                ft.appendChild(miniBtn('编辑', async function () {
                    var arr = await readScripts();
                    var hit = findScript(arr, s.id);
                    if (!hit) { toast('找不到这个脚本', true); return; }
                    openScriptEditor({ id: hit.id, name: hit.name, code: hit.code, matches: hit.matches });
                }));
                // B1: 只有带了 @downloadURL/@updateURL 的脚本才有"检查更新"可言
                if (s.updateURL || s.downloadURL) {
                    ft.appendChild(miniBtn('检查更新', async function () {
                        await checkScriptUpdate(s.id, s.downloadURL || s.updateURL);
                    }));
                }
                ft.appendChild(miniBtn('删', async function () {
                    if (!confirm('删除脚本「' + s.name + '」？')) return;
                    var arr = await readScripts();
                    var kept = arr.filter(function (x) { return x.id !== s.id; });
                    var r = await writeScripts(kept);
                    if (r && !r.ok) toast('删除后注入失败：' + (r.regError || r.error || ''), true);
                    else toast('已删除');
                    await refreshScripts();
                    backupFull('delete-script');   // N2: 删脚本也算结构变更，删前其实更好，这里删后立即备份现状
                }, 'danger'));
            }
            row.appendChild(ft);
            box.appendChild(row);
        });
        if (!st.scripts.length) {
            var d = document.createElement('div');
            d.className = 'empty';
            d.textContent = '还没有脚本 —— 点「＋ 添加脚本」';
            box.appendChild(d);
        }
    }

    async function refreshScripts() {
        var r = await send({ type: 'scriptList' });
        if (!r || !r.ok) { toast('读不到脚本清单', true); return; }
        st.scripts = r.scripts || [];
        st.userScriptsOk = r.userScriptsOk;
        st.userScriptsError = r.userScriptsError;
        st.scriptsLoaded = true;
        // 主脚本的下发状态(版本/取到时间/注册上了没) —— 拿不到也不能卡住列表
        try {
            var b = await send({ type: 'bridgeStatus' });
            st.bridge = (b && b.ok) ? b : null;
        } catch (e) { st.bridge = null; }
        renderScripts();
    }

    // B1 脚本检查更新（按 @downloadURL/@updateURL）。跨域抓取要在管理页(有用户手势)先申请主机权限。
    async function checkScriptUpdate(id, url) {
        var m = /^(https?:\/\/[^\/]+)/i.exec(String(url || ''));
        if (m) {
            var origins = [m[1] + '/*'];
            var has = await new Promise(function (res) { chrome.permissions.contains({ origins: origins }, res); });
            if (!has) {
                var granted = await new Promise(function (res) { chrome.permissions.request({ origins: origins }, res); });
                // 不 return: 授权失败也能靠助手 /ext/script-proxy 代拉(需要助手开着)
                if (!granted) toast('未授权 ' + m[1] + ' —— 改走助手代拉（需要助手开着）');
            }
        }
        toast('正在检查更新…');
        var r = await send({ type: 'scriptCheckUpdate', id: id });
        if (!r || !r.ok) { toast('检查更新失败：' + ((r && r.error) || '未知'), true); return; }
        if (!r.hasUpdate) { toast('已是最新（v' + (r.newVersion || r.oldVersion || '?') + '）'); return; }
        if (!window.confirm('发现新版本 v' + (r.oldVersion || '?') + ' → v' + (r.newVersion || '?') + '\n更新并重新注入？')) return;
        var a = await send({ type: 'scriptApplyUpdate', id: id });
        if (a && a.ok && a.hasUpdate) {
            toast('已更新到 v' + (a.newVersion || '?') + ' 并重新注入（刷新游戏页后生效）');
            await refreshScripts();
            backupFull('script-update');   // N2: 更新替换了代码，备份新版
        } else if (a && a.ok) {
            toast('没有变化');
        } else {
            toast('更新失败：' + ((a && a.error) || '未知'), true);
        }
    }

    function openScriptEditor(pre) {
        st.seId = pre && pre.id ? pre.id : null;
        $('scriptEditor').hidden = false;
        $('seName').value = pre && pre.name ? pre.name : '';
        $('seMatches').value = pre && pre.matches ? pre.matches.join('\n') : SC.DEFAULT_MATCHES.join('\n');
        var code = pre && pre.code ? pre.code : '';
        // ★ 大脚本(锦囊袋 7.3MB)整段塞进 <textarea> 会直接卡死管理页 —— 超过阈值就只读 + 禁保存,
        //   改走「停用 / 删除 / 导出」。300KB 足够覆盖正常脚本, 不会误伤。
        if (code.length > 300 * 1024) {
            st.seHuge = true;
            $('seCode').value = '';
            $('seCode').readOnly = true;
            $('seHint').className = 'hint warn';
            $('seHint').textContent = '脚本过大(' + Math.round(code.length / 1024) + 'KB)，无法在编辑器内打开 —— 请用「停用 / 删除 / 导出」。';
        } else {
            st.seHuge = false;
            $('seCode').value = code;
            $('seCode').readOnly = false;
            $('seHint').textContent = '';
        }
        $('seCode').focus();
    }

    function closeScriptEditor() {
        $('scriptEditor').hidden = true;
        st.seId = null;
    }

    $('seCancel').addEventListener('click', closeScriptEditor);

    $('seSave').addEventListener('click', async function () {
        if (st.seHuge) { toast('脚本过大，不能在编辑器内保存 —— 请用「删除」或「停用」', true); return; }
        var matches = $('seMatches').value.split(/\r?\n/).map(function (s) { return s.trim(); })
            .filter(function (s) { return s; });
        var n = SC.normalizeScript({
            id: st.seId || undefined,
            name: $('seName').value.trim(),
            code: $('seCode').value,
            matches: matches
        });
        if (!n.ok) {
            $('seHint').className = 'hint warn';
            $('seHint').textContent = n.error;
            return;
        }
        var arr = await readScripts();
        if (st.seId) {
            var idx = -1;
            for (var i = 0; i < arr.length; i++) if (arr[i].id === st.seId) idx = i;
            if (idx >= 0) {
                n.script.enabled = arr[idx].enabled !== false;
                n.script.addedAt = arr[idx].addedAt || Date.now();
                arr[idx] = n.script;
            } else arr.push(n.script);
        } else {
            for (var j = arr.length - 1; j >= 0; j--) {
                if (!arr[j].builtin && arr[j].name === n.script.name) arr.splice(j, 1);
            }
            n.script.addedAt = Date.now();
            arr.push(n.script);
        }
        var r = await writeScripts(arr);
        (n.warnings || []).forEach(function (w) { toast(w, true); });
        if (r && !r.ok) toast('已保存，但注入失败：' + (r.regError || r.error || ''), true);
        else if (r && r.regError) toast('已保存，但注入失败：' + r.regError, true);
        else toast('已保存并注入' + (r && r.registered != null ? '（生效脚本 ' + r.registered + ' 个）' : ''));
        closeScriptEditor();
        await refreshScripts();
    });

    $('addScriptBtn').addEventListener('click', function () { openScriptEditor(null); });
    $('refreshScripts').addEventListener('click', refreshScripts);

    $('importScriptsBtn').addEventListener('click', function () { $('scriptFile').click(); });
    $('scriptFile').addEventListener('change', function (ev) {
        var files = Array.prototype.slice.call(ev.target.files || []);
        ev.target.value = '';
        files.forEach(function (f) {
            var fr = new FileReader();
            fr.onload = async function () {
                var text = String(fr.result || '');
                if (/\.json$/i.test(f.name)) {
                    // 备份包（可能是脚本包，也可能是"全部数据"）
                    var r = SC.importJson(text);
                    if (!r.ok) {
                        var b = importBackup(text);
                        if (!b.ok) { toast(r.error, true); return; }
                        toast('备份已导入');
                        await refreshScripts();
                        return;
                    }
                    var arr = await readScripts();
                    var added = 0;
                    for (var i = 0; i < r.entries.length; i++) {
                        var e = r.entries[i];
                        for (var j = arr.length - 1; j >= 0; j--) {
                            if (!arr[j].builtin && arr[j].name === e.name) arr.splice(j, 1);
                        }
                        if (!e.addedAt) e.addedAt = Date.now();
                        arr.push(e);
                        added++;
                    }
                    await writeScripts(arr);
                    toast('导入 ' + added + ' 个脚本' + (r.skipped ? '，格式不对跳过 ' + r.skipped : ''));
                    await refreshScripts();
                    backupFull('import-scripts');   // N2: 结构变更 → 含代码全备份
                    return;
                }
                // 普通 .user.js / .js → 进编辑器让人确认一眼再装（油猴也是这个节奏）
                var head = SC.parseHeader(text);
                openScriptEditor({
                    name: head.name || f.name.replace(/\.(user\.js|js)$/i, ''),
                    code: text,
                    matches: head.matches.length ? head.matches : SC.DEFAULT_MATCHES.slice()
                });
            };
            fr.readAsText(f, 'utf-8');
        });
    });

    // ================================================================ 获取新脚本
    async function loadMarket() {
        var b = base();
        var box = $('mList');
        box.textContent = '';
        $('mCount').textContent = '';
        if (!b) {
            var d = document.createElement('div');
            d.className = 'empty';
            d.textContent = '先在「总览」里连上助手（端口没设好）';
            box.appendChild(d);
            return;
        }
        try {
            var resp = await fetch(b + '/scripts.json', { cache: 'no-store' });
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            var obj = await resp.json();
            st.market = Array.isArray(obj.scripts) ? obj.scripts : [];
        } catch (e) {
            st.market = [];
            var d2 = document.createElement('div');
            d2.className = 'empty';
            d2.textContent = '取不到清单(' + e.message + ') —— 助手没开或版本太旧(需要 v4.72+)';
            box.appendChild(d2);
            return;
        }
        $('mCount').textContent = st.market.length ? ('共 ' + st.market.length + ' 个') : '';
        if (!st.market.length) {
            var d3 = document.createElement('div');
            d3.className = 'empty';
            d3.textContent = '助手没提供可安装的脚本';
            box.appendChild(d3);
            return;
        }
        st.market.forEach(function (m) {
            var row = document.createElement('div');
            row.className = 'srow';
            var nm = document.createElement('div');
            nm.className = 'nm';
            nm.innerHTML = '<b>' + esc(m.name) + '</b> <span class="tag">' + (m.size ? Math.round(m.size / 1024) + 'KB' : '') + '</span>' +
                (m.builtin ? ' <span class="tag cur">主脚本·助手下发(不用装)</span>' : '');
            row.appendChild(nm);
            if (m.description) {
                var de = document.createElement('div');
                de.className = 'sk';
                de.textContent = m.description;
                row.appendChild(de);
            }
            var ft = document.createElement('div');
            ft.className = 'ft';
            if (!m.builtin) {
                ft.appendChild(miniBtn('安装', async function () {
                    var r = await installFromHelper(m);
                    if (r) toast('已安装「' + m.name + '」');
                    await refreshScripts();
                }, 'primary'));
            }
            row.appendChild(ft);
            box.appendChild(row);
        });
    }

    async function installFromHelper(m) {
        try {
            var resp = await fetch(base() + '/' + m.file, { cache: 'no-store' });
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            var code = await resp.text();
            var arr = await readScripts();
            var n = SC.normalizeScript({ code: code, name: m.name });
            if (!n.ok) { toast(n.error, true); return false; }
            for (var j = arr.length - 1; j >= 0; j--) {
                if (!arr[j].builtin && arr[j].name === n.script.name) arr.splice(j, 1);
            }
            n.script.addedAt = Date.now();
            arr.push(n.script);
            var r = await writeScripts(arr);
            if (r && r.regError) toast('装好了，但注入失败：' + r.regError, true);
            return true;
        } catch (e) {
            toast('下载/安装失败：' + e.message, true);
            return false;
        }
    }

    $('refreshMarket').addEventListener('click', loadMarket);
    $('openMarketRes').addEventListener('click', function () {
        if (!base()) { toast('先连上助手(端口没设好)', true); return; }
        chrome.tabs.create({ url: base() + '/res' });
    });

    // ================================================================ 设置
    function renderSettings() {
        // 值来自最近一次 refreshStatus 的缓存
        $('setAutoSync').checked = st.autoSync !== false;
        $('setBadge').checked = st.badge !== false;
        // 脚本总开关: 老版本 storage 里没这个键 → undefined 也算开(默认开)
        var se = $('setScriptsEnabled');
        if (se) se.checked = st.scriptsEnabled !== false;
    }
    $('setAutoSync').addEventListener('change', async function () {
        await send({ type: 'setSetting', key: 'autoSync', value: $('setAutoSync').checked });
        toast($('setAutoSync').checked ? '已开启自动同步' : '已关闭自动同步（只剩手动同步）');
    });
    $('setBadge').addEventListener('change', async function () {
        await send({ type: 'setSetting', key: 'badge', value: $('setBadge').checked });
        var r = await send({ type: 'sync' });       // 立刻按新设置重画角标
        if (r && r.error) toast(r.error, true);
    });
    var _seEl = $('setScriptsEnabled');
    if (_seEl) _seEl.addEventListener('change', async function () {
        var on = _seEl.checked;
        await send({ type: 'setSetting', key: 'scriptsEnabled', value: on });
        // 后台收到后会立刻 syncScripts+syncBridge(不等一分钟的 alarm)
        if (on) toast('脚本注入已开启 —— 游戏页要刷新一次才会重新注入');
        else toast('脚本注入已关闭 —— 已撤销注册；当前打开的游戏页刷新后才彻底停', true);
    });

    // ================================================================ 实用工具
    function utilOut(cls, text) {
        var box = $('utilOut');
        box.hidden = false;
        box.className = 'probe' + (cls ? ' ' + cls : '');
        box.textContent = text;
    }

    // N2: 手动“备份到助手”(含脚本代码)
    $('utilBackup').addEventListener('click', async function () {
        toast('正在备份到助手…');
        var r = await backupFull('manual-btn');
        if (r && r.ok) toast('已备份到助手：' + (r.file || ''));
        else toast('备份失败：' + ((r && r.error) || '助手没开?'), true);
    });

    // N2: 查看助手备份 + 从备份恢复(设置/手动规则/自添脚本)
    $('utilListBk').addEventListener('click', async function () {
        var base = M.baseUrlFromPort(st.port);
        var box = $('backupList');
        if (!base) { toast('端口没设好', true); return; }
        box.textContent = '正在读取…';
        var r = await fetch(base + '/ext/backups', { cache: 'no-store' })
            .then(function (x) { return x.json(); }).catch(function () { return null; });
        if (!r || !r.ok) { box.textContent = '读不到备份列表：' + ((r && r.error) || '助手没开?'); return; }
        box.textContent = '';
        if (!r.items.length) { box.textContent = '还没有备份 —— 改条规则或点「备份到助手」就会生成'; return; }
        r.items.forEach(function (it) {
            var d = new Date((it.mtime || 0) * 1000);
            var pad = function (n) { return (n < 10 ? '0' : '') + n; };
            var when = d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate())
                + ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
            var row = document.createElement('div');
            row.className = 'srow';
            var nm = document.createElement('div');
            nm.className = 'nm';
            nm.innerHTML = '<b>' + esc(when) + '</b>'
                + (it.why ? ' <span class="tag">' + esc(it.why) + '</span>' : '')
                + ' <span class="sk">' + (it.size / 1024).toFixed(1) + ' KB</span>';
            row.appendChild(nm);
            var ft = document.createElement('div');
            ft.className = 'ft';
            ft.appendChild(miniBtn('恢复此备份', async function () {
                if (!window.confirm('用这份备份覆盖当前的 手动规则/自添脚本/设置？\n(' + when + (it.why ? ' · ' + it.why : '') + ')')) return;
                await restoreBackup(it.file);
            }));
            row.appendChild(ft);
            box.appendChild(row);
        });
    });

    async function restoreBackup(file) {
        var base = M.baseUrlFromPort(st.port);
        if (!base) { toast('端口没设好', true); return; }
        toast('正在恢复…');
        var obj = await fetch(base + '/ext/backups/' + encodeURIComponent(file), { cache: 'no-store' })
            .then(function (x) { if (!x.ok) throw new Error('HTTP ' + x.status); return x.json(); })
            .catch(function (e) { return { __err: String((e && e.message) || e) }; });
        if (obj.__err || obj.type !== 'sgs-ext-backup') { toast('读取备份失败：' + (obj.__err || '格式不对'), true); return; }
        if (obj.settings) {
            await localSet({
                port: obj.settings.port, enabled: obj.settings.enabled !== false,
                autoSync: obj.settings.autoSync !== false, badge: obj.settings.badge !== false,
                scriptsEnabled: obj.settings.scriptsEnabled !== false
            });
        }
        st.manualEntries = Array.isArray(obj.manual) ? obj.manual : [];
        await saveManual('已恢复规则');       // 存规则 + 触发一次同步(用恢复后的端口)
        if (Array.isArray(obj.scripts) && obj.scripts.length) {
            await writeScripts(obj.scripts.filter(function (s) { return s; }));   // 含代码才恢复, 不误删现有
            await refreshScripts();
        }
        await refreshStatus();
        renderRules();
        toast('已从备份恢复' + (obj.scripts && obj.scripts.length ? '（含脚本）' : ''));
    }

    $('utilTest').addEventListener('click', async function () {
        utilOut('', '正在连接…');
        var r = await send({ type: 'utilTest' });
        if (!r || !r.ok) {
            utilOut('bad', '✗ 连不上(' + (r && r.ms != null ? r.ms + 'ms' : '—') + ')' +
                (r && r.error ? ' —— ' + r.error : ''));
            return;
        }
        utilOut('', '✓ 助手在线 · ' + r.ms + 'ms · 赛季 ' + (r.season || '—') +
            ' · 版本戳 ' + (r.rev || '—') + ' · 规则 ' + r.rules + ' 条');
    });

    $('utilRereg').addEventListener('click', async function () {
        utilOut('', '正在重新注入…');
        var r = await send({ type: 'utilReRegister' });
        if (r && !r.ok) utilOut('bad', '✗ ' + (r.error || '失败'));
        else utilOut('', '✓ 已重新注入 ' + (r && r.registered != null ? r.registered : 0) + ' 个脚本' +
            (r && r.error ? '（部分失败：' + r.error + '）' : ''));
        await refreshScripts();
    });

    $('utilExport').addEventListener('click', async function () {
        var arr = await readScripts();
        var codes = [];
        arr.forEach(function (s) {
            if (s.builtin) return;
            codes.push({
                name: s.name, version: s.version, description: s.description,
                matches: s.matches, code: s.code, enabled: s.enabled !== false
            });
        });
        var payload = {
            type: 'sgs-ext-backup', version: 1,
            exportedAt: new Date().toISOString(),
            manual: st.manualEntries,
            scripts: codes,
            settings: { port: st.port, autoSync: st.autoSync, badge: st.badge }
        };
        var url = URL.createObjectURL(new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' }));
        var a = document.createElement('a');
        a.href = url;
        a.download = 'sgs-ext-backup.json';
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(function () { URL.revokeObjectURL(url); }, 4000);
        utilOut('', '✓ 已导出：手动规则 ' + st.manualEntries.length + ' 条 · 脚本 ' + codes.length + ' 个');
    });

    $('utilImport').addEventListener('click', function () { $('utilFile').click(); });
    $('utilFile').addEventListener('change', function (ev) {
        var f = ev.target.files && ev.target.files[0];
        ev.target.value = '';
        if (!f) return;
        var fr = new FileReader();
        fr.onload = async function () {
            var b = importBackup(String(fr.result || ''));
            if (!b.ok) { toast(b.error, true); return; }
            var parts = [];
            if (b.manual) {
                await new Promise(function (res) {
                    chrome.storage.local.set({ manual: b.manual }, res);
                });
                await send({ type: 'manualChanged' });
                parts.push('手动规则 ' + b.manual.length + ' 条');
            }
            if (b.settings && R_isLocalPort(b.settings.port)) {
                await send({ type: 'setPort', port: b.settings.port });
                parts.push('端口 ' + b.settings.port);
            }
            var arr = await readScripts();
            var added = 0;
            for (var i = 0; i < b.scripts.length; i++) {
                var n = SC.normalizeScript({ code: b.scripts[i].code, name: b.scripts[i].name, matches: b.scripts[i].matches });
                if (!n.ok) continue;
                for (var j = arr.length - 1; j >= 0; j--) {
                    if (!arr[j].builtin && arr[j].name === n.script.name) arr.splice(j, 1);
                }
                n.script.addedAt = Date.now();
                arr.push(n.script);
                added++;
            }
            await writeScripts(arr);
            if (b.scripts.length) parts.push('脚本 ' + added + '/' + b.scripts.length + ' 个');
            await refreshStatus();
            await refreshScripts();
            toast('备份已导入：' + (parts.join(' · ') || '空备份'));
        };
        fr.readAsText(f, 'utf-8');
    });

    // 备份包 → {ok, manual?, scripts[], settings?}；type 不对报错，字段缺省容忍
    function importBackup(text) {
        var obj;
        try { obj = JSON.parse(text); } catch (e) { return { ok: false, error: '不是合法 JSON' }; }
        if (!obj || (obj.type !== 'sgs-ext-backup' && obj.type !== 'sgs-ext-scripts')) {
            return { ok: false, error: '不是本扩展导出的备份/脚本包' };
        }
        var out = { ok: true, manual: null, scripts: [], settings: null };
        if (Array.isArray(obj.scripts)) {
            for (var i = 0; i < obj.scripts.length; i++) {
                var raw = obj.scripts[i] || {};
                if (typeof raw.code !== 'string' || !raw.code.trim()) continue;
                out.scripts.push(raw);
            }
        }
        if (obj.type === 'sgs-ext-backup') {
            if (Array.isArray(obj.manual)) out.manual = obj.manual;
            if (obj.settings && typeof obj.settings === 'object') out.settings = obj.settings;
        }
        return out;
    }

    function R_isLocalPort(p) {
        var n = parseInt(p, 10);
        return !isNaN(n) && n >= 1 && n <= 65535;
    }

    $('utilWipe').addEventListener('click', async function () {
        if (!confirm('清空全部手动规则？(助手下发的规则不受影响)')) return;
        await new Promise(function (res) { chrome.storage.local.set({ manual: [] }, res); });
        await send({ type: 'manualChanged' });
        await refreshStatus();
        toast('手动规则已清空');
    });

    // ---------------------------------------------------------------- 启动
    refreshStatus().then(loadServerRules);
})();

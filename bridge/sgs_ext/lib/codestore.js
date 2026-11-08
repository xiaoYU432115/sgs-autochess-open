// 全能扩展 ·【大对象分片存储】的纯逻辑层（可在 Node 里直接跑，见 dev/_verify_codestore.js）
//
// 为什么要有它（"容量一大就不行了"这条的根因）
// ==========================================
// chrome.storage.local 名义上有两条限制：
//   ① 总量 QUOTA_BYTES = 5MB —— 声明了 unlimitedStorage 就没了；
//   ② 【单项】QUOTA_BYTES_PER_ITEM = 5MB —— 通常说这条不随 unlimitedStorage 解除。
//
// ★ 真机实测(2026-10-04, Edge + 本扩展, 已声明 unlimitedStorage):
//     navigator.storage.estimate() → 配额 276GB;
//     把 6MB 字符串塞进【单个键】→ set 成功无 lastError, 再 get 回来 6291456 字节, 一个不少。
//   也就是【本环境下单项 5MB 这条并没有生效】。所以分片在这台机器上不是"不这么做就会炸",
//   它的价值是: ① 别的浏览器/没 unlimitedStorage 时仍安全; ② 单键不会无限膨胀。
//   别把它当成"修好了容量一大就不行"来理解 —— 那是错的, 记录在此免得以后照着错前提改。
//
// 解法：把大对象 JSON 化后切成 ≤1MB 的片，分散到 `scripts#0 / scripts#1 ...` 这些键上，
//   主键只留 {__chunks, __bytes} 这种几十字节的目录。读的时候按目录拼回来。
//   小对象（<1MB）【原样存主键】，不走分片 —— 少一次往返，也保持和旧版数据兼容。
//
// 兼容性（旧数据必须还能读，否则用户一升级脚本全没了）：
//   读的时候先认目录（分片），认不到就当"旧格式整块值"返回。所以从没分片过的老数据
//   升级上来照样用；下次保存时才自动转成分片格式。
//
// 这里只做【纯函数】：给我一个 storage 快照 → 还你对象；给你对象 → 还你要写的键值对。
//   真正的 chrome.storage 调用在 background.js / options.js，这样离线也能测。
(function (root) {
    'use strict';

    var CHUNK = 1024 * 1024;          // 单片上限 1MB（留足余量给 5MB 的单项上限）
    var SEP = '#';                    // 主键与片号的 separator
    var MARK = '__chunks';            // 目录键的标记字段

    // ---------------------------------------------------------------- 写
    // 返回要交给 chrome.storage.local.set 的对象；同时返回要 remove 的键列表
    // （片数变少时，旧的尾巴片必须删掉，否则下次拼回来会多出一截脏数据）。
    function planWrite(key, value) {
        var json;
        try { json = JSON.stringify(value == null ? null : value); }
        catch (e) { return { ok: false, error: '对象无法序列化: ' + (e && e.message || e) }; }
        var bytes = json.length;

        if (bytes <= CHUNK) {                       // 小对象：原样存主键
            var small = {}; small[key] = value;
            return { ok: true, set: small, remove: [], chunked: false, chunks: 0, bytes: bytes };
        }

        var set = {}, n = Math.ceil(bytes / CHUNK);
        for (var i = 0; i < n; i++) {
            set[key + SEP + i] = json.slice(i * CHUNK, (i + 1) * CHUNK);
        }
        var meta = {};
        meta[MARK] = n;
        meta['__bytes'] = bytes;
        set[key] = meta;                            // 主键变成目录
        return { ok: true, set: set, remove: [], chunked: true, chunks: n, bytes: bytes };
    }

    // ★ v4.140 真机事故后重写: 要删哪些片, 不能靠"主键目录里写的片数"来算。
    //   事故经过(实测): 主键 `scripts` 被一次小对象写入覆盖成了数组(目录丢了),
    //   但 scripts#0..#7 那 8.4MB 片还躺在存储里 —— 按目录算 oldChunks=0 → 一片都不删,
    //   于是界面上"锦囊袋"凭空消失, 8MB 数据变成谁也读不到的孤儿。
    //   现在改成"保留前 keep 片, 其余一律列入删除" —— 不存在的键 remove 也无害,
    //   不用先把存储整个读一遍才知道有几片。
    function staleChunks(key, keep, maxScan) {
        var out = [];
        var k = parseInt(keep, 10) || 0;
        var upto = parseInt(maxScan, 10) || (k + 64);
        if (upto < k) upto = k;
        for (var i = k; i < upto; i++) out.push(key + SEP + i);
        return out;
    }

    function planRemove(key, oldChunks) {
        var out = [key];
        var n = parseInt(oldChunks, 10) || 0;
        for (var i = 0; i < n; i++) out.push(key + SEP + i);
        return out;
    }

    // ---------------------------------------------------------------- 读
    // store: chrome.storage.local.get(...) 的结果对象（或一个普通对象，测试时直接造）
    function readFrom(store, key) {
        if (!store || typeof store !== 'object') return undefined;
        if (!(key in store)) return undefined;
        var v = store[key];

        // 分片格式：主键是目录 {__chunks:n, __bytes:len}
        if (v && typeof v === 'object' && !Array.isArray(v) && v[MARK]) {
            var n = parseInt(v[MARK], 10) || 0;
            var parts = [];
            for (var i = 0; i < n; i++) {
                var c = store[key + SEP + i];
                if (typeof c !== 'string') {
                    return { __sgsChunkError: '缺第 ' + i + ' 片(共 ' + n + ' 片) —— 存储被清过？' };
                }
                parts.push(c);
            }
            var json = parts.join('');
            try { return JSON.parse(json); }
            catch (e) { return { __sgsChunkError: '分片拼回来解析失败: ' + (e && e.message || e) }; }
        }

        // ★ v4.140 孤儿片恢复: 主键不是目录, 但 key#0.. 这些片还在。
        //   这只可能是"上次是分片写的, 后来主键被一次小对象写入覆盖了" —— 分片里那份
        //   才是完整数据(实测过: 主键只剩 1 条种子, 片里是 2 条共 7.3MB 的脚本)。
        //   不恢复的话用户看到的就是"我的脚本凭空没了"。
        var orphans = [];
        for (var j = 0; ; j++) {
            var ck = store[key + SEP + j];
            if (typeof ck !== 'string') break;
            orphans.push(ck);
            if (j > 512) break;                     // 疯了才这么多片, 到此为止
        }
        if (orphans.length) {
            var joined = orphans.join('');
            try {
                var rec = JSON.parse(joined);
                // 只有"片里那份明显更完整"才覆盖主键那份 —— 拿长度当代理指标。
                // 否则宁可信任主键(正常情况下主键就是权威)。
                var sizeOf = function (x) { try { return JSON.stringify(x == null ? null : x).length; } catch (e) { return 0; } };
                if (sizeOf(rec) > sizeOf(v)) {
                    rec.__sgsRecovered = '从 ' + orphans.length + ' 个残留分片恢复'
                        + '（主键被覆盖过, 已按更完整的那份算）';
                    return rec;
                }
            } catch (e) { /* 拼不出来就当没看见, 继续用主键那份 */ }
        }

        return v;   // 旧格式：整块值（数组/字符串/对象都原样）
    }

    // 给定 store，列出某个键占用的全部键名（迁移/清理/统计用）
    function keysOf(store, key) {
        var out = [];
        if (!store || typeof store !== 'object') return out;
        Object.keys(store).forEach(function (k) {
            if (k === key || k.indexOf(key + SEP) === 0) out.push(k);
        });
        return out;
    }

    // 现有分片数。目录在就以目录为准；目录丢了就【实际扫一遍】key#0.. 有几个算几个
    //   （原来目录不在一律返回 0，这正是孤儿片没人管的原因）。
    function chunksOf(store, key) {
        if (!store || typeof store !== 'object') return 0;
        var v = store[key];
        if (v && typeof v === 'object' && !Array.isArray(v) && v[MARK]) return parseInt(v[MARK], 10) || 0;
        var n = 0;
        while (n <= 512 && typeof store[key + SEP + n] === 'string') n++;
        return n;
    }

    function human(bytes) {
        var b = parseInt(bytes, 10) || 0;
        if (b < 1024) return b + ' B';
        if (b < 1024 * 1024) return (b / 1024).toFixed(1) + ' KB';
        return (b / 1024 / 1024).toFixed(2) + ' MB';
    }

    var api = {
        CHUNK: CHUNK, SEP: SEP, MARK: MARK,
        planWrite: planWrite,
        planRemove: planRemove,
        readFrom: readFrom,
        keysOf: keysOf,
        chunksOf: chunksOf,
        staleChunks: staleChunks,
        human: human
    };

    if (typeof module !== 'undefined' && module.exports) module.exports = api;   // Node 测试
    else root.SGSCodeStore = api;                                               // 扩展里挂 self
})(typeof self !== 'undefined' ? self : this);

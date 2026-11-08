// 一键探针: dump 技能进度计数器(吴六剑「已招募」/ 李广「飞将·剩余」等)
// 只读, 不改任何逻辑。在【游戏页控制台 F12】粘贴运行, 把 SGS_PROBE_START~END 之间的输出贴回即可。
// 原理: 这些计数器都存在 SelfInfo 里(武器在 Equipments, 武将技能在 chessPlayerSkillInfo / 棋子实例),
//   但运行时字段名配置没给, 必须 dump 全字段(含原型链, 游戏很多字段挂在原型上 JSON.stringify 抓不到)。
(function () {
  const getMgr = (typeof getManager === 'function') ? getManager
    : (window.getManager || window.gameManager || null);
  const m = getMgr ? getMgr() : null;
  if (!m) { console.log('SGS_PROBE_ERR: 没有 getManager()'); return; }
  const SI = m.SelfInfo || m.selfInfo || null;
  if (!SI) { console.log('SGS_PROBE_ERR: 没有 SelfInfo'); return; }

  // 跨原型链抓所有属性名(游戏字段常挂在原型上)
  function allKeys(obj) {
    const seen = new Set(); let o = obj;
    while (o && o !== Object.prototype && o !== null) {
      try { Object.getOwnPropertyNames(o).forEach(k => seen.add(k)); } catch (_) {}
      try { o = Object.getPrototypeOf(o); } catch (_) { break; }
    }
    return [...seen];
  }
  // 安全快照: 防环 + 深度截断 + 数组截断, 避免爆栈
  function snap(obj, depth, seen) {
    if (obj === null || obj === undefined) return obj;
    if (typeof obj !== 'object') return obj;
    if (seen.has(obj)) return '[cycle]';
    if (depth > 4) return '[deep]';
    seen.add(obj);
    const out = {};
    for (const k of allKeys(obj)) {
      if (['constructor', '__proto__', 'prototype'].includes(k)) continue;
      try {
        const v = obj[k];
        if (typeof v === 'function') continue;
        if (typeof v === 'object' && v !== null) {
          out[k] = Array.isArray(v)
            ? v.slice(0, 24).map(x => snap(x, depth + 1, seen))
            : snap(v, depth + 1, seen);
        } else out[k] = v;
      } catch (_) {}
    }
    seen.delete(obj);
    return out;
  }
  // 只抽「技能/进度/计数」相关 primitive 字段, 便于一眼定位
  function skillHints(obj, tag) {
    const hints = [];
    try {
      const ks = allKeys(obj);
      const hit = ks.filter(k => /skill|progress|count|num|remain|recruit|进度|飞将|已招募|trigger|cd|times|used/i.test(k));
      for (const k of hit) {
        let v; try { v = obj[k]; } catch (_) { continue; }
        if (typeof v === 'function') continue;
        hints.push({ from: tag, key: k, val: (typeof v === 'object' ? JSON.stringify(v).slice(0, 160) : v) });
      }
    } catch (_) {}
    return hints;
  }

  const result = { ts: Date.now(), selfInfoTopKeys: allKeys(SI) };

  // 1) 武器(吴六剑 已招募): SelfInfo.Equipments
  const eqKeys = ['Equipments', 'equipments', 'EquipmentList', 'equipList'];
  let eq = null;
  for (const k of eqKeys) { if (SI[k]) { eq = SI[k]; break; } }
  if (Array.isArray(eq)) {
    result.equipments = eq.map((e, i) => {
      const o = snap(e, 0, new WeakSet());
      o.__hint = skillHints(e, 'equip#' + i);
      return o;
    });
  } else {
    result.equipments = eq ? snap(eq, 0, new WeakSet()) : 'MISSING';
  }

  // 2) 武将技能(李广 飞将 剩余): SelfInfo.chessPlayerSkillInfo
  const cpsi = SI.chessPlayerSkillInfo || SI.ChessPlayerSkillInfo || null;
  result.chessPlayerSkillInfo = cpsi ? snap(cpsi, 0, new WeakSet()) : 'MISSING';

  // 3) 棋子实例(李广在场上/手牌时, 进度可能挂在棋子对象上): 只抽技能相关 hint
  const hints = [];
  for (const key of ['BattleChess', 'HandChess', 'battleChess', 'handChess', 'LineupChess', 'BenchChess']) {
    const arr = SI[key] || (m[key]) || null;
    if (!Array.isArray(arr)) continue;
    arr.slice(0, 14).forEach((c, i) => {
      const name = (c && (c.name || c.Name || c.chessName)) || ('#' + i);
      skillHints(c, key + ':' + name).forEach(h => hints.push(h));
    });
  }
  result.chessSkillHints = hints;

  const txt = JSON.stringify(result, null, 1);
  console.log('=== SGS_PROBE_START ===');
  console.log(txt);
  console.log('=== SGS_PROBE_END ===');
  try { if (navigator.clipboard) navigator.clipboard.writeText(txt).then(() => console.log('(已尝试复制到剪贴板)')); } catch (_) {}
})();

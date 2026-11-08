// src/plugins/select3-equip.js
// 装备三选一（天降神兵，规范 §6-8）。pick 为纯函数（可测），createEquipRunner 负责事务/来源关联/三向确认（§6.5-§6.6）。
// 运行时依赖通过 deps 注入：Runtime / Config / Debug，便于在 node 下用 mock 单测。

const EQUIP_GM_KEY = 'sgs_shenbing_targets_v1';
const WULI = 4112011; // 吴六剑

// §7 装备附表（ID → {name, type, round, available}）。type: 1=武器 2=防具 3=坐骑
const EQUIP_DATA = {
  4111001: { name: '白鹄', type: 'horse', round: 3, available: true },
  4111002: { name: '乌骓', type: 'horse', round: 3, available: true },
  4111003: { name: '照夜玉狮子', type: 'horse', round: 3, available: true },
  4111004: { name: '的卢', type: 'horse', round: 3, available: true },
  4111005: { name: '骅骝', type: 'horse', round: 3, available: true },
  4111006: { name: '紫骍', type: 'horse', round: 3, available: true },
  4111007: { name: '惊帆', type: 'horse', round: 3, available: true },
  4111008: { name: '乌云踏雪', type: 'horse', round: 3, available: true },
  4111009: { name: '赤兔', type: 'horse', round: 3, available: true },
  4111010: { name: '大宛', type: 'horse', round: 3, available: true },
  4111011: { name: '绝影', type: 'horse', round: 3, available: true },
  4111012: { name: '爪黄飞电', type: 'horse', round: 3, available: true },
  4112001: { name: '古锭刀', type: 'weapon', round: 7, available: false },
  4112002: { name: '蒺藜骨朵', type: 'weapon', round: 7, available: true },
  4112003: { name: '朱雀羽扇', type: 'weapon', round: 7, available: true },
  4112004: { name: '诸葛连弩', type: 'weapon', round: 7, available: true },
  4112005: { name: '寒冰剑', type: 'weapon', round: 7, available: true },
  4112006: { name: '青龙偃月刀', type: 'weapon', round: 7, available: true },
  4112007: { name: '丈八蛇矛', type: 'weapon', round: 7, available: true },
  4112008: { name: '贯石斧', type: 'weapon', round: 7, available: true },
  4112009: { name: '方天画戟', type: 'weapon', round: 7, available: true },
  4112010: { name: '雌雄双股剑', type: 'weapon', round: 7, available: true },
  4112011: { name: '吴六剑', type: 'weapon', round: 7, available: true },
  4112012: { name: '青釭剑', type: 'weapon', round: 7, available: true },
  4113001: { name: '丹画盾', type: 'armor', round: 11, available: true },
  4113002: { name: '八卦阵', type: 'armor', round: 11, available: true },
  4113003: { name: '白银狮子', type: 'armor', round: 11, available: true },
  4113004: { name: '藤甲', type: 'armor', round: 11, available: true },
  4113005: { name: '木牛流马', type: 'armor', round: 11, available: true },
  4113006: { name: '流星锤', type: 'weapon', round: 7, available: true },
  4113007: { name: '玉玺', type: 'armor', round: 11, available: true },
  4113008: { name: '锦盒', type: 'armor', round: 11, available: true },
  4113009: { name: '马铠', type: 'armor', round: 11, available: true },
  4113010: { name: '黑光铠', type: 'armor', round: 11, available: true },
  4113011: { name: '羽林甲', type: 'armor', round: 11, available: true },
  4113012: { name: '七星灯', type: 'armor', round: 11, available: true },
};

function dedupe(arr) { const s = new Set(); const o = []; for (const x of arr) { if (!s.has(x)) { s.add(x); o.push(x); } } return o; }

export function equipType(id) {
  const d = EQUIP_DATA[Number(id)];
  if (d) return d.type;
  const s = String(id);
  if (s.startsWith('4111')) return 'horse';
  if (s.startsWith('4112')) return 'weapon';
  if (s.startsWith('4113')) return 'armor';
  return null;
}

export function normalizeEquipCandidate(c) {
  if (!c) return null;
  const cv = c.EquipVO !== undefined ? c.EquipVO : c;
  if (!cv) return null;
  const id = cv.equipmentID != null ? cv.equipmentID : (cv.id != null ? cv.id : cv.goodsID);
  if (id == null) return null;
  let type = cv.type != null
    ? (cv.type === 1 ? 'weapon' : cv.type === 2 ? 'armor' : cv.type === 3 ? 'horse' : cv.type)
    : equipType(id);
  return { id: Number(id), type, name: cv.equipmentName != null ? cv.equipmentName : cv.name };
}

// §6.1 配置归一化：{armor:[],weapon:[]}；去重、过滤不可用项与错误类别；坐骑不设目标
export function normalizeTargets(raw) {
  const out = { armor: [], weapon: [] };
  if (!raw || typeof raw !== 'object') return out;
  const take = (arr, type) => (Array.isArray(arr)
    ? dedupe(arr.map(Number).filter((n) => {
        if (!Number.isFinite(n)) return false;
        const d = EQUIP_DATA[n];
        if (!d) return false;        // 未知 ID 不猜测（§6.1 停止猜测）
        if (!d.available) return false;
        return d.type === type;
      }))
    : []);
  out.armor = take(raw.armor, 'armor');
  out.weapon = take(raw.weapon, 'weapon');
  return out;
}

function rank(id, type, config) {
  const list = type === 'armor' ? config.armor : type === 'weapon' ? config.weapon : null;
  if (!list || !list.length) return Infinity;
  const i = list.map(Number).indexOf(Number(id));
  return i === -1 ? Infinity : i;
}
function currentRank(type, current, config) {
  const cur = current[type];
  if (cur == null) return Infinity;
  return rank(Number(cur), type, config);
}

// §6.2 完整装备选择算法（纯函数）
// candidates: [{id,type,name}]；config: {armor:[],weapon:[]}；ctx:{current:{armor,weapon}, unlocked:{armor,weapon}}
export function pick(candidates, config, ctx) {
  const current = (ctx && ctx.current) || { armor: null, weapon: null };
  const unlocked = (ctx && ctx.unlocked) || { armor: true, weapon: true };
  const isProc = (c) => {
    if (c.type === 'horse') return true;            // 坐骑不设目标/解锁门控
    if (c.type === 'armor') return !!unlocked.armor;
    if (c.type === 'weapon') return !!unlocked.weapon;
    return false;                                   // 未知类型不猜测
  };
  const proc = candidates.filter(isProc);
  if (!proc.length) return null;
  const armors = proc.filter((c) => c.type === 'armor');
  const weapons = proc.filter((c) => c.type === 'weapon');
  const horses = proc.filter((c) => c.type === 'horse');

  const armorUp = armors
    .filter((c) => rank(c.id, 'armor', config) < currentRank('armor', current, config))
    .sort((a, b) => rank(a.id, 'armor', config) - rank(b.id, 'armor', config));
  const weaponUp = weapons
    .filter((c) => rank(c.id, 'weapon', config) < currentRank('weapon', current, config))
    .sort((a, b) => rank(a.id, 'weapon', config) - rank(b.id, 'weapon', config));
  const armorBest = armorUp[0];
  const weaponBest = weaponUp[0];

  if (armorBest) {
    if (rank(armorBest.id, 'armor', config) === 0) return armorBest; // 防具最终目标优先
    // 防具过渡项：若武器首选是吴六剑且其在候选中构成升级 → 选吴六剑
    if (config.weapon[0] === WULI && weapons.some((c) => Number(c.id) === WULI) && rank(WULI, 'weapon', config) < currentRank('weapon', current, config)) {
      return weapons.find((c) => Number(c.id) === WULI);
    }
    return armorBest;
  }
  if (weaponBest) return weaponBest;

  // 无可升级项：优先保护已获名单装备
  const safe = proc.filter((c) => {
    const list = c.type === 'armor' ? config.armor : c.type === 'weapon' ? config.weapon : null;
    const inList = list && list.map(Number).includes(Number(c.id));
    if (!inList) return true;                       // 不在名单（如坐骑）视为安全
    const cur = current[c.type];
    return cur != null && Number(cur) === Number(c.id); // 或候选与当前装备相同
  });
  if (safe.length) {
    const h = safe.find((c) => c.type === 'horse');
    return h || safe[0];
  }
  // safe 空：在全部合法（在名单中）候选里按 rank 取首项
  const ranked = proc
    .filter((c) => {
      const list = c.type === 'armor' ? config.armor : c.type === 'weapon' ? config.weapon : null;
      return list && list.length && list.map(Number).includes(Number(c.id));
    })
    .sort((a, b) => rank(a.id, a.type, config) - rank(b.id, b.type, config));
  return ranked.length ? ranked[0] : null;
}

// ---- runner（事务/来源关联/三向确认 §6.5-§6.6）----
export function createEquipRunner(deps) {
  const Runtime = deps.Runtime;
  const Config = deps.Config || { get() { return null; } };
  const Debug = deps.Debug || { info() {}, warn() {}, error() {}, event() {} };
  const clock = deps.clock || { setTimeout: (fn) => setTimeout(fn, 0), clearTimeout: (h) => clearTimeout(h), now: () => Date.now() };
  const state = { source: null, phase: 'idle', selectedID: null, responseMatched: false, timeout: null };

  function getEquipContext() {
    const eqs = (Runtime.getEquipments && Runtime.getEquipments()) || [];
    const current = { armor: null, weapon: null };
    for (const id of eqs) {
      const t = equipType(id);
      if (t === 'armor') current.armor = id;
      else if (t === 'weapon') current.weapon = id;
    }
    const round = (Runtime.getRound && Runtime.getRound()) || 0;
    const disabled = (Runtime.getDisableEquipTypes && Runtime.getDisableEquipTypes()) || [];
    const unlocked = { armor: round >= 11 && !disabled.includes(2), weapon: round >= 7 && !disabled.includes(1) };
    return { current, unlocked };
  }

  function markUse(goodsID) {
    if (state.source) return false;
    state.source = Number(goodsID);
    return true;
  }

  function handleEquipOptions() {
    if (!state.source) { Debug.info('[equip] 无来源（未用天降神兵），不接管'); return; }
    const eqs = Runtime.waitSelectEquiments();
    if (!eqs || eqs.length === 0) return;
    const norm = eqs.map(normalizeEquipCandidate).filter(Boolean);
    if (!norm.length) return;
    const cfg = normalizeTargets(Config.get(EQUIP_GM_KEY, null));
    const ctx = getEquipContext();
    const chosen = pick(norm, cfg, ctx);
    if (!chosen) { Debug.warn('[equip] 无合法候选，转人工'); return; }
    state.selectedID = chosen.id;
    state.phase = 'submitted';
    state.responseMatched = false;
    Runtime.submitEquip(chosen.id, false);
    state.timeout = clock.setTimeout(() => {
      if (state.selectedID && state.phase === 'submitted') Debug.warn('[equip] 选择超时未确认，保留锁');
    }, 8000);
  }

  function handleEquipResponse(payload) {
    if (!state.selectedID) return;
    if (payload && payload.isPreselection) { Debug.info('[equip] 预选回包不完成'); return; }
    const match = payload && (String(payload.equipmentID) === String(state.selectedID) || String(payload.goodsID) === String(state.selectedID));
    if (!match) return;
    state.responseMatched = true;
    tryConfirm();
  }

  function tryConfirm() {
    if (!state.responseMatched || !state.selectedID) return;
    const eqs = Runtime.waitSelectEquiments();
    const actual = (Runtime.getEquipments && Runtime.getEquipments()) || [];
    if ((!eqs || eqs.length === 0) && actual.map(Number).includes(Number(state.selectedID))) finish('confirmed');
  }

  function finish(reason) {
    Debug.event('equip_finished', { reason, id: state.selectedID });
    state.phase = 'idle';
    state.selectedID = null;
    state.responseMatched = false;
    state.source = null;
    if (state.timeout) { clock.clearTimeout(state.timeout); state.timeout = null; }
  }

  function attach() {
    Runtime.on('RESP_CHOOSE_EQUIP', runner, handleEquipResponse);
    Runtime.on('RESP_CHESS_SPELL_USE', runner, (p) => { if (state.source && p && String(p.goodsID) === String(state.source)) Debug.info('[equip] 用牌成功响应已记'); });
  }
  function detach() {
    Runtime.off('RESP_CHOOSE_EQUIP', runner, handleEquipResponse);
  }

  const runner = { handleEquipOptions, handleEquipResponse, markUse, tryConfirm, finish, attach, detach, getState: () => state };
  return runner;
}

// ---- 插件入口 ----
const TIANJIANG = 311061; // 天降神兵
export default {
  id: 'select3-equip',
  name: '装备（天降神兵）三选一',
  enabled: true,
  load(ctx) {
    if (!ctx.Runtime) return { unload() {} };
    const runner = createEquipRunner({ Runtime: ctx.Runtime, Config: ctx.Config, Debug: ctx.Debug });
    const onUpdate = () => runner.handleEquipOptions();
    const onSpellUse = (p) => { if (p && p.goodsID === TIANJIANG) runner.markUse(TIANJIANG); };
    ctx.Runtime.on('UI_UPDATE_SELECT_CARD', runner, onUpdate);
    ctx.Runtime.on('NOTIFY_START_SELECT', runner, onUpdate);
    ctx.Runtime.on('RESP_CHESS_SPELL_USE', runner, onSpellUse);
    runner.attach();
    return {
      unload() {
        ctx.Runtime.off('UI_UPDATE_SELECT_CARD', runner, onUpdate);
        ctx.Runtime.off('NOTIFY_START_SELECT', runner, onUpdate);
        ctx.Runtime.off('RESP_CHESS_SPELL_USE', runner, onSpellUse);
        runner.detach();
      },
    };
  },
};

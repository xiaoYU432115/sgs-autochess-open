// src/plugins/select3-reward.js
// 武将/锦囊 三选一（规范 §4-5）。pick 为纯函数（可测），createRunner 负责事务/队列（§5）。
// 运行时依赖通过 deps 注入：Runtime / Config / Debug，便于在 node 下用 mock 单测。

const GM_KEY = 'sgs_triple_reward_priority';

// §8 特殊武将固定名单（借尸还魂 311019 / 草船借箭 311002 窗口）
const SPECIAL_CHESS_SPELLS = { '311019': true, '311002': true };
const SPECIAL_CHESS_FIXED = ['陈珪','左慈','贾诩','许劭','蔡文姬','程昱','祢衡','孙尚香','庞德公','高升','黄承彦','诸葛亮','徐庶','张鲁','皇甫嵩','周仓','典韦','廖化','陆逊','丁奉','雷薄','邓艾','邹氏'];
const DEFAULT_CHESS = SPECIAL_CHESS_FIXED.slice();
const DEFAULT_SPELL = [311034,311063,311052,311047,311081,311066,311001,311032,311062,311015,311014,311070,311069,311013,311037,311018,311076,311036,311035];

function dedupe(arr) { const s = new Set(); const o = []; for (const x of arr) { if (!s.has(x)) { s.add(x); o.push(x); } } return o; }

// ---- 配置归一化（§4.1）----
export function normalizeConfig(raw) {
  let cfg = raw;
  if (cfg && cfg.version === 1) {
    cfg = { version: 2, defaultPriority: { chessNames: cfg.chessNames || [], spellIds: (cfg.spellIds || []).map(Number) }, groups: [], activeGroupId: null, hiddenChessNames: [], hiddenSpellIds: [] };
  }
  if (!cfg || typeof cfg !== 'object') {
    cfg = { version: 2, defaultPriority: { chessNames: [], spellIds: [] }, groups: [], activeGroupId: null, hiddenChessNames: [], hiddenSpellIds: [] };
  }
  const dp = cfg.defaultPriority || { chessNames: [], spellIds: [] };
  const chessNames = dedupe((dp.chessNames || []).map(String).filter(Boolean));
  const spellIds = dedupe((dp.spellIds || []).map(Number).filter((n) => Number.isFinite(n)));
  let groups = Array.isArray(cfg.groups) ? cfg.groups.filter((g) => g && g.id != null).map((g) => ({
    id: String(g.id), name: String(g.name || g.id),
    chessNames: dedupe((g.chessNames || []).map(String).filter(Boolean)),
    spellIds: dedupe((g.spellIds || []).map(Number).filter((n) => Number.isFinite(n))),
  })) : [];
  const seen = {}; groups = groups.filter((g) => { if (seen[g.id]) return false; seen[g.id] = 1; return true; });
  let activeGroupId = cfg.activeGroupId != null ? String(cfg.activeGroupId) : null;
  if (activeGroupId && !groups.some((g) => g.id === activeGroupId)) activeGroupId = null;
  const hiddenChessNames = dedupe((cfg.hiddenChessNames || []).map(String).filter(Boolean));
  const hiddenSpellIds = dedupe((cfg.hiddenSpellIds || []).map(Number).filter((n) => Number.isFinite(n)));
  return { version: 2, defaultPriority: { chessNames, spellIds }, groups, activeGroupId, hiddenChessNames, hiddenSpellIds };
}

export function activePriority(cfg) {
  const g = cfg.activeGroupId != null && cfg.groups.find((x) => x.id === cfg.activeGroupId);
  const base = g ? g : cfg.defaultPriority;
  return { chessNames: base.chessNames, spellIds: base.spellIds, hiddenChessNames: cfg.hiddenChessNames, hiddenSpellIds: cfg.hiddenSpellIds };
}

export function defaultConfig() {
  return normalizeConfig({
    version: 2,
    defaultPriority: { chessNames: DEFAULT_CHESS.slice(), spellIds: DEFAULT_SPELL.slice() },
    groups: [], activeGroupId: null, hiddenChessNames: [], hiddenSpellIds: [],
  });
}

// ---- 纯函数选择（§4.2）----
export function pick(options, priority, opts = {}) {
  if (!Array.isArray(options) || options.length === 0) return null;
  const kind = options[0].kind;
  if (kind === 'chess') return pickChess(options, priority, opts);
  if (kind === 'spell') return pickSpell(options, priority, opts);
  return null;
}

export function pickChess(options, priority, opts = {}) {
  let names = (priority && priority.chessNames) || [];
  if (opts.special) names = SPECIAL_CHESS_FIXED;
  const hidden = opts.hiddenChessNames || [];
  const valid = options.filter((o) => o && o.goodsID != null);
  if (valid.length === 0) return null;
  for (const want of names) {
    for (let i = 0; i < valid.length; i++) {
      if (hidden.includes(valid[i].name)) continue;
      if (valid[i].name === want) return valid[i];
    }
  }
  return valid[0]; // 服务器首项兜底（不受 hidden 约束，§4.3）
}

export function pickSpell(options, priority, opts = {}) {
  const hidden = (opts.hiddenSpellIds || []).map(Number);
  const valid = options.filter((o) => o && o.id != null);
  if (valid.length === 0) return null;
  const ids = (priority && priority.spellIds) || [];
  for (const want of ids) {
    for (let i = 0; i < valid.length; i++) {
      if (hidden.includes(Number(valid[i].id))) continue;
      if (Number(valid[i].id) === Number(want)) return valid[i];
    }
  }
  return valid[0];
}

// ---- 候选归一化 ----
export function normalizeCandidate(c) {
  if (!c) return null;
  const cv = c.CardVO !== undefined ? c.CardVO : c;
  if (!cv) return null;
  const isSpell = cv.spellID != null || cv.kind === 'spell' || cv.type === 'spell';
  if (isSpell) return { kind: 'spell', id: cv.spellID, name: cv.spellName != null ? cv.spellName : cv.name, cardID: cv.cardID };
  return { kind: 'chess', name: cv.chessName != null ? cv.chessName : cv.name, goodsID: cv.goodsID != null ? cv.goodsID : cv.id, cardID: cv.cardID };
}

export function fingerprint(cards) {
  return cards.map((c) => `${c.cardID != null ? c.cardID : ''}|${c.goodsID != null ? c.goodsID : (c.id != null ? c.id : '')}`).join(';');
}

// ---- runner（事务/队列 §5）----
export function createRunner(deps) {
  const Runtime = deps.Runtime;
  const Config = deps.Config || { get() { return null; } };
  const Debug = deps.Debug || { info() {}, warn() {}, error() {}, event() {} };
  const clock = deps.clock || { setTimeout: (fn, ms) => setTimeout(fn, ms), clearTimeout: (h) => clearTimeout(h), now: () => Date.now() };
  const state = { busy: false, generation: 0, current: null, sourceSpellID: null, sourceExpiresAt: 0, timers: [] };

  function clearTimers() { state.timers.forEach((h) => clock.clearTimeout(h)); state.timers = []; }
  function reset() { clearTimers(); state.busy = false; state.current = null; state.generation++; }
  function markSource(spellID) { state.sourceSpellID = String(spellID); state.sourceExpiresAt = clock.now() + 15000; }
  function invalidate() { clearTimers(); state.busy = false; state.current = null; state.generation++; }

  function handleSelect() {
    if (state.busy) return;
    const cards = Runtime.waitSelectCards && Runtime.waitSelectCards();
    if (!cards || cards.length === 0) return;
    const norm = cards.map(normalizeCandidate).filter(Boolean);
    if (norm.length === 0) return;
    const kind = norm[0].kind;
    const cfg = normalizeConfig(Config.get(GM_KEY, null));
    const pri = activePriority(cfg);
    const special = kind === 'chess' && state.sourceSpellID && clock.now() < state.sourceExpiresAt && SPECIAL_CHESS_SPELLS[state.sourceSpellID];
    const chosen = pick(norm, pri, { special, hiddenChessNames: pri.hiddenChessNames, hiddenSpellIds: pri.hiddenSpellIds });
    if (!chosen) { Debug.warn('[reward] 候选全非法，不提交'); return; }
    const fp = fingerprint(norm);
    if (state.current && state.current.fp === fp && state.current.submitted) { Debug.info('[reward] 相同指纹已提交，跳过'); return; }
    state.busy = true;
    state.current = { fp, chosen, generation: state.generation, submitted: true };
    const id = chosen.goodsID != null ? chosen.goodsID : chosen.id;
    Debug.event('selection_called', { fp, id, special });
    Runtime.submitReward(id, false);
    const to = clock.setTimeout(() => {
      if (state.current && state.current.generation === state.generation && !state.current.done) {
        Debug.warn('[reward] 选择超时未确认，保留锁'); state.current.timeout = true;
      }
    }, 2000);
    state.timers.push(to);
  }

  function handleResponse(payload) {
    if (!state.current) return;
    if (payload && payload.isPreselection) { Debug.info('[reward] 预选回包，不完成'); return; }
    const ch = state.current.chosen;
    const match = payload && (
      (ch.goodsID != null && String(payload.goodsID) === String(ch.goodsID)) ||
      (ch.id != null && (String(payload.spellID) === String(ch.id) || String(payload.cardID) === String(ch.id)))
    );
    if (!match) return;
    if (state.current.generation !== state.generation) { Debug.info('[reward] 旧上下文回包，忽略'); return; }
    finish('response_matched');
  }

  function finish(reason) {
    if (!state.current) return;
    state.current.done = true;
    Debug.event('selection_finished', { reason, fp: state.current.fp });
    clearTimers();
    state.busy = false;
    state.current = null;
    state.generation++;
    if (state.sourceSpellID) { state.sourceSpellID = null; state.sourceExpiresAt = 0; }
  }

  function attach() { Runtime.on('RESP_CHOOSE_CARD', runner, handleResponse); }
  function detach() { Runtime.off('RESP_CHOOSE_CARD', runner, handleResponse); }

  const runner = { handleSelect, handleResponse, markSource, invalidate, reset, attach, detach, getState: () => state };
  return runner;
}

// ---- 插件入口 ----
export default {
  id: 'select3-reward',
  name: '武将/锦囊三选一',
  enabled: true,
  load(ctx) {
    if (!ctx.Runtime) return { unload() {} };
    const runner = createRunner({ Runtime: ctx.Runtime, Config: ctx.Config, Debug: ctx.Debug });
    const onUpdate = () => runner.handleSelect();
    ctx.Runtime.on('UI_UPDATE_SELECT_CARD', runner, onUpdate);
    ctx.Runtime.on('NOTIFY_START_SELECT', runner, onUpdate);
    runner.attach();
    return {
      unload() {
        ctx.Runtime.off('UI_UPDATE_SELECT_CARD', runner, onUpdate);
        ctx.Runtime.off('NOTIFY_START_SELECT', runner, onUpdate);
        runner.detach();
      },
    };
  },
};

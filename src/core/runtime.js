// src/core/runtime.js
// 游戏运行时适配层（规范 §2）。所有对局协议/字段访问的唯一出口。
// 取证来源：docs/RUNTIME_NOTES.md（官方 all_tavernchess_classes.txt）。
// 环境：Tampermonkey 或 MV3 content script(world:MAIN)，游戏全局挂在 window 上。

const G = () => (typeof unsafeWindow !== 'undefined' ? unsafeWindow : window);
const gname = (n) => G()[n];

// 递归在 Laya 场景树里找 TavernChessGameScene 节点，再取其 .manager（取证 §10.6）
function findManager(sceneRoot) {
  const Laya = gname('Laya');
  if (!Laya || !Laya.stage) return null;
  const walk = (node) => {
    if (!node || !node._children) return null;
    for (const c of node._children) {
      if (c && c.constructor && c.constructor.name === 'TavernChessGameScene') return c;
      const found = walk(c);
      if (found) return found;
    }
    return null;
  };
  const scene = walk(sceneRoot || Laya.stage);
  return scene && scene.manager ? scene.manager : null;
}

const Runtime = {
  getManager: () => findManager(),
  getContext: () => gname('TavernChessGameContext'),
  getConfiger: () => { const C = gname('TavernChessConfiger'); return C ? C.GetInstance() : null; },
  isReady: () => !!findManager(),

  // 事件常量名（真实值由 _evtConst 解析为 TavernChessGameManager.X）。取证 §8.5
  EVT: {
    RESP_CHOOSE_CARD: 'RESP_CHOOSE_CARD', RESP_CHOOSE_EQUIP: 'RESP_CHOOSE_EQUIP', RESP_CHOOSE_SKILL: 'RESP_CHOOSE_SKILL',
    UI_UPDATE_SELECT_CARD: 'UI_UPDATE_SELECT_CARD', NOTIFY_START_SELECT: 'NOTIFY_START_SELECT', RESP_SELECT_GENERAL: 'RESP_SELECT_GENERAL', ANI_SELECT_GENERAL_SHOW_LIST: 'ANI_SELECT_GENERAL_SHOW_LIST',
    UI_UPDATE_SELECT_BTN: 'UI_UPDATE_SELECT_BTN', SELECT_CHESS_FAIL: 'SELECT_CHESS_FAIL',
    ANI_SHOP_BUY: 'ANI_SHOP_BUY', ANI_SHOP_REFRESH: 'ANI_SHOP_REFRESH', UI_OPEN_SHOP: 'UI_OPEN_SHOP',
    UI_UPDATE_SHOP_LEVEL: 'UI_UPDATE_SHOP_LEVEL', UI_UPDATE_SHOP_LEVELUP_COST: 'UI_UPDATE_SHOP_LEVELUP_COST',
    UI_UPDATE_SHOP_REFRESH_COST: 'UI_UPDATE_SHOP_REFRESH_COST', UI_UPDATE_SHOP_LOCK: 'UI_UPDATE_SHOP_LOCK',
    UI_UPDATE_FREE_REFRESH_END_TIME: 'UI_UPDATE_FREE_REFRESH_END_TIME', UI_UPDATE_COIN_NUM: 'UI_UPDATE_COIN_NUM',
    UI_UPDATE_HAND_CARD: 'UI_UPDATE_HAND_CARD', UI_UPDATE_HAND_CARD_LIMIT: 'UI_UPDATE_HAND_CARD_LIMIT',
    UI_UPDATE_HAND_CARD_NUM: 'UI_UPDATE_HAND_CARD_NUM', UI_UPDATE_SELF_HP: 'UI_UPDATE_SELF_HP', UI_UPDATE_DISPERSION_COIN: 'UI_UPDATE_DISPERSION_COIN',
    ANI_CHESS_COMPOSITE: 'ANI_CHESS_COMPOSITE', ANI_CHESS_RECYCLE: 'ANI_CHESS_RECYCLE', ANI_LINE_UP: 'ANI_LINE_UP',
    ANI_LINE_UP_SELECT_FAIL: 'ANI_LINE_UP_SELECT_FAIL', ANI_ADD_HAND_CARD: 'ANI_ADD_HAND_CARD', DELETE_CHESS: 'DELETE_CHESS', ADD_CHESS: 'ADD_CHESS',
    ANI_SANLIAN_TIP: 'ANI_SANLIAN_TIP',
    UI_UPDATE_GENERAL: 'UI_UPDATE_GENERAL', UI_UPDATE_GENERAL_ENEMY: 'UI_UPDATE_GENERAL_ENEMY',
    GENERAL_SKILL_TRIGGER: 'GENERAL_SKILL_TRIGGER', GENERAL_INJURED: 'GENERAL_INJURED', UI_UPDATE_DING_QING_TURN: 'UI_UPDATE_DING_QING_TURN', GENERAL_DOUBLE_HURT: 'GENERAL_DOUBLE_HURT',
    CHESS_USE_SPELL: 'CHESS_USE_SPELL', RESP_CHESS_SPELL_USE: 'RESP_CHESS_SPELL_USE', SPELL_EFFECT_TURNS_UPDATE: 'SPELL_EFFECT_TURNS_UPDATE', CANCEL_USE_SPELL: 'CANCEL_USE_SPELL', RESP_CHESS_QUERY_BUFF: 'RESP_CHESS_QUERY_BUFF', JUDGE_SKILL: 'JUDGE_SKILL',
    RESP_CHESS_FOLLOW_UP: 'RESP_CHESS_FOLLOW_UP', NOTIFY_QUYUAN_QUESTIONINFO: 'NOTIFY_QUYUAN_QUESTIONINFO',
    GAME_PHASE_CHANGED: 'GAME_PHASE_CHANGED', GAME_START: 'GAME_START', GAME_ON_STAGE: 'GAME_ON_STAGE',
    ANI_ROUND_END: 'ANI_ROUND_END', AUTO_JUMP_GAME_EVENT: 'AUTO_JUMP_GAME_EVENT', JUMP_GAME_EVENT: 'JUMP_GAME_EVENT', JUMP_GAME_OVER: 'JUMP_GAME_OVER', END_RECRUIT_JUMP: 'END_RECRUIT_JUMP',
    UPDATE_REPLAY_INFO: 'UPDATE_REPLAY_INFO', UI_UPDATE_LINEUP_TIP: 'UI_UPDATE_LINEUP_TIP',
  },

  _evtConst(evt) {
    const Mgr = gname('TavernChessGameManager');
    if (!Mgr) return evt;
    return Mgr[evt] !== undefined ? Mgr[evt] : evt;
  },

  on(evt, ctx, fn) {
    const mgr = this.getManager();
    if (!mgr || typeof mgr.on !== 'function') return false;
    mgr.on(this._evtConst(evt), ctx, fn);
    return true;
  },
  off(evt, ctx, fn) {
    const mgr = this.getManager();
    if (!mgr || typeof mgr.off !== 'function') return;
    mgr.off(this._evtConst(evt), ctx, fn);
  },
  once(evt, ctx, fn) {
    const mgr = this.getManager();
    if (!mgr || typeof mgr.on !== 'function') return false;
    const c = this._evtConst(evt);
    const wrapper = (...a) => { mgr.off(c, ctx, wrapper); fn(...a); };
    mgr.on(c, ctx, wrapper);
    return true;
  },

  waitSelectCards() { const m = this.getManager(); return m ? m.WaitSelectCards : null; },
  waitSelectEquiments() { const m = this.getManager(); return m ? m.WaitSelectEquiments : null; },

  // ---- 提交方法（签名取证自 RUNTIME_NOTES §1-§7）----
  reqSelectOtherChess(goodsID, isPreselection = false) {
    const m = this.getManager();
    if (m && typeof m.ReqSelectOtherChess === 'function') m.ReqSelectOtherChess(goodsID, isPreselection);
  },
  reqChessSelectSpellChess(goodsID, isPreselection = false) {
    const m = this.getManager();
    if (m && typeof m.ReqChessSelectSpellChess === 'function') m.ReqChessSelectSpellChess(goodsID, isPreselection);
  },
  reqChessChooseGeneral(generalID, isPreselection = false) {
    const m = this.getManager();
    if (m && typeof m.ReqChessChooseGeneral === 'function') m.ReqChessChooseGeneral(generalID, isPreselection);
  },
  shopRefresh() {
    const m = this.getManager();
    if (m && typeof m.ShopRefresh === 'function') m.ShopRefresh();
  },
  reqShopBuyChess(goodsID) {
    const m = this.getManager();
    if (m && typeof m.ReqShopBuyChess === 'function') m.ReqShopBuyChess(goodsID);
  },
  reqShopLevelUp() {
    const m = this.getManager();
    if (m && typeof m.ReqShopLevelUp === 'function') m.ReqShopLevelUp(m.TableID);
  },
  reqShopRecycleChess(goodsID) {
    const m = this.getManager();
    if (m && typeof m.ReqShopRecycleChess === 'function') m.ReqShopRecycleChess(goodsID);
  },
  reqChessDeleteSpell(spellID) {
    const m = this.getManager();
    if (m && typeof m.ReqChessDeleteSpell === 'function') m.ReqChessDeleteSpell(spellID);
  },
  reqChessComposite(arr) {
    const m = this.getManager();
    if (m && typeof m.ReqChessComposite === 'function') m.ReqChessComposite(arr);
  },
  reqChessLineUp() {
    const m = this.getManager();
    if (m && typeof m.ReqChessLineUp === 'function') m.ReqChessLineUp();
  },
  // 按当前选择上下文分流（取证 §1）：有 userID 走武将奖励入口，否则走 spell 入口
  submitReward(goodsID, isPreselection = false) {
    const m = this.getManager();
    if (!m) return false;
    if (m.currentSelectOtherChessUserID) { this.reqSelectOtherChess(goodsID, isPreselection); return true; }
    if (typeof m.currentSelectChessOrSpellIndex === 'number' && m.currentSelectChessOrSpellIndex >= 0) { this.reqChessSelectSpellChess(goodsID, isPreselection); return true; }
    this.reqChessSelectSpellChess(goodsID, isPreselection);
    return true;
  },

  // 装备选择（天降神兵 §6）：单入口
  reqChessChooseEquipment(equipmentID, isPreselection = false) {
    const m = this.getManager();
    if (m && typeof m.ReqChessChooseEquipment === 'function') m.ReqChessChooseEquipment(equipmentID, isPreselection);
  },
  submitEquip(equipmentID, isPreselection = false) { this.reqChessChooseEquipment(equipmentID, isPreselection); return true; },

  // 天降神兵用牌（§6.4）：基础/有效 ID 均为 311061 的锦囊
  reqChessUseSpell(goodsID, ids = []) {
    const m = this.getManager();
    if (m && typeof m.ReqChessUseSpell === 'function') m.ReqChessUseSpell(goodsID, ids);
  },

  // 实际已装备 / 回合 / 禁用类别（§6.1 解锁与完成判定用）
  getEquipments() {
    const m = this.getManager();
    if (!m) return [];
    if (Array.isArray(m.Equipments)) return m.Equipments;
    if (Array.isArray(m.equipments)) return m.equipments;
    return [];
  },
  getRound() {
    const m = this.getManager();
    if (!m) return 0;
    if (m.Round != null) return m.Round;
    const Ctx = this.getContext();
    return Ctx && Ctx.Round != null ? Ctx.Round : 0;
  },
  getDisableEquipTypes() {
    const m = this.getManager();
    if (!m) return [];
    if (Array.isArray(m.DisableEquipTypes)) return m.DisableEquipTypes;
    return [];
  },

  // 商店状态读取（§3：字段众多，按候选名兜底；缺失则降级为 null 并交由 UI 告警）
  getShopState() {
    const m = this.getManager();
    if (!m) return null;
    const num = (...keys) => { for (const k of keys) { const v = m[k]; if (v != null && typeof v === 'number') return v; } return null; };
    const raw = m.currnetShopArr || m.currentShopArr || m.shopGoods || [];
    const handChess = m.HandChess || (m.SelfInfo && m.SelfInfo.HandChess) || (m.selfChessPlayer && m.selfChessPlayer.HandChess) || [];
    const lvl = num('ShopLevel', 'shopLevel');
    const lvlFallback = m.SelfInfo && m.SelfInfo.shopLevel != null ? m.SelfInfo.shopLevel
      : (m.selfChessPlayer && m.selfChessPlayer.shopLevel != null ? m.selfChessPlayer.shopLevel : null);
    const hl = num('HandCardLimit', 'handCardLimit');
    const hlFallback = m.SelfInfo && m.SelfInfo.HandCardLimit != null ? m.SelfInfo.HandCardLimit
      : (m.selfChessPlayer && m.selfChessPlayer.HandCardLimit != null ? m.selfChessPlayer.HandCardLimit : null);
    return {
      coins: num('restCoin', 'coins', 'coin', 'gold'),
      level: lvl != null ? lvl : lvlFallback,
      levelUpCost: num('shopLevelUpCost', 'levelUpCost', 'shopLevelupCost'),
      rerollCost: num('shopRefreshCost', 'shopRerollCost', 'rerollCost'),
      handLimit: hl != null ? hl : hlFallback,
      handCount: Array.isArray(handChess) ? handChess.length : null,
      shopCards: Array.isArray(raw) ? raw.map((c) => this.normalizeShopCard(c)) : [],
    };
  },
  normalizeShopCard(c) {
    if (c == null) return { id: null, cost: null };
    const cv = c.CardVO || c.goods || c;
    const id = cv != null ? (cv.goodsID != null ? cv.goodsID : (cv.id != null ? cv.id : c.goodsID)) : c;
    const cost = cv != null ? (cv.cost != null ? cv.cost : (cv.coin != null ? cv.coin : (cv.price != null ? cv.price : null))) : null;
    return { id: Number(id), cost: Number(cost) };
  },

  // 手牌武将（遣散/卖牌 §5）：按候选字段名兜底读取，归一化 {id, cost, locked}
  getHandChess() {
    const m = this.getManager();
    if (!m) return [];
    const arr = m.HandChess || (m.SelfInfo && m.SelfInfo.HandChess) || (m.selfChessPlayer && m.selfChessPlayer.HandChess) || [];
    return Array.isArray(arr) ? arr.map((c) => this.normalizeChess(c)) : [];
  },
  normalizeChess(c) {
    if (c == null) return { id: null, cost: null, locked: false };
    const cv = c.CardVO || c.goods || c;
    const id = cv != null ? (cv.goodsID != null ? cv.goodsID : (cv.id != null ? cv.id : c.goodsID)) : c;
    const cost = cv != null ? (cv.cost != null ? cv.cost : (cv.coin != null ? cv.coin : (cv.price != null ? cv.price : null))) : null;
    const locked = !!(cv && (cv.isLocked != null ? cv.isLocked : (c.isLocked != null ? c.isLocked : false)));
    return { id: Number(id), cost: Number(cost), locked: !!locked };
  },

  // 候选主公（主公选择 §6）：优先读选将窗 DataProvider，回退 manager.GeneralList
  getLordCandidates() {
    const win = this._findGeneralWindow();
    if (win) {
      const dp = (win.panel && win.panel.DataProvider) || win.DataProvider;
      if (Array.isArray(dp) && dp.length) return this.normalizeLords(dp);
    }
    const m = this.getManager();
    if (m) {
      const arr = m.GeneralList || m.generalList || m.Candidates || m.candidates;
      if (Array.isArray(arr) && arr.length) return this.normalizeLords(arr);
    }
    return [];
  },
  normalizeLords(raw) {
    return (raw || []).map((c) => ({
      id: c.GeneralID != null ? c.GeneralID : (c.id != null ? c.id : c),
      name: c.GeneralName != null ? c.GeneralName : (c.name != null ? c.name : ''),
    })).filter((c) => c.id != null);
  },
  _findGeneralWindow() {
    const Laya = gname('Laya');
    if (!Laya || !Laya.stage) return null;
    const walk = (node) => {
      if (!node || !node._children) return null;
      for (const c of node._children) {
        if (c && c.constructor && c.constructor.name === 'TavernChessSelectGeneralWindow') return c;
        const r = walk(c);
        if (r) return r;
      }
      return null;
    };
    return walk(Laya.stage);
  },

  // ---- 加速（取证 §7）----
  setSpeed(v) {
    const Ctx = this.getContext();
    if (!Ctx) return;
    try { Ctx.Speed = v; } catch (e) {}
    try { Ctx.accelerateSpeed = v; } catch (e) {}
  },
  getSpeed() { const Ctx = this.getContext(); return Ctx ? Ctx.Speed : null; },

  // ---- 配置读取 ----
  getCardByCardID(id) { const c = this.getConfiger(); return c && c.GetCardByCardID ? c.GetCardByCardID(id) : null; },
  getSpellBySpellID(id) { const c = this.getConfiger(); return c && c.GetSpellBySpellID ? c.GetSpellBySpellID(id) : null; },
  globalData() { const c = this.getConfiger(); return c && c.GlobalData ? c.GlobalData : null; },
};

export default Runtime;
export { Runtime };

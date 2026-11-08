# 运行时取证（RUNTIME_NOTES.md）

> **用途**：重写时唯一权威的"对局运行时协议/字段地图"。拆 `sgs_bridge.user.js` 成 `src/plugins/*` 前，
> 所有 `req`/`event`/`字段` 必须来此处核对，杜绝凭记忆臆测。
> **权威来源**：`参考_sgs_forward_looking-main/source/all_tavernchess_classes.txt`（官方游戏源码，全部 TavernChess 类）
> + `参考_sgs_forward_looking-main/1/TavernChessConfig.json`（S11 线上 44 张配置表）。
> 配置缺失项可查 `参考_sgs_forward_looking-main/配置表探查/`（1281 份 .md 探查）。
> **wasmtime 已装（48.0.0）**，若需重新解密 `*.sgs` 用 `参考_sgs_forward_looking-main/sgs_decrypt.py <x.sgs> --wasm 参考_sgs_forward_looking-main/resc`。

---

## 0. 管理类地图（9 个 Manager）
`TavernChessGameManager`(主) · `TavernChessLineupManager` · `TavernChessZhuGongSkillManager`(主公技)
· `TavernChessGameGuideManager`(教程/新手) · `TavernChessGameReplayManager`(复盘) · `TavernChessManager`
· `TavernChessOpenManager` · `TavernChessTableManager` · `TavernChessValuableBookManager`

- 游戏全局上下文：`TavernChessGameContext`（静态 `Manager` 指向主 manager；`Speed`/`LineUpArr`/`LineUpChess` 皆在此）。
- 配置读取统一走 `TavernChessConfiger.GetInstance()`（`GetCardByCardID` / `GetSpellBySpellID` / `GlobalData` 等）。

## 1. 武将/锦囊 三选一（规范 §4-5）
来源：官方源码 `TavernChessGameManager`
- 候选读取：`get WaitSelectCards(){return this.waitSelectCards}`；候选数组元素 `{CardVO:TavernChessConfiger.GetInstance().GetCardByCardID(t), ServerInfo:null, isSelect:perSelectSpellID==t}`（由 `SelectTavernSpellList(e)` 构建，`e.ProtoData.perSelectSpellID` / `e.ProtoData.spellIDs`）。
- 提交入口有**两个**（按当前选择上下文分流）：
  - `ReqSelectOtherChess(e,t)` → `CMSG_CREQSELECTOTHERCHESS {tableID, userID:currentSelectOtherChessUserID, goodsID:e, isPreselection:t}`（当 `currentSelectOtherChessUserID` 有值时）。
  - `ReqChessSelectSpellChess(e,t)` → `CMSG_CREQCHESSSELECTSPELLCHESS {tableID, goodsID:e, isPreselection:t}`（当 `currentSelectChessOrSpellIndex>=0`，由 `ReqChessSelectSpellID` 经 `ReqChessSkillSelectSpellOrChess` 进入）。
- 响应：`onRespChessSelectSpellChess(e)` → 校验 errCode，取 `ProtoData.chess`/`isPreselection`；触发 `this.event(TavernChessGameManager.RESP_CHOOSE_CARD,[chess,isPreselection])`（预览/旁观）或 `SelfInfo.AddHandChess(chess)`。
- 规范命名对照：规范 `ReqSelectOtherChess`≈`CMSG_CREQSELECTOTHERCHESS`；规范 `RESP_CHOOSE_CARD`≈官方 `RESP_CHOOSE_CARD` 事件（在 `onRespChessSelectSpellChess` 内触发，非 `OnResp...`）。
- 关键字段：`TableID`、`currentSelectOtherChessUserID`、`currentSelectChessOrSpellIndex`、`isPreselection`（预选/锁定）。

## 2. 装备 三选一（规范 §6）
来源：官方源码 `TavernChessGameManager`
- 候选读取：`get WaitSelectEquiments(){return this.waitSelectEquiments}`，`set WaitSelectEquiments(e){this.waitSelectEquiments=e; this.event(TavernChessGameManager.UI_UPDATE_SELECT_CARD)}`。
- 提交：`ReqChessChooseEquipment(e,t)` → `proxy.SendProto(ProtoBufId.CMSG_CREQCHESSCHOOSEEQUIPMENT,{tableID:this.TableID, equipmentID:e, isPreselection:t})`，前置 `TavernChessGameContext.IsViewer||` 保护。
- 响应：`onRespChessChooseEquipment(e)`（官方源码确有此 handler）。结算事件：`this.event(TavernChessGameManager.RESP_CHOOSE_EQUIP,[i,s])` 与 `event(RESP_CHOOSE_EQUIP,i)`。
- 规范命名对照：规范 `ReqChessChooseEquipment`≈`CMSG_CREQCHESSCHOOSEEQUIPMENT`；规范 `RESP_CHOOSE_EQUIP`≈官方 `RESP_CHOOSE_EQUIP` 事件。

## 3. 购买 / 刷新 / 升级商店
来源：官方源码 `TavernChessGameManager`
- 购买：`ReqShopBuyChess(e)` → 前置 `if(!this.CanOperate)return`；`if(this.HandChess.length>=this.HandCardLimit)return ShowErrorTextPrompt(TAVERN_CHESS_SHOP_3)`；成功发 `event(ANI_SHOP_BUY, ...)`。**注意**：`e` 为购买目标（goodsID/序号），`HandCardLimit` 来自 `selfChessPlayer`。
- 升级商店：`ReqShopLevelUp(e)` → 取 `selfChessPlayer.shopLevel+1` / `shopLevelUpCost` / `coin-cost`；发 `CMSG_CNOTIFYCHESSDATAUPDATE {tableID, chessDataUpdateTyp:4, useSkillTyp:1, skillInfo:{spellList:[{spells:[...]}]}}`。
- 刷新：`ShopRefresh()`（**非 `ReqShopRefresh`**）→ `getShopGoods(!0)` → `currnetShopArr`；扣 `shopRefreshCost`；发 `WBMgr.GetInstance().SendProtoToSelf(ProtoBufId.CMSG_CRESPSHOPREFRESHCHESS,{shopGoods, restCoin})`。购买发 `CMSG_CRESPSHOPBUYCHESS {goodsID, restCoin, buyChess}`。

## 4. 合成 / 三连
来源：官方源码 `TavernChessGameManager`
- 合成：`ReqChessComposite(e)` → 仅 `Phase==TavernChessPhaseType.InRecruit && !IsViewer && !BShowSelfEquipWindow` 时生效；`e` 为 `{goodsID}` 数组。同文件有 `checkSanLianReq`（三连检测，bridge 曾 patch 它做"禁用三连"）。
- 三连动画事件：`TavernChessGameManager.ANI_SANLIAN_TIP`（bridge 监听做合成特效/跳过）。

## 5. 上阵 / 遣散（卖）
来源：官方源码 `TavernChessGameManager`
- 上阵：`ReqChessLineUp(...)` 关联 `TavernChessGameContext.LineUpArr` / `LineUpChess`（上阵前布置数组，落点/选人/当先时序都走这条）。
- 卖武将：`ReqShopRecycleChess(e)`（e=goodsID）。
- 卖锦囊：`ReqChessDeleteSpell(e)`（e=spellID）。**重要分流**：武将与锦囊卖出走**不同协议**，发错服务器静默不处理（bridge 实机踩坑，见 §6）。

## 6. 主公
来源：官方源码 `TavernChessGameManager`
- `ReqChessChooseGeneral(e,t)` → `proxy.SendProto(ProtoBufId.CMSG_CREQCHESSCHOOSEGENERAL,{tableID:this.TableID, generalID:e, ...})`，前置 `CheckHaveGeneralByGeneralID(e)` / `CheckGeneralAlive` / `CheckGeneralHasOtherSureSelect`。主公技走 `TavernChessZhuGongSkillManager`。

## 7. 加速 / 跳过战斗
来源：官方源码 `TavernChessGameContext` + `TavernChessGameManager`
- 加速（基础速度）：`TavernChessGameContext.Speed` getter 返回 `accelerateSpeed||speed`；**setter `static set Speed(e){this.speed=e}`**（改基础速度）。战斗内加速另走 `this.accelerateSpeed=...`（源码在战斗加速逻辑里 `accelerateSpeed=0` 复位）。
- 跳过战斗：未定位到 `ReqChessSkipBattle` / `onSkipBattle` / `SkipBattle(` 等公开方法；战斗时长由 `GlobalData.MaxBattleTime` / `SkipBattleTime` 控制（`skinBattleTime=startTime+SkipBattleTime`）。**跳过战斗具体方法仍待补抓**（可能在 `WBMgr` 发送的某 proto 或 phase 切换，非 manager 公开方法）。

## 8. 随征（FollowUp）
- 配置：`chessBuffFollowUpConf`（BuffFollowUp 表）；`TavernChessGameContext` 解析 `ChessBuffFollowUpConf`。随征是"使用某牌后自动再选一次"类逻辑，与 §4/§5 二段挑牌同源。

---

## 8.5 Manager 事件面（runtime 适配层订阅清单，来自官方源码枚举 128 个常量）
下列 `TavernChessGameManager.X` 常量可直接 `manager.on(X, ctx, fn)` 订阅。与功能相关的：
- 三选一/选择：`RESP_CHOOSE_CARD`、`RESP_CHOOSE_EQUIP`、`RESP_CHOOSE_SKILL`、`UI_UPDATE_SELECT_CARD`、`NOTIFY_START_SELECT`、`RESP_SELECT_GENERAL`、`UI_UPDATE_SELECT_BTN`、`SELECT_CHESS_FAIL`、`ANI_SELECT_GENERAL_SHOW_LIST`、`ANI_SELECT_GENERAL_FLY`
- 商店：`ANI_SHOP_BUY`、`ANI_SHOP_REFRESH`、`UI_OPEN_SHOP`、`UI_UPDATE_SHOP_LEVEL`、`UI_UPDATE_SHOP_LEVELUP_COST`、`UI_UPDATE_SHOP_REFRESH_COST`、`UI_UPDATE_SHOP_LOCK`、`UI_UPDATE_FREE_REFRESH_END_TIME`、`UI_UPDATE_COIN_NUM`
- 手牌/血：`UI_UPDATE_HAND_CARD`、`UI_UPDATE_HAND_CARD_LIMIT`、`UI_UPDATE_HAND_CARD_NUM`、`UI_UPDATE_SELF_HP`、`UI_UPDATE_DISPERSION_COIN`
- 合成/上阵/卖：`ANI_CHESS_COMPOSITE`、`ANI_CHESS_RECYCLE`、`ANI_LINE_UP`、`ANI_LINE_UP_SELECT_FAIL`、`ANI_ADD_HAND_CARD`、`DELETE_CHESS`、`ADD_CHESS`
- 三连/动画：`ANI_SANLIAN_TIP`
- 主公/将：`UI_UPDATE_GENERAL`、`UI_UPDATE_GENERAL_ENEMY`、`GENERAL_SKILL_TRIGGER`、`GENERAL_INJURED`、`ZHONGKUI_GENERAL_SKILL_TIGGER`、`LIBAI_GENERAL_SKILL_TIGGER`、`JUETAO_GENERAL_SKILL_TRIGGER`、`UI_UPDATE_DING_QING_TURN`(狄青)、`GENERAL_DOUBLE_HURT`
- 技能/锦囊：`CHESS_USE_SPELL`、`RESP_CHESS_SPELL_USE`、`SPELL_EFFECT_TURNS_UPDATE`、`CANCEL_USE_SPELL`、`RESP_CHESS_QUERY_BUFF`、`JUDGE_SKILL`
- 随征：`RESP_CHESS_FOLLOW_UP`、`NOTIFY_QUYUAN_QUESTIONINFO`
- 阶段/对局：`GAME_PHASE_CHANGED`、`GAME_START`、`GAME_ON_STAGE`、`ANI_ROUND_END`、`AUTO_JUMP_GAME_EVENT`、`JUMP_GAME_EVENT`、`JUMP_GAME_OVER`、`END_RECRUIT_JUMP`
- 复盘：`UPDATE_REPLAY_INFO`
- 阵容/队伍：`UI_UPDATE_LINEUP_TIP`、`TEAM_CHESS_*`

---

## 9. 规范命名 ↔ 官方命名 对照表（重写时必须对齐）
| 规范/桥 用语 | 官方真实 |
|---|---|
| `WaitSelectCards`（候选） | `manager.waitSelectCards`（getter `WaitSelectCards`） |
| `ReqSelectOtherChess` | `CMSG_CREQSELECTOTHERCHESS {tableID,userID,goodsID,isPreselection}` |
| `RESP_CHOOSE_CARD` | `onRespChessSelectSpellChess` handler + `RESP_CHOOSE_CARD` 事件 |
| `WaitSelectEquiments` | `manager.waitSelectEquiments`（getter `WaitSelectEquiments`） |
| `ReqChessChooseEquipment` | `CMSG_CREQCHESSCHOOSEEQUIPMENT {tableID,equipmentID,isPreselection}` |
| `RESP_CHOOSE_EQUIP` | `onRespChessChooseEquipment` handler + `RESP_CHOOSE_EQUIP` 事件 |
| `ReqChessChooseGeneral` | `CMSG_CREQCHESSCHOOSEGENERAL {tableID,generalID,...}` |
| 卖武将 | `ReqShopRecycleChess(goodsID)` |
| 卖锦囊 | `ReqChessDeleteSpell(spellID)` |
| 刷新商店 | `ShopRefresh()` → `CMSG_CRESPSHOPREFRESHCHESS {shopGoods, restCoin}` |
| 购买 | `ReqShopBuyChess(goodsID)` → `CMSG_CRESPSHOPBUYCHESS`；受 `CanOperate`/`HandCardLimit` 门控 |
| 奖励响应 | `onRespChessSelectSpellChess` → `RESP_CHOOSE_CARD [chess,isPreselection]` |
| 加速 | `TavernChessGameContext.Speed`(setter=`this.speed`) / 战斗内 `accelerateSpeed` |
| 三连检测 | `checkSanLianReq`；动画 `ANI_SANLIAN_TIP` |

## 10. 实机经验坑（来自 bridge 实测，官方源码没有，重写必须保留）
> 这些不是协议名，是"真机才暴露的行为"，务必在新架构里保留对应处理，并逐一复盘确认。
1. **卖出协议分流**：武将 `ReqShopRecycleChess(goodsID)` vs 锦囊 `ReqChessDeleteSpell(spellID)`，发错服务器静默不处理（"包发了但它还在手牌"）。
2. **三连动画卡忙**：合成动画期间 `IsPlayCompositeAni=true`/`CanOperate=false`；跳过动画须识别合成节点（按 constructor.name 含 Composite/SanLian）并**强制复位** `IsPlayCompositeAni=false`/`CanOperate=true`，否则后续全卡。
3. **陈珪三段链时序**：上阵陈珪→当先收回手牌 B→按 goodsID 找 B→上阵 B（丁奉则指定技能目标 C）或遣散陈珪；超时 1.2s 停牌。
4. **狄青**：三类选择窗口之一，走 `ReqDiQingSelectSkill`（真机实测，非通用 Req）。
5. **挑牌两步包**：部分锦囊（草船/珠联/天降神兵）用完还要再选一次，须走 `pickOne`/`_scPickOption` 那段二段挑牌。
6. **Manager 获取**：递归 `Laya.stage._children` 找 `constructor.name==='TavernChessGameScene'` 的节点再取 `.manager`；大厅/加载中拿不到，须等进对局。

---
> 第 2 批已补抓（2026-11-04）：刷新=`ShopRefresh()`→`CMSG_CRESPSHOPREFRESHCHESS`；奖励响应=`onRespChessSelectSpellChess`→`RESP_CHOOSE_CARD`；速度 setter=`TavernChessGameContext.Speed`+`accelerateSpeed`；装备候选来自 `onNotifyChessSelectEquiment`→`WaitSelectEquiments`；并枚举 `TavernChessGameManager` 全部 128 事件常量（见 §8.5）。
> 仍待补抓：跳过战斗的具体公开方法（仅时长常量，无直接方法）、`onRespChessSelectSpellChess` 在 buff/spell 上下文外的其它入口、`equipmentID` 候选与"用牌来源"的精确关联。

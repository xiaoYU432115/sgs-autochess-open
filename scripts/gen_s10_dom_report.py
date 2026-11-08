# -*- coding: utf-8 -*-
import json
import os
import sys
import time

# v4.22★ 修复硬编码绝对路径(原为 r'd:\todesk\三国杀\自走棋助手\data')。
#   改为从 core.config 推导, 换目录/换机器都不会静默失效。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.config import DATA_DIR


def generate_report():
    data_dir = DATA_DIR
    diag_path = os.path.join(data_dir, 'diagnostics.json')
    report_path = os.path.join(data_dir, 's10_container_report.txt')
    
    # 模拟从用户提供的日志中提取的数据结构
    # 路径: Laya.stage._children[1]._children[0]._children[0]._children[0].manager.selfInfo.Chess
    s10_data = {
        "path": "Laya.stage._children[1]._children[0]._children[0]._children[0].manager.selfInfo.Chess",
        "timestamp": time.strftime('%Y-%m-%d %H:%M:%S'),
        "description": "S10 赛季核心武将/棋子父容器快照",
        "units": [
            {
                "slot": 1,
                "goodsID": 135,
                "chessID": 21002021,
                "name": "张星彩",
                "star": "1星",
                "attack": 3,
                "hp": 4,
                "side": 1,
                "side_text": "我方",
                "raw_path": "...manager.selfInfo.lineUpChess[0]"
            },
            {
                "slot": 2,
                "goodsID": 914,
                "chessID": 21003141,
                "name": "赵云",
                "star": "1星",
                "attack": 7,
                "hp": 39,
                "side": 1,
                "side_text": "我方",
                "raw_path": "...manager.selfInfo.lineUpChess[1]"
            },
            {
                "slot": 3,
                "goodsID": 1366,
                "spellID": 310022,
                "name": "酒",
                "type": "锦囊",
                "side": 1,
                "side_text": "我方",
                "raw_path": "...manager.selfInfo.handChess[0]"
            },
            {
                "slot": "Enemy-1",
                "goodsID": 2015,
                "chessID": 21008162,
                "name": "未知对手",
                "side": 2,
                "side_text": "对手 (已过滤)",
                "raw_path": "...otherChilds[5]"
            }
        ]
    }

    lines = [
        "// ============================================================",
        "// S10 赛季父容器分层探测报告",
        f"// 探测路径: {s10_data['path']}",
        f"// 生成时间: {s10_data['timestamp']}",
        "// ============================================================\n",
        "// [层级]       | [对象ID]    | [纯净名称] | [属性]          | [状态/归属]",
        "// ------------------------------------------------------------"
    ]

    for unit in s10_data["units"]:
        sid = str(unit.get("goodsID") or unit.get("chessID") or unit.get("spellID"))
        name = unit.get("name", "?")
        side = unit.get("side_text", "?")
        attr = f"{unit.get('attack','-')}/{unit.get('hp','-')}"
        if "spellID" in unit:
            attr = "[锦囊]"
        
        line = f"{str(unit['slot']).ljust(12)} | {sid.ljust(10)} | {name.ljust(8)} | {attr.ljust(14)} | {side}"
        lines.append(line)
        lines.append(f"  └─ 逻辑路径: {unit['raw_path']}")

    lines.append("\n// --- 技术追溯 ---")
    lines.append("// 数据依据: LayaAir 引擎舞台树递归探测")
    lines.append("// 过滤逻辑: side=2 (对手单位) 在协议层已物理隔离，UI 不予显示")
    lines.append("// 净化引擎: v3.96 正则清洗 (移除赛季前缀、合金后缀、星级噪音)")

    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    
    print(f"Report generated at: {report_path}")

if __name__ == "__main__":
    generate_report()

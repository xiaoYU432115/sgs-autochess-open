import json
import os
import sys

# v4.22★ 修复硬编码绝对路径: 原来写死 r"d:\todesk\三国杀\自走棋助手\data",
#   项目换盘符/换目录/别人机器上跑就静默失效(脚本只会打印 "Generated ..." 之外什么都不做)。
#   改为从 core.config 推导, 与本项目其他模块保持同一套路径基准。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.config import DATA_DIR

def gen_reports():
    # 1. Tavern Lords Report
    tl_path = os.path.join(DATA_DIR, "tavern_lords.json")
    report_path = os.path.join(DATA_DIR, "tavern_lords_report.txt")
    
    if os.path.exists(tl_path):
        with open(tl_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        lines = ["// Tavern Lords Data Report", "// Format: ID | Name | Description", "-" * 60]
        # Group by ID range
        sorted_ids = sorted(data.keys(), key=lambda x: int(x) if x.isdigit() else 99999)
        
        for lid in sorted_ids:
            item = data[lid]
            name = item.get("n", "?")
            desc = item.get("desc", "").replace("\n", " ")
            lines.append(f"{lid.ljust(10)} | {name.ljust(15)} | {desc}")
            
        with open(report_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print(f"Generated {report_path}")

    # 2. S10 IDs Report
    discovered_path = os.path.join(DATA_DIR, "discovered_ids.json")
    s10_report_path = os.path.join(DATA_DIR, "s10_ids_report.txt")
    
    if os.path.exists(discovered_path):
        with open(discovered_path, "r", encoding="utf-8") as f:
            all_ids = json.load(f)
            
        s10_lines = ["// S10 Discovered IDs Report", "// Format: ID | Pure Name | Star | Season", "-" * 60]
        
        # Filter for S10-like IDs (starts with 2100 or 2000, length >= 7)
        s10_items = []
        for gid, info in all_ids.items():
            if gid.isdigit() and len(gid) >= 7 and (gid.startswith("2100") or gid.startswith("2000")):
                s10_items.append((gid, info))
        
        s10_items.sort(key=lambda x: x[0])
        
        for gid, info in s10_items:
            name = info.get("name", "?")
            star = gid[-1]
            season = "S10" if gid.startswith("2100") else "S?"
            s10_lines.append(f"{gid.ljust(12)} | {name.ljust(15)} | {star}星 | {season}")
            
        with open(s10_report_path, "w", encoding="utf-8") as f:
            f.write("\n".join(s10_lines))
        print(f"Generated {s10_report_path}")

if __name__ == "__main__":
    gen_reports()

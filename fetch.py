#!/usr/bin/env python3
# A股盘后数据抓取 —— 在 GitHub Actions（外网开放）上运行，本地/Claude沙箱跑不了
# 输出 data/quotes.json，供 Claude 通过 raw.githubusercontent 读取
# 只抓两类：1) 自选股快照  2) 板块资金流向（找方向：今日 + 5日，行业 + 概念）

import json, os, traceback
from datetime import datetime
from zoneinfo import ZoneInfo
import akshare as ak

BJ = ZoneInfo("Asia/Shanghai")


def load_watchlist(path="watchlist.txt"):
    codes = []
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            c = line.strip().split("#")[0].strip()      # 去掉 # 注释
            if c.isdigit() and len(c) == 6:
                codes.append(c)
    return codes


def safe(fn, *a, **k):
    try:
        return fn(*a, **k)
    except Exception:
        traceback.print_exc()
        return None


def main():
    out = {
        "updated_at": datetime.now(BJ).strftime("%Y-%m-%d %H:%M:%S %Z"),
        "watchlist": [],
        "errors": [],
    }

    codes = load_watchlist()

    # 1) 自选股快照（一次性取全A，再按代码过滤）
    spot = safe(ak.stock_zh_a_spot_em)
    if spot is None:
        out["errors"].append("stock_zh_a_spot_em failed")
    elif codes:
        try:
            sel = spot[spot["代码"].isin(codes)]
            out["watchlist"] = json.loads(sel.to_json(orient="records", force_ascii=False))
        except Exception as e:
            out["errors"].append(f"watchlist filter: {e}")

    # 2) 板块资金流向（找方向）
    for key, indicator, sector_type in [
        ("industry_today", "今日", "行业资金流"),
        ("industry_5d",    "5日",  "行业资金流"),
        ("concept_today",  "今日", "概念资金流"),
        ("concept_5d",     "5日",  "概念资金流"),
    ]:
        df = safe(ak.stock_sector_fund_flow_rank, indicator=indicator, sector_type=sector_type)
        if df is None:
            out["errors"].append(f"{key} failed")
        else:
            # 该接口默认按主力净流入排序，取前 15
            out[key] = json.loads(df.head(15).to_json(orient="records", force_ascii=False))

    os.makedirs("data", exist_ok=True)
    with open("data/quotes.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print("OK", out["updated_at"],
          "| watchlist:", len(out["watchlist"]),
          "| errors:", out["errors"])


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# 读 data/quotes.json,把四步 funnel 机械地过一遍,输出结论。
# 不联网、不碰东财,只消费已抓好的 json。可本地跑,也可 Claude 侧跑。
#
# 四步:
#   1) 找方向  : 今日+5日双确认 + 主力净占比质量闸
#   2) 挑分支  : 真方向里挑低位(5日涨幅不透支)子分支
#   3) 选个股  : drilldown 成分股套 B型筛(需 drilldown.txt 有料)
#   4) 认四阶段: 用现成字段给 A型透支打标(K线形态仍需人工)
import json
import sys

# ---- 可调阈值(想松紧直接改这里)----
Q_GOOD = 2.0      # 今日主力净占比 >= 此值 = 高质(真金,非对倒)
Q_WEAK = 1.0      # 今日主力净占比 <  此值 = 低质(放量对倒/派发感)
EXT_5D = 15.0     # 5日涨幅 > 此值 = 分支已偏透支,低位优选踢掉

# B型筛(第三步)
B_CAP_LO, B_CAP_HI = 50.0, 300.0   # 流通市值(亿)
B_60D_MAX = 30.0                    # 60日涨幅上限(低基数)
B_PCT_LO, B_PCT_HI = 0.0, 7.0       # 今日涨幅区间(刚启动,不追高)
B_TO_LO, B_TO_HI = 3.0, 20.0        # 换手率区间

# A型排除
A_60D = 60.0      # 60日涨幅 >= 已跑太多
A_PCT = 9.5       # 今日涨幅 >= 顶涨停/纯追
A_TO = 25.0       # 换手率 >= 抛物线换手


def load(path="data/quotes.json"):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def yi(v):
    return None if v is None else round(v / 1e8, 2)


def step1_direction(d):
    it = {r["code"]: r for r in d.get("industry_today", [])}
    i5 = {r["code"]: r for r in d.get("industry_5d", [])}
    ct = {r["code"]: r for r in d.get("concept_today", [])}
    c5 = {r["code"]: r for r in d.get("concept_5d", [])}
    lines = []

    def scan(today, d5, label):
        rows = []
        for code, t in today.items():
            f = d5.get(code)
            dual = f is not None                      # 今日+5日都在榜 = 持续
            q = t.get("main_pct")
            tag = "真" if (dual and q is not None and q >= Q_GOOD) else \
                  ("低质" if (q is not None and q < Q_WEAK) else "观察")
            rows.append((tag, t["name"], t.get("pct"), q,
                         yi(t.get("main_inflow")),
                         (f or {}).get("pct"), (f or {}).get("main_pct")))
        order = {"真": 0, "观察": 1, "低质": 2}
        rows.sort(key=lambda x: (order[x[0]], -(x[4] or 0)))
        lines.append(f"  [{label}]")
        for tag, name, tp, tq, ti, p5, q5 in rows[:8]:
            lines.append(f"    {tag:<3} {name:<8} 今{tp}%/占比{tq} 净{ti}亿"
                         f" | 5日{p5}%/占比{q5}")
        return rows

    lines.append("① 找方向(今日+5日双确认 + 净占比质量闸)")
    if not today_has(d):
        lines.append("  ⚠ 资金流数据缺失(本次抓取失败),方向无法判定")
        return "\n".join(lines), []
    ind = scan(it, i5, "行业")
    con = scan(ct, c5, "概念")
    真方向 = [(r[1], r[2], r[3]) for r in ind if r[0] == "真"]
    return "\n".join(lines), 真方向


def today_has(d):
    return any(d.get(k) for k in
               ("industry_today", "industry_5d", "concept_today", "concept_5d"))


def step2_branch(d):
    lines = ["② 挑分支(真方向里挑 5日未透支的低位子分支)"]
    i5 = d.get("industry_5d", [])
    if not i5:
        lines.append("  ⚠ 5日行业数据缺失,跳过")
        return "\n".join(lines)
    low, high = [], []
    for r in i5:
        p = r.get("pct")
        if p is None:
            continue
        (low if p <= EXT_5D else high).append((r["name"], p, yi(r.get("main_inflow"))))
    lines.append(f"  低位(5日≤{EXT_5D}%,优选区):")
    for n, p, mi in low[:8]:
        lines.append(f"    {n:<8} 5日{p}%  净{mi}亿")
    if high:
        lines.append(f"  已透支(5日>{EXT_5D}%,避开):")
        for n, p, mi in high[:6]:
            lines.append(f"    {n:<8} 5日{p}%  净{mi}亿")
    return "\n".join(lines)


def classify(s):
    """返回 (标签, 命中原因) —— A型排除优先,再B型入选。"""
    cap, p60, pct, to = (s.get("float_cap_yi"), s.get("chg_60d"),
                         s.get("pct"), s.get("turnover_pct"))
    if p60 is not None and p60 >= A_60D:
        return "A", f"60日+{p60}%"
    if pct is not None and pct >= A_PCT:
        return "A", f"今日+{pct}%"
    if to is not None and to >= A_TO:
        return "A", f"换手{to}%"
    ok = (cap is not None and B_CAP_LO <= cap <= B_CAP_HI and
          p60 is not None and p60 <= B_60D_MAX and
          pct is not None and B_PCT_LO <= pct <= B_PCT_HI and
          to is not None and B_TO_LO <= to <= B_TO_HI)
    if ok:
        return "B", f"市值{cap}亿/60日{p60}%/今{pct}%/换{to}%"
    return "中性", ""


def step3_stocks(d):
    lines = ["③ 选个股(drilldown 成分股 → B型筛)"]
    dd = d.get("drilldown")
    if not dd:
        lines.append("  ⚠ 无 drilldown 数据。往 drilldown.txt 填板块BK代码并重跑后再筛。")
        return "\n".join(lines)
    for bk, rows in dd.items():
        b = []
        for s in rows:
            tag, why = classify(s)
            if tag == "B":
                b.append((s["name"], s["code"], why))
        lines.append(f"  [{bk}] 成分{len(rows)} → B型候选 {len(b)}:")
        for n, c, why in b[:15]:
            lines.append(f"    B  {n:<8}({c})  {why}")
        if not b:
            lines.append("    (无票通过B型筛,可能整板已透支或市值不匹配)")
    return "\n".join(lines)


def step4_stage(d):
    lines = ["④ 认四阶段(A型透支打标;横盘→突破 形态仍需看K线)"]
    wl = d.get("watchlist", [])
    if wl:
        lines.append("  自选股:")
        for s in wl:
            pct, to, pe = s.get("pct"), s.get("turnover_pct"), s.get("pe_ttm")
            flags = []
            if pct is not None and pct >= A_PCT:
                flags.append(f"今+{pct}%")
            if to is not None and to >= A_TO:
                flags.append(f"换{to}%")
            if pe is not None and (pe < 0 or pe >= 150):
                flags.append(f"PE{pe}")
            tag = "A型/警惕" if flags else "—"
            lines.append(f"    {s['name']:<8} {s.get('price')} ({pct}%)"
                         f"  {tag} {' '.join(flags)}")
    dd = d.get("drilldown") or {}
    for bk, rows in dd.items():
        a = [(s["name"], classify(s)[1]) for s in rows if classify(s)[0] == "A"]
        if a:
            lines.append(f"  [{bk}] A型透支(避开区)共{len(a)}:")
            for n, why in a[:10]:
                lines.append(f"    A  {n:<8}  {why}")
    return "\n".join(lines)


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "data/quotes.json"
    d = load(path)
    print("=" * 60)
    print(f"funnel @ {d.get('updated_at')}   (源: {path})")
    ls = d.get("limit_stats") or {}
    print(f"涨跌停: {ls.get('zt_count')} / {ls.get('dt_count')}"
          f"   errors: {len(d.get('errors', []))}")
    print("=" * 60)
    dir_txt, 真方向 = step1_direction(d)
    print(dir_txt); print()
    print(step2_branch(d)); print()
    print(step3_stocks(d)); print()
    print(step4_stage(d))
    print("=" * 60)
    if 真方向:
        print("真方向候选:", "、".join(f"{n}({p}%,占比{q})" for n, p, q in 真方向))
    else:
        print("真方向候选: 无(数据缺失或无高质持续板块)")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# A股数据抓取 —— GitHub Actions 上运行
# 自选股报价：腾讯(主) + 新浪(兜底)，海外 IP 友好
# 板块资金流：尽力试东财(可能仍被墙)，失败则优雅留空
import json, os, traceback
from datetime import datetime
from zoneinfo import ZoneInfo
import requests

BJ = ZoneInfo("Asia/Shanghai")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def load_watchlist(path="watchlist.txt"):
    codes = []
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            c = line.strip().split("#")[0].strip()
            if c.isdigit() and len(c) == 6:
                codes.append(c)
    return codes


def to_secid(code):
    return ("sh" if code[0] in "69" else "sz") + code   # 6/9->沪, 其余->深


def num(f, i):
    try:
        return float(f[i])
    except Exception:
        return None


def fetch_tencent(codes):
    """腾讯行情，字段丰富，海外友好。返回 code->record"""
    out = {}
    if not codes:
        return out
    url = "https://qt.gtimg.cn/q=" + ",".join(to_secid(c) for c in codes)
    r = requests.get(url, headers={"User-Agent": UA}, timeout=15)
    text = r.content.decode("gbk", errors="ignore")
    for line in text.split(";"):
        line = line.strip()
        if not line.startswith("v_") or '="' not in line:
            continue
        try:
            body = line.split('="', 1)[1].rstrip('"')
            f = body.split("~")
            code = f[2]
            price, prev = float(f[3]), float(f[4])
            out[code] = {
                "name": f[1], "code": code, "source": "tencent",
                "price": price, "prev_close": prev, "open": float(f[5]),
                "high": float(f[33]), "low": float(f[34]),
                "pct": round((price - prev) / prev * 100, 2) if prev else None,
                "time": f[30],
                # 以下字段索引若偶有偏差，可从 raw 重新解析
                "turnover_pct": num(f, 38), "pe_ttm": num(f, 39),
                "amount_wan": num(f, 37), "float_cap_yi": num(f, 44),
                "total_cap_yi": num(f, 45), "vol_ratio": num(f, 49),
                "raw": body,
            }
        except Exception:
            traceback.print_exc()
    return out


def fetch_sina(codes):
    """新浪兜底，字段索引稳定"""
    out = {}
    if not codes:
        return out
    url = "https://hq.sinajs.cn/list=" + ",".join(to_secid(c) for c in codes)
    r = requests.get(url, headers={"User-Agent": UA,
                                   "Referer": "https://finance.sina.com.cn"}, timeout=15)
    text = r.content.decode("gbk", errors="ignore")
    for line in text.split(";"):
        line = line.strip()
        if "hq_str_" not in line or '="' not in line:
            continue
        try:
            head, body = line.split('="', 1)
            code = head.split("hq_str_")[1][2:]
            f = body.rstrip('"').split(",")
            if len(f) < 32:
                continue
            price, prev = float(f[3]), float(f[2])
            out[code] = {
                "name": f[0], "code": code, "source": "sina",
                "price": price, "prev_close": prev, "open": float(f[1]),
                "high": float(f[4]), "low": float(f[5]),
                "pct": round((price - prev) / prev * 100, 2) if prev else None,
                "time": f[30] + " " + f[31],
            }
        except Exception:
            traceback.print_exc()
    return out


def fetch_em_sector_flow():
    """尽力试东财板块资金流(今日, 行业+概念)。被墙就抛异常, 由调用方接住"""
    res = {}
    base = "https://push2.eastmoney.com/api/qt/clist/get"
    headers = {"User-Agent": UA, "Referer": "https://data.eastmoney.com/"}
    for key, fs in [("industry_today", "m:90+t:2"), ("concept_today", "m:90+t:3")]:
        params = {"pn": 1, "pz": 15, "po": 1, "np": 1, "fltt": 2, "invt": 2,
                  "fid": "f62", "fs": fs, "fields": "f12,f14,f3,f62,f184"}
        r = requests.get(base, params=params, headers=headers, timeout=15)
        rows = ((r.json() or {}).get("data") or {}).get("diff") or []
        res[key] = [{"code": x.get("f12"), "name": x.get("f14"),
                     "pct": x.get("f3"), "main_inflow": x.get("f62"),
                     "main_pct": x.get("f184")} for x in rows]
    return res


def main():
    out = {"updated_at": datetime.now(BJ).strftime("%Y-%m-%d %H:%M:%S %Z"),
           "watchlist": [], "errors": []}
    codes = load_watchlist()

    # 自选股：腾讯主、新浪补缺
    quotes = {}
    try:
        quotes = fetch_tencent(codes)
    except Exception as e:
        out["errors"].append(f"tencent failed: {e}")
    missing = [c for c in codes if c not in quotes]
    if missing:
        try:
            quotes.update(fetch_sina(missing))
        except Exception as e:
            out["errors"].append(f"sina failed: {e}")
    out["watchlist"] = [quotes[c] for c in codes if c in quotes]
    for c in codes:
        if c not in quotes:
            out["errors"].append(f"quote missing: {c}")

    # 板块资金流：尽力试东财
    try:
        out.update(fetch_em_sector_flow())
    except Exception as e:
        out["errors"].append(f"eastmoney sector flow blocked/failed: {e}")

    os.makedirs("data", exist_ok=True)
    with open("data/quotes.json", "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=2)
    print("OK", out["updated_at"], "| quotes:", len(out["watchlist"]),
          "| errors:", out["errors"])


if __name__ == "__main__":
    main()

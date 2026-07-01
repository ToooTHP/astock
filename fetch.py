#!/usr/bin/env python3
# A股数据抓取 —— GitHub Actions 上运行
# 1) 指数风向标 (腾讯, 海外友好)
# 2) 自选股报价 (腾讯主 + 新浪兜底)
# 3) 板块资金流 (东财, 今日 + 5日, 行业 + 概念)
import json, os, time, random, traceback
from datetime import datetime
from zoneinfo import ZoneInfo
import requests

BJ = ZoneInfo("Asia/Shanghai")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

INDEX = [("sh000001", "上证指数"), ("sz399001", "深证成指"),
         ("sz399006", "创业板指"), ("sh000688", "科创50"),
         ("sh000300", "沪深300"), ("sh000852", "中证1000"),
         ("bj899050", "北证50")]


def load_watchlist(path="watchlist.txt"):
    codes = []
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            c = line.strip().split("#")[0].strip()
            if c.isdigit() and len(c) == 6:
                codes.append(c)
    return codes


def to_secid(code):
    return ("sh" if code[0] in "69" else "sz") + code


def num(f, i):
    try:
        return float(f[i])
    except Exception:
        return None


def _tencent(secids):
    url = "https://qt.gtimg.cn/q=" + ",".join(secids)
    r = requests.get(url, headers={"User-Agent": UA}, timeout=15)
    return r.content.decode("gbk", errors="ignore")


def fetch_indices():
    out = []
    text = _tencent([s for s, _ in INDEX])
    for line in text.split(";"):
        line = line.strip()
        if not line.startswith("v_") or '="' not in line:
            continue
        try:
            f = line.split('="', 1)[1].rstrip('"').split("~")
            price, prev = float(f[3]), float(f[4])
            out.append({"name": f[1], "code": f[2], "price": price,
                        "prev_close": prev,
                        "pct": round((price - prev) / prev * 100, 2) if prev else None,
                        "high": num(f, 33), "low": num(f, 34)})
        except Exception:
            traceback.print_exc()
    return out


def fetch_tencent(codes):
    out = {}
    if not codes:
        return out
    text = _tencent([to_secid(c) for c in codes])
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
                "turnover_pct": num(f, 38), "pe_ttm": num(f, 39),
                "amount_wan": num(f, 37), "float_cap_yi": num(f, 44),
                "total_cap_yi": num(f, 45), "vol_ratio": num(f, 49),
                "raw": body,
            }
        except Exception:
            traceback.print_exc()
    return out


def fetch_sina(codes):
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


# 东财 push2 有一组负载均衡镜像,单个网关 502 时换一台大概率是好的
EM_HOSTS = ["push2", "1.push2", "23.push2", "78.push2",
            "79.push2", "80.push2", "82.push2"]


def _em_flow(fs, fid, fields, retries=3, backoff=2.0):
    headers = {"User-Agent": UA, "Referer": "https://data.eastmoney.com/"}
    params = {"pn": 1, "pz": 15, "po": 1, "np": 1, "fltt": 2, "invt": 2,
              "ut": "b2884a393a59ad64002292a3e90d46a5",  # 资金流接口固定解锁令牌,5日排序必需
              "fid": fid, "fs": fs, "fields": fields}
    hosts = random.sample(EM_HOSTS, len(EM_HOSTS))  # 打乱,别每次都先撞同一台
    last_err = None
    for attempt in range(retries + 1):
        host = hosts[attempt % len(hosts)]              # 每次重试换一台镜像
        url = f"https://{host}.eastmoney.com/api/qt/clist/get"
        if attempt:
            time.sleep(backoff * attempt)               # 递增退避
        try:
            r = requests.get(url, params=params, headers=headers, timeout=15)
            return ((r.json() or {}).get("data") or {}).get("diff") or []
        except Exception as e:
            code = getattr(getattr(e, "response", None), "status_code", None)
            try:
                code = r.status_code
                body = r.text[:100]
            except Exception:
                body = ""
            last_err = f"{host}: {e} | status={code} body={body!r}"
    raise RuntimeError(last_err)


def fetch_sector_flow(errors):
    out = {}
    # (key, 板块类型 fs, 排序字段 fid, 字段, 涨跌幅键, 净额键, 净占比键)
    plans = [
        ("industry_today", "m:90+t:2", "f62",  "f12,f14,f3,f62,f184",   "f3",   "f62",  "f184"),
        ("concept_today",  "m:90+t:3", "f62",  "f12,f14,f3,f62,f184",   "f3",   "f62",  "f184"),
        ("industry_5d",    "m:90+t:2", "f164", "f12,f14,f109,f164,f165", "f109", "f164", "f165"),
        ("concept_5d",     "m:90+t:3", "f164", "f12,f14,f109,f164,f165", "f109", "f164", "f165"),
    ]
    for i, (key, fs, fid, fields, kp, ki, kr) in enumerate(plans):
        if i:
            time.sleep(1.5)  # 4次请求错开节奏
        try:
            rows = _em_flow(fs, fid, fields)
            out[key] = [{"code": x.get("f12"), "name": x.get("f14"),
                         "pct": x.get(kp), "main_inflow": x.get(ki),
                         "main_pct": x.get(kr)} for x in rows]
        except Exception as e:
            errors.append(f"{key} failed: {e}")
            traceback.print_exc()
    return out


def main():
    out = {"updated_at": datetime.now(BJ).strftime("%Y-%m-%d %H:%M:%S %Z"),
           "indices": [], "watchlist": [], "errors": []}
    errors = out["errors"]

    # 指数风向标
    try:
        out["indices"] = fetch_indices()
    except Exception as e:
        errors.append(f"indices failed: {e}")

    # 自选股
    codes = load_watchlist()
    quotes = {}
    try:
        quotes = fetch_tencent(codes)
    except Exception as e:
        errors.append(f"tencent failed: {e}")
    missing = [c for c in codes if c not in quotes]
    if missing:
        try:
            quotes.update(fetch_sina(missing))
        except Exception as e:
            errors.append(f"sina failed: {e}")
    out["watchlist"] = [quotes[c] for c in codes if c in quotes]
    for c in codes:
        if c not in quotes:
            errors.append(f"quote missing: {c}")

    # 板块资金流 今日 + 5日
    out.update(fetch_sector_flow(errors))

    os.makedirs("data", exist_ok=True)
    with open("data/quotes.json", "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=2)
    print("OK", out["updated_at"], "| idx:", len(out["indices"]),
          "| quotes:", len(out["watchlist"]), "| errors:", errors)


if __name__ == "__main__":
    main()

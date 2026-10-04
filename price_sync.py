#!/usr/bin/env python3
"""
antseed price sync
==================

Keep antseed node pricing aligned with upstream cost, so you never sell below
what you pay.

Why
---
Seller pricing drifts. Upstream raises a price, you forget to update, and you
spend a week selling at a loss before anyone notices.

This tool takes two things:

    costs.json    what you pay upstream, per million tokens, in USD
    seller.json   what your node advertises

and tells you where the two disagree.

Two things that are easy to get wrong
-------------------------------------
1. **Model names are not unique across sources.** Match on a normalised key,
   never on the raw string, or you will silently match the wrong model.
   This tool lowercases, strips provider prefixes ("openai/", "anthropic/"),
   and strips trailing date suffixes.

2. **"Official price is 0" does not mean "upstream is free".** Some models are
   listed at zero by the provider and still cost money from every reseller.
   Always take the cheapest *paid* upstream as your cost basis.

Usage
-----
    python price_sync.py --costs costs.json --config seller.json
    python price_sync.py --costs costs.json --config seller.json --margin 1.15

costs.json
----------
    {
      "deepseek-v4-flash": {"inputUsdPerMillion": 0.14, "outputUsdPerMillion": 0.28},
      "kimi-k3":           {"inputUsdPerMillion": 0.60, "outputUsdPerMillion": 2.50}
    }

seller.json
-----------
    {
      "models": {
        "deepseek-v4-flash": {"inputUsdPerMillion": 0.20, "outputUsdPerMillion": 0.40}
      }
    }

Exit codes
----------
    0  pricing is healthy
    1  at least one model is priced below cost
    2  bad input
"""

import argparse
import json
import re
import sys

# Provider prefixes that show up in front of the same underlying model.
PROVIDER_PREFIXES = ("openai/", "anthropic/", "google/", "meta/", "mistral/",
                     "deepseek/", "qwen/", "z-ai/", "moonshot/", "xai/")

# Trailing version/build suffixes that differ between catalogs.
DATE_SUFFIX = re.compile(r"[-@]v?\d{4}[-.]?\d{2}([-.]?\d{2})?$")
VARIANT_SUFFIX = re.compile(r"[-:](latest|stable|preview|beta)$")


def canonical(model):
    """A stable key for cross-source matching.

    Deliberately conservative: it strips only things that are known to vary
    between catalogs (provider prefix, date stamp, channel suffix). It does not
    try to merge genuinely different models.
    """
    m = (model or "").strip().lower()
    for p in PROVIDER_PREFIXES:
        if m.startswith(p):
            m = m[len(p):]
            break
    m = DATE_SUFFIX.sub("", m)
    m = VARIANT_SUFFIX.sub("", m)
    return m.strip("-:/ ")


def load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def index_by_canonical(block):
    """{canonical_name: (original_name, value)} - first wins, warns on clash."""
    out = {}
    clashes = []
    for name, value in (block or {}).items():
        key = canonical(name)
        if key in out and out[key][0] != name:
            clashes.append((name, out[key][0]))
            continue
        out.setdefault(key, (name, value))
    return out, clashes


def main():
    ap = argparse.ArgumentParser(description="Compare node pricing against upstream cost.")
    ap.add_argument("--costs", required=True, help="what you pay upstream")
    ap.add_argument("--config", required=True, help="what your node advertises")
    ap.add_argument("--margin", type=float, default=1.0,
                    help="minimum acceptable markup, e.g. 1.15 = +15%% (default 1.0)")
    ap.add_argument("--json", dest="json_out", default=None)
    args = ap.parse_args()

    try:
        costs_raw = load(args.costs)
        seller_raw = load(args.config)
    except Exception as e:
        print("could not read input: %s" % e, file=sys.stderr)
        return 2

    costs, cost_clashes = index_by_canonical(costs_raw)
    seller_models = seller_raw.get("models") if isinstance(seller_raw.get("models"), dict) else seller_raw
    seller, seller_clashes = index_by_canonical(seller_models)

    if cost_clashes:
        print("提示：成本表里有名称冲突，已按先出现的为准：")
        for a, b in cost_clashes:
            print("   %s / %s" % (a, b))
        print()

    rows = []
    for key, (cost_name, cost) in sorted(costs.items()):
        i_cost = cost.get("inputUsdPerMillion")
        o_cost = cost.get("outputUsdPerMillion")
        if not isinstance(i_cost, (int, float)) or not isinstance(o_cost, (int, float)):
            continue

        if key not in seller:
            rows.append({"key": key, "model": cost_name, "state": "缺失",
                         "i_cost": i_cost, "o_cost": o_cost,
                         "i_px": None, "o_px": None})
            continue

        sname, price = seller[key]
        i_px = price.get("inputUsdPerMillion")
        o_px = price.get("outputUsdPerMillion")
        if not isinstance(i_px, (int, float)) or not isinstance(o_px, (int, float)):
            rows.append({"key": key, "model": sname, "state": "价格缺失",
                         "i_cost": i_cost, "o_cost": o_cost, "i_px": None, "o_px": None})
            continue

        i_ratio = (i_px / i_cost) if i_cost else float("inf")
        o_ratio = (o_px / o_cost) if o_cost else float("inf")
        # The worse of the two sides is what actually decides whether you lose money.
        ratio = min(i_ratio, o_ratio)

        if ratio < 1.0:
            state = "亏本"
        elif ratio < args.margin:
            state = "利润过薄"
        else:
            state = "正常"

        rows.append({"key": key, "model": sname, "state": state,
                     "i_cost": i_cost, "o_cost": o_cost, "i_px": i_px, "o_px": o_px,
                     "ratio": ratio})

    seller_only = [k for k in seller if k not in costs]

    print("上游成本 vs 节点定价")
    print()
    head = "%-26s %-8s %9s %9s %9s %9s %7s" % (
        "模型", "状态", "进价输入", "进价输出", "售价输入", "售价输出", "倍率")
    print(head)
    print("-" * len(head))
    for r in rows:
        def n(v):
            return "-" if v is None else "%.4f" % v
        ratio = r.get("ratio")
        print("%-26s %-8s %9s %9s %9s %9s %7s" % (
            r["model"][:26], r["state"], n(r["i_cost"]), n(r["o_cost"]),
            n(r["i_px"]), n(r["o_px"]),
            "-" if ratio is None else "%.2f" % ratio))

    bad = [r for r in rows if r["state"] in ("亏本", "利润过薄")]
    missing = [r for r in rows if r["state"].startswith("价格") or r["state"] == "缺失"]

    print()
    print("=" * 70)
    print("模型总数 %d ｜ 亏本或过薄 %d ｜ 缺价或缺失 %d ｜ 定价表独有 %d"
          % (len(rows), len(bad), len(missing), len(seller_only)))

    if bad:
        print()
        print("需要处理（倍率越低越危险；两头取更吃亏的那一侧）：")
        for r in sorted(bad, key=lambda x: x.get("ratio") or 0):
            print("   %-26s 倍率 %.2f" % (r["model"], r.get("ratio") or 0))

    if seller_only:
        print()
        print("节点在卖、但成本表里没有（无法判断是否亏本）：")
        for k in sorted(seller_only):
            print("   %s" % seller[k][0])

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump({"rows": rows, "seller_only": seller_only}, fh,
                      indent=2, ensure_ascii=False)
        print("\n已写出：%s" % args.json_out)

    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

"""Analyse per-image scores from the MVTec benchmark.

Usage:
    python3 tests/analyze_scores.py /tmp/scores_v2.json
"""
import json
import sys
import numpy as np

DEFAULT_DELTA = 0.05


def verdict(score, T, delta):
    if delta == 0:
        return "PASS" if score < T else "FAIL"
    if score < T - delta:
        return "PASS"
    if score < T + delta:
        return "REVIEW"
    return "FAIL"


def main(path):
    data = json.load(open(path))
    all_good = []
    all_bad = []
    per_category = {}
    for cat, items in data.items():
        g = [it["score"] for it in items if not it["defective"]]
        b = [it["score"] for it in items if it["defective"]]
        per_category[cat] = (g, b)
        all_good.extend(g)
        all_bad.extend(b)

    all_good = np.asarray(all_good)
    all_bad = np.asarray(all_bad)

    print(f"{'T':>6} {'caught':>12} {'recall':>8} {'false_rej':>12} {'fr_rate':>8}")
    print("-" * 52)
    for T in [0.40, 0.45, 0.50, 0.52, 0.55, 0.58, 0.60, 0.62, 0.65, 0.70]:
        caught = int((all_bad >= T - DEFAULT_DELTA).sum())
        fr = int((all_good >= T + DEFAULT_DELTA).sum())
        recall = caught / len(all_bad)
        fr_rate = fr / len(all_good)
        print(f"{T:>6.2f} {caught:>5}/{len(all_bad):<6} {recall:>7.1%} "
              f"{fr:>5}/{len(all_good):<6} {fr_rate:>7.1%}")

    # Per-category at T=0.60
    print("\nPer-category at T=0.60, delta=0.05:")
    print(f"{'category':<12} {'caught':>10} {'recall':>8} {'false_rej':>10}")
    for cat, (g, b) in per_category.items():
        g = np.asarray(g)
        b = np.asarray(b)
        caught = int((b >= 0.55).sum())
        fr = int((g >= 0.65).sum())
        print(f"{cat:<12} {caught:>4}/{len(b):<5} {caught/len(b):>7.1%} "
              f"{fr:>4}/{len(g):<5}")

    # Per-category at tuned T
    print(f"\nPer-category at T=0.46, delta=0.05:")
    print(f"{'category':<12} {'caught':>10} {'recall':>8} {'false_rej':>10}")
    for cat, (g, b) in per_category.items():
        g = np.asarray(g)
        b = np.asarray(b)
        caught = int((b >= 0.41).sum())
        fr = int((g >= 0.51).sum())
        print(f"{cat:<12} {caught:>4}/{len(b):<5} {caught/len(b):>7.1%} "
              f"{fr:>4}/{len(g):<5}")

    # Leave-one-category-out: tune T on 14 categories, evaluate on held-out one
    print("\nLeave-one-category-out threshold generalisation:")
    cats = sorted(per_category.keys())
    loco_recall_num = loco_recall_den = 0
    loco_fr_num = loco_fr_den = 0
    for held in cats:
        g_train, b_train = [], []
        for c in cats:
            if c == held:
                continue
            g_train.extend(per_category[c][0])
            b_train.extend(per_category[c][1])
        g_train = np.asarray(g_train)
        b_train = np.asarray(b_train)
        chosen = None
        for T in np.arange(0.30, 0.80, 0.01):
            caught = int((b_train >= T - DEFAULT_DELTA).sum())
            fr = int((g_train >= T + DEFAULT_DELTA).sum())
            if caught / len(b_train) >= 0.90 and fr / len(g_train) <= 0.10:
                chosen = round(float(T), 2)
        if chosen is None:
            chosen = 0.46
        g = np.asarray(per_category[held][0])
        b = np.asarray(per_category[held][1])
        caught = int((b >= chosen - DEFAULT_DELTA).sum())
        fr = int((g >= chosen + DEFAULT_DELTA).sum())
        loco_recall_num += caught
        loco_recall_den += len(b)
        loco_fr_num += fr
        loco_fr_den += len(g)
        print(f"  {held:<12} T={chosen:.2f}  recall={caught}/{len(b)} "
              f"({caught/len(b):.0%})  false_rej={fr}/{len(g)}")
    print(f"  LOCO overall: recall={loco_recall_num}/{loco_recall_den} "
          f"({loco_recall_num/loco_recall_den:.1%}), "
          f"false rejects={loco_fr_num}/{loco_fr_den} "
          f"({loco_fr_num/loco_fr_den:.1%})")

    # Pick T achieving target recall with the best false-reject rate
    print("\nSearch: highest T with recall >= 90% and false rejects <= 10%:")
    best = None
    for T in np.arange(0.30, 0.80, 0.01):
        caught = int((all_bad >= T - DEFAULT_DELTA).sum())
        fr = int((all_good >= T + DEFAULT_DELTA).sum())
        recall = caught / len(all_bad)
        fr_rate = fr / len(all_good)
        if recall >= 0.90 and fr_rate <= 0.10:
            best = (round(float(T), 2), recall, fr_rate)
    if best:
        print(f"  T={best[0]:.2f}: recall={best[1]:.1%}, false rejects={best[2]:.1%}")
    else:
        print("  none found in range")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/scores_v2.json")

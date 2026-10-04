"""Check every clip's ball movement for lag and stutter.

Prints per-clip counts and writes data/review/ball_speed.png: ball speed over
time for every clip (red line = the shot, orange dots = frames with no ball).
Usage: python pipeline/ball_audit.py
"""

import glob
import json
import math
import statistics
from pathlib import Path

CLIPS = Path("clips")
OUT = Path("data/review/ball_speed.png")
FAST = 45.0  # m/s between two frames: faster than any real kick


def audit(clip):
    frames = clip["frames"]
    ball = [f["b"] for f in frames]
    t = [f["t"] for f in frames]
    r = {"repeat_t": 0, "uneven_dt": 0, "stall": 0, "fast": 0, "gaps": 0, "missing": 0}
    dts = [b - a for a, b in zip(t, t[1:])]
    r["repeat_t"] = sum(dt <= 0 for dt in dts)
    good = [dt for dt in dts if dt > 0]
    if good:
        med = statistics.median(good)
        r["uneven_dt"] = sum(abs(dt - med) > 0.25 * med for dt in good)
    for i in range(1, len(frames)):
        a, b = ball[i - 1], ball[i]
        if a and b and dts[i - 1] > 0 and math.dist(a, b) / dts[i - 1] > FAST:
            r["fast"] += 1
    for i in range(1, len(frames) - 1):
        if ball[i - 1] and ball[i] and ball[i + 1]:
            if math.dist(ball[i - 1], ball[i]) < 0.005 and math.dist(ball[i], ball[i + 1]) > 0.15:
                r["stall"] += 1
    r["missing"] = sum(b is None for b in ball)
    i = 0
    while i < len(ball):
        if ball[i] is None:
            j = i
            while j < len(ball) and ball[j] is None:
                j += 1
            if 0 < i and j < len(ball):
                r["gaps"] += 1
            i = j
        else:
            i += 1
    return r


def sheet(clips, out):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    cols = 10
    rows = math.ceil(len(clips) / cols)
    fig, axes = plt.subplots(rows, cols, figsize=(cols * 2.6, rows * 1.5), squeeze=False)
    for ax, c in zip(axes.flat, clips):
        f = c["frames"]
        ts, vs = [], []
        for i in range(1, len(f)):
            a, b = f[i - 1]["b"], f[i]["b"]
            dt = f[i]["t"] - f[i - 1]["t"]
            if a and b and dt > 0:
                ts.append(f[i]["t"])
                vs.append(min(math.dist(a, b) / dt, 80))
        ax.plot(ts, vs, lw=0.6, color="k")
        miss = [x["t"] for x in f if x["b"] is None]
        ax.scatter(miss, [1] * len(miss), s=0.3, color="#f59e0b")
        ax.axvline(c["goalT"], color="r", lw=0.6)
        ax.set_xlim(0, f[-1]["t"])
        ax.set_ylim(0, 80)
        ax.set_xticks([])
        ax.set_yticks([0, 40])
        ax.tick_params(labelsize=5)
        ax.set_title(f"{c['scorer'][:16]} {c['clock']}", fontsize=6)
    for ax in list(axes.flat)[len(clips):]:
        ax.axis("off")
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=55)


def main():
    clips = [json.load(open(p)) for p in sorted(glob.glob(str(CLIPS / "*_*.json")))]
    rows = []
    for c in clips:
        r = audit(c)
        r["name"] = f"{c['gameId']}_{c['gameEventId']}.json {c['scorer']} {c['clock']} ({c['ballSource']})"
        rows.append(r)
    keys = ["repeat_t", "uneven_dt", "stall", "fast", "gaps", "missing"]
    print(f"{len(rows)} clips")
    for k in keys:
        vals = [r[k] for r in rows]
        print(f"  {k:10} clips affected {sum(v > 0 for v in vals):4}   total {sum(vals)}")
    worst = sorted(rows, key=lambda r: -(r["stall"] + r["fast"] + r["repeat_t"] + r["uneven_dt"]))[:15]
    print("worst:")
    for r in worst:
        print("  " + r["name"] + "  " + "  ".join(f"{k}={r[k]}" for k in keys if r[k]))
    sheet(clips, OUT)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()

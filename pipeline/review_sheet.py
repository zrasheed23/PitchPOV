"""Draw every clip on the review list as a 2D sketch around the shot, on one image.

Green star = scorer at the shot, green line = his last 3 s, grey = ball before the shot,
red = ball after the shot, black x = ball at the shot. Output: data/review/review_sheet.png
Usage: python pipeline/review_sheet.py
"""
import json, math
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ix = json.load(open("clips/index.json"))
flagged = [g for g in ix if g.get("review")]
if not flagged:
    raise SystemExit("review list is empty, nothing to draw")
cols = 4
rows = math.ceil(len(flagged) / cols)
fig, axes = plt.subplots(rows, cols, figsize=(cols * 5, rows * 4.2), squeeze=False)
out = []
for n, (g, ax) in enumerate(zip(flagged, axes.flat), 1):
    c = json.load(open("clips/" + g["clip"]))
    F = c["frames"]; gf = c["goalFrame"]; sid = c["scorerId"]
    side = 1 if any(f["b"] and f["b"][0] > 40 for f in F[gf:]) else -1
    if not any(f["b"] and abs(f["b"][0]) > 40 for f in F[gf:]):
        # fall back: side the scorer is on at the shot
        side = 1 if F[gf]["p"].get(sid, [0, 0])[0] >= 0 else -1
    # pitch: penalty area + goal
    gx = 52.5 * side
    ax.plot([gx, gx], [-34, 34], color="#999", lw=1)
    ax.plot([gx, gx - 16.5 * side, gx - 16.5 * side, gx], [-20.16, -20.16, 20.16, 20.16], color="#bbb", lw=1)
    ax.plot([gx, gx - 5.5 * side, gx - 5.5 * side, gx], [-9.16, -9.16, 9.16, 9.16], color="#bbb", lw=1)
    ax.plot([gx, gx + 2 * side, gx + 2 * side, gx], [-3.66, -3.66, 3.66, 3.66], color="k", lw=2)
    team = {p["id"]: p["team"] for p in c["players"]}
    for pid, (x, y) in F[gf]["p"].items():
        if pid == sid: continue
        ax.plot(x, y, "o", ms=4, color="#e57373" if team[pid] == "home" else "#64b5f6", alpha=.7)
    a = max(0, gf - 90)
    sp = [f["p"].get(sid) for f in F[a:gf + 1] if f["p"].get(sid)]
    if sp:
        ax.plot([p[0] for p in sp], [p[1] for p in sp], color="green", lw=1)
    s = F[gf]["p"].get(sid)
    if s: ax.plot(*s, "*", ms=16, color="green", mec="k")
    pre = [f["b"] for f in F[a:gf + 1] if f["b"]]
    post = [f["b"] for f in F[gf:] if f["b"]]
    if pre: ax.plot([b[0] for b in pre], [b[1] for b in pre], color="#555", lw=1.2)
    if post: ax.plot([b[0] for b in post], [b[1] for b in post], color="red", lw=2)
    b = F[gf]["b"]
    d = math.hypot(b[0] - s[0], b[1] - s[1]) if (b and s) else None
    if b: ax.plot(b[0], b[1], "kx", ms=10, mew=2)
    ax.set_xlim(gx - 40 * side, gx + 4 * side) if side == 1 else ax.set_xlim(gx + 4 * side, gx - 40 * side)
    ax.set_ylim(-34, 34); ax.set_aspect("equal"); ax.set_xticks([]); ax.set_yticks([])
    if side == -1: ax.invert_xaxis()
    title = f"#{n} {g['scorer']} {g['clock']} ({g['teamShort']} v {g['opponentShort']})\nsrc={c['ballSource']} ball-shooter={'?' if d is None else f'{d:.1f}m'}"
    ax.set_title(title, fontsize=9)
    ax.text(0.01, 0.01, "; ".join(g["review"])[:70], transform=ax.transAxes, fontsize=7, color="#a00")
    out.append((n, g["clip"], g["scorer"], g["clock"], c["ballSource"], None if d is None else round(d, 1), g["review"]))
for ax in list(axes.flat)[len(flagged):]: ax.axis("off")
fig.tight_layout()
Path("data/review").mkdir(parents=True, exist_ok=True)
fig.savefig("data/review/review_sheet.png", dpi=70)
json.dump(out, open("data/review/review_list.json", "w"), ensure_ascii=False, indent=0)
for o in out: print(o)

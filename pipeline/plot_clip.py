"""Plot four frames of a clip (start, middle, goal, end) on a 2D pitch to sanity-check positions.

Usage: python pipeline/plot_clip.py CLIP_JSON [OUT_PNG]
Defaults to CLIP_JSON with "_check.png" in place of ".json".
"""

import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import to_rgb
from matplotlib.patches import Circle, Rectangle

HALF_LENGTH = 52.5
HALF_WIDTH = 34.0


def draw_pitch(ax):
    ax.set_facecolor("#3a7d44")
    line = dict(color="white", lw=1, fill=False)
    ax.add_patch(Rectangle((-HALF_LENGTH, -HALF_WIDTH), 2 * HALF_LENGTH, 2 * HALF_WIDTH, **line))
    ax.plot([0, 0], [-HALF_WIDTH, HALF_WIDTH], color="white", lw=1)
    ax.add_patch(Circle((0, 0), 9.15, **line))
    for side in (-1, 1):
        goal_line = side * HALF_LENGTH
        for depth, half_w in ((16.5, 20.16), (5.5, 9.16)):
            x = goal_line - depth if side > 0 else goal_line
            ax.add_patch(Rectangle((x, -half_w), depth, 2 * half_w, **line))
        x = goal_line if side > 0 else goal_line - 2
        ax.add_patch(Rectangle((x, -3.66), 2, 7.32, **line))
    ax.set_xlim(-HALF_LENGTH - 4.5, HALF_LENGTH + 4.5)
    ax.set_ylim(-HALF_WIDTH - 3, HALF_WIDTH + 3)
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])


def label_color(hex_color):
    r, g, b = to_rgb(hex_color)
    return "black" if 0.299 * r + 0.587 * g + 0.114 * b > 0.6 else "white"


def plot_clip(clip, out_path):
    frames = clip["frames"]
    players = {p["id"]: p for p in clip["players"]}
    n = len(frames)
    picks = [("start", 0), ("middle", n // 2), ("goal", clip["goalFrame"]), ("end", n - 1)]

    fig, axes = plt.subplots(2, 2, figsize=(16, 11))
    for ax, (label, i) in zip(axes.flat, picks):
        draw_pitch(ax)
        frame = frames[i]
        for pid, (x, y) in frame["p"].items():
            team = clip["teams"][players[pid]["team"]]
            ax.scatter(x, y, s=260, c=team["color"], edgecolors=team["textColor"], linewidths=2, zorder=3)
            ax.text(x, y, str(players[pid]["number"]), ha="center", va="center", fontsize=7,
                    color=label_color(team["color"]), weight="bold", zorder=4)
        if frame["b"] is not None:
            bx, by, bz = frame["b"]
            ax.scatter(bx, by, s=70, c="yellow", edgecolors="black", zorder=5)
            ball = f"ball ({bx:.1f}, {by:.1f}, z={bz:.2f})"
        else:
            ball = "ball missing"
        ax.set_title(f"{label}: idx {i}, t={frame['t']:.2f}s, {ball}", fontsize=10)

    home, away = clip["teams"]["home"], clip["teams"]["away"]
    fig.suptitle(f"{clip['scorer']} {clip['clock']}, {home['name']} vs {away['name']}", fontsize=13)
    plt.tight_layout()
    plt.savefig(out_path, dpi=90)
    plt.close(fig)


def main():
    clip_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2]) if len(sys.argv) > 2 else clip_path.with_name(clip_path.stem + "_check.png")
    with open(clip_path) as f:
        clip = json.load(f)
    plot_clip(clip, out_path)
    print(f"saved {out_path}")


if __name__ == "__main__":
    main()

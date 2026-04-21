"""
Run from the demo/ directory:
    python generate_figures.py

Outputs PNG files to demo/figures/.
Requires: matplotlib, numpy
    pip install matplotlib numpy
"""

import os
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np

os.makedirs("figures", exist_ok=True)

BLUE  = "#4C72B0"
ORANGE = "#DD8452"
GREEN  = "#55A868"
RED    = "#C44E52"
GRAY   = "#8C8C8C"

plt.rcParams.update({
    "font.family": "sans-serif",
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": True,
    "grid.alpha": 0.3,
    "figure.dpi": 150,
})


# ── Experiment 1a: Wall Time vs Workers ─────────────────────────────────────
workers = [1, 3, 5]
naive_time   = [3752.89, 1966.43, 1339.98]
cached_time  = [5201.88, 1919.52, 1157.03]

fig, ax = plt.subplots(figsize=(6, 4))
ax.plot(workers, naive_time,  "o-", color=BLUE,   label="Naive",        linewidth=2, markersize=7)
ax.plot(workers, cached_time, "s-", color=ORANGE, label="Redis Cached", linewidth=2, markersize=7)

# crossover annotation
ax.axvspan(2.5, 3.5, alpha=0.08, color=GREEN)
ax.annotate("crossover\n~3 workers", xy=(3, 1940), xytext=(3.3, 2800),
            arrowprops=dict(arrowstyle="->", color=GRAY), color=GRAY, fontsize=9)

ax.set_xlabel("Worker count")
ax.set_ylabel("Total wall time (s)")
ax.set_title("Exp 1 — Wall Time: Naive vs. Cached")
ax.set_xticks(workers)
ax.legend()
fig.tight_layout()
fig.savefig("figures/exp1_wall_time.png")
plt.close(fig)
print("saved figures/exp1_wall_time.png")


# ── Experiment 1b: Input Tokens vs Workers ───────────────────────────────────
naive_tokens  = [60117, 78242, 83274]
cached_tokens = [27909, 30422, 30664]

x = np.arange(len(workers))
w = 0.35

fig, ax = plt.subplots(figsize=(6, 4))
bars_n = ax.bar(x - w/2, naive_tokens,  w, label="Naive",        color=BLUE)
bars_c = ax.bar(x + w/2, cached_tokens, w, label="Redis Cached", color=ORANGE)

for bar in bars_n:
    ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 600,
            f"{int(bar.get_height()):,}", ha="center", va="bottom", fontsize=8)
for bar in bars_c:
    ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 600,
            f"{int(bar.get_height()):,}", ha="center", va="bottom", fontsize=8)

ax.set_xlabel("Worker count")
ax.set_ylabel("Total input tokens prefilled")
ax.set_title("Exp 1 — Prefill Tokens: Naive vs. Cached (~54% reduction)")
ax.set_xticks(x)
ax.set_xticklabels([f"{w} worker{'s' if w > 1 else ''}" for w in workers])
ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{int(v):,}"))
ax.legend()
fig.tight_layout()
fig.savefig("figures/exp1_tokens.png")
plt.close(fig)
print("saved figures/exp1_tokens.png")


# ── Experiment 2: Backend × Compression ─────────────────────────────────────
labels      = ["In-Memory\nCompress Off", "In-Memory\nCompress On",
               "Redis\nCompress Off",     "Redis\nCompress On"]
total_time  = [3925.53, 4949.32, 5159.06, 5201.88]
llm_latency = [3387.36, 4133.08, 4295.51, 4337.43]
bytes_gb    = [3.12, 0.82, 3.12, 0.82]

colors = [BLUE, BLUE, ORANGE, ORANGE]
alphas = [1.0, 0.5, 1.0, 0.5]

x = np.arange(len(labels))
w = 0.35

fig, ax1 = plt.subplots(figsize=(7, 4.5))
for i, (lbl, tt, la, col, alph) in enumerate(zip(labels, total_time, llm_latency, colors, alphas)):
    ax1.bar(i - w/2, tt, w, color=col, alpha=alph, label="Total time" if i == 0 else "")
    ax1.bar(i + w/2, la, w, color=col, alpha=alph * 0.6, label="LLM latency" if i == 0 else "")

# Legend patches
from matplotlib.patches import Patch
legend_elements = [
    Patch(facecolor=BLUE,   label="In-Memory"),
    Patch(facecolor=ORANGE, label="Redis"),
    Patch(facecolor="gray", alpha=1.0, label="Total time (left bar)"),
    Patch(facecolor="gray", alpha=0.5, label="LLM latency (right bar)"),
]
ax1.legend(handles=legend_elements, fontsize=8, loc="upper left")

# Secondary axis: bytes written
ax2 = ax1.twinx()
ax2.plot(x, bytes_gb, "D--", color=RED, linewidth=1.5, markersize=6, label="Bytes written (GB)")
ax2.set_ylabel("Data written to cache (GB)", color=RED)
ax2.tick_params(axis="y", labelcolor=RED)
ax2.spines["right"].set_visible(True)
ax2.set_ylim(0, 4.5)
ax2.legend(loc="upper right", fontsize=8)

ax1.set_ylabel("Time (s)")
ax1.set_title("Exp 2 — Backend & Compression Overhead (1 Worker)")
ax1.set_xticks(x)
ax1.set_xticklabels(labels, fontsize=9)
ax1.set_ylim(0, 6200)
fig.tight_layout()
fig.savefig("figures/exp2_backend_compression.png")
plt.close(fig)
print("saved figures/exp2_backend_compression.png")


# ── Experiment 3: Cache Ordering Strategies ──────────────────────────────────
strategies  = ["size_desc\n(baseline)", "size_asc", "freq +\nsize_desc", "freq +\nsize_asc"]
total_time  = [1919.52, 1500.18, 2060.97, 1333.83]
llm_latency = [4346.59, 3261.74, 4885.25, 2986.35]
tokens      = [30422, 25092, 30631, 26800]

bar_colors = [GRAY, BLUE, RED, GREEN]
x = np.arange(len(strategies))
w = 0.3

fig, axes = plt.subplots(1, 2, figsize=(10, 4.5))

# Left: wall time + LLM latency
ax = axes[0]
bars_t = ax.bar(x - w/2, total_time,  w, color=bar_colors, alpha=0.9, label="Total wall time")
bars_l = ax.bar(x + w/2, llm_latency, w, color=bar_colors, alpha=0.5, label="LLM latency")

for bar in bars_t:
    ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 30,
            f"{bar.get_height():.0f}s", ha="center", va="bottom", fontsize=8)

ax.set_ylabel("Time (s)")
ax.set_title("Wall Time & LLM Latency by Ordering")
ax.set_xticks(x)
ax.set_xticklabels(strategies, fontsize=8)
ax.legend(fontsize=8)
ax.axhline(total_time[0], color=GRAY, linewidth=1, linestyle="--", alpha=0.5)

# Right: input tokens
ax = axes[1]
bars_tok = ax.bar(x, tokens, color=bar_colors, alpha=0.9)
for bar in bars_tok:
    ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 200,
            f"{int(bar.get_height()):,}", ha="center", va="bottom", fontsize=8)

ax.set_ylabel("Total input tokens prefilled")
ax.set_title("Prefill Tokens by Ordering")
ax.set_xticks(x)
ax.set_xticklabels(strategies, fontsize=8)
ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{int(v):,}"))
ax.axhline(tokens[0], color=GRAY, linewidth=1, linestyle="--", alpha=0.5)

fig.suptitle("Exp 3 — Smart Caching Order (3 Workers, Redis, Compress On)", fontsize=11, y=1.01)
fig.tight_layout()
fig.savefig("figures/exp3_ordering.png", bbox_inches="tight")
plt.close(fig)
print("saved figures/exp3_ordering.png")

print("\nAll figures written to demo/figures/")

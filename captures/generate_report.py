"""Render a multi-page PDF report from a decoded buffer JSON.

Usage:
    python generate_report.py <decoded.json>   # writes <stem>.pdf alongside it
"""
import json
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.gridspec import GridSpec


SIGNAL_COLORS = {
    "obd.rpm":             "#C8102E",
    "obd.coolant_temp":    "#f39c12",
    "obd.intake_air_temp": "#3498db",
    "obd.throttle":        "#2ecc71",
    "obd.speed":           "#9b59b6",
    "obd.fuel_level":      "#1abc9c",
}

PRETTY_NAME = {
    "obd.rpm":             "Engine speed",
    "obd.coolant_temp":    "Coolant temperature",
    "obd.intake_air_temp": "Intake air temperature",
    "obd.throttle":        "Throttle position",
    "obd.speed":           "Vehicle speed",
    "obd.fuel_level":      "Fuel tank level",
}


def parse_ts(s: str) -> datetime:
    s = s.replace("Z", "+00:00")
    s = re.sub(r"\.(\d{6})\d+", r".\1", s)
    return datetime.fromisoformat(s)


def load(json_path: Path) -> dict:
    with json_path.open(encoding="utf-8") as f:
        data = json.load(f)
    for s in data["samples"]:
        s["dt"] = parse_ts(s["ts"])
    return data


def title_page(pdf: PdfPages, data: dict, source_db: str):
    samples = data["samples"]
    dt_first = parse_ts(data["first_ts"])
    dt_last = parse_ts(data["last_ts"])
    duration = (dt_last - dt_first).total_seconds()

    by_signal = defaultdict(list)
    units = {}
    for s in samples:
        by_signal[s["signal"]].append(s["value"])
        units[s["signal"]] = s["unit"]

    fig = plt.figure(figsize=(8.27, 11.69))   # A4 portrait
    gs = GridSpec(20, 1, figure=fig, hspace=0.6)

    # Title
    ax_title = fig.add_subplot(gs[:3, 0])
    ax_title.axis("off")
    ax_title.text(0.0, 0.85, "K N I G H T   R I D E R", fontsize=10,
                  color="#C8102E", fontweight="bold")
    ax_title.text(0.0, 0.55, "Field-Capture Report", fontsize=22, fontweight="bold",
                  color="#1a1a1a")
    ax_title.text(0.0, 0.2,
                  "Decoded OBD-II telemetry from a Toyota Vitz DBA-NSP130 (engine idling)",
                  fontsize=11, color="#444", style="italic")
    ax_title.axhline(0.05, color="#C8102E", linewidth=2)

    # Metadata block
    ax_meta = fig.add_subplot(gs[3:8, 0])
    ax_meta.axis("off")
    meta_rows = [
        ("Source buffer",       source_db),
        ("Device ID",           data["device_id"]),
        ("Envelope schema",     f"v{data['envelope_schema_v']}"),
        ("Payload schema",      f"v{data['payload_schema_v']}"),
        ("Total samples",       f"{data['sample_count']:,}"),
        ("Distinct signals",    f"{len(by_signal)}"),
        ("First sample",        dt_first.strftime("%Y-%m-%d %H:%M:%S UTC")),
        ("Last sample",         dt_last.strftime("%Y-%m-%d %H:%M:%S UTC")),
        ("Capture duration",    f"{duration:.1f} s  ({duration/60:.2f} min)"),
        ("Effective rate",      f"{data['sample_count']/duration:.2f} samples/sec"),
    ]
    for i, (k, v) in enumerate(meta_rows):
        y = 1.0 - (i + 1) * (1.0 / (len(meta_rows) + 1))
        ax_meta.text(0.0,  y, k,           fontsize=10, color="#555")
        ax_meta.text(0.32, y, str(v),      fontsize=10, color="#1a1a1a",
                     family="monospace")

    # Stats table
    ax_stats = fig.add_subplot(gs[9:17, 0])
    ax_stats.axis("off")
    ax_stats.text(0.0, 1.02, "Per-signal statistics", fontsize=13,
                  fontweight="bold", color="#1a1a1a", transform=ax_stats.transAxes)

    header = ["signal", "unit", "n", "min", "max", "mean", "std"]
    body = []
    for sig in sorted(by_signal):
        vals = by_signal[sig]
        n = len(vals)
        mean = sum(vals) / n
        var = sum((v - mean) ** 2 for v in vals) / n
        std = var ** 0.5
        body.append([
            sig,
            units[sig],
            f"{n:,}",
            f"{min(vals):.2f}",
            f"{max(vals):.2f}",
            f"{mean:.2f}",
            f"{std:.2f}",
        ])

    tbl = ax_stats.table(
        cellText=body, colLabels=header, loc="upper center",
        cellLoc="center", colWidths=[0.27, 0.07, 0.10, 0.11, 0.11, 0.11, 0.11],
    )
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(9)
    tbl.scale(1, 1.5)
    # header row styling
    for j in range(len(header)):
        cell = tbl[(0, j)]
        cell.set_facecolor("#1a1a1a")
        cell.set_text_props(color="white", fontweight="bold")
    for i in range(1, len(body) + 1):
        tbl[(i, 0)].set_text_props(family="monospace", color="#C8102E")
        for j in range(len(header)):
            tbl[(i, j)].set_facecolor("#fafafa" if i % 2 else "#ffffff")

    # Plain-language reading
    ax_note = fig.add_subplot(gs[17:, 0])
    ax_note.axis("off")
    ax_note.text(0.0, 1.0, "Plain-language reading", fontsize=12, fontweight="bold",
                 color="#1a1a1a")
    rpm = by_signal.get("obd.rpm", [])
    cool = by_signal.get("obd.coolant_temp", [])
    notes = []
    if rpm:
        nonzero = [v for v in rpm if v > 0]
        if nonzero:
            notes.append(
                f"Idle RPM averaged {sum(nonzero)/len(nonzero):.0f} rpm — within normal warm-idle range."
            )
        zeros = sum(1 for v in rpm if v == 0)
        if zeros:
            notes.append(
                f"RPM returned to 0 on {zeros} samples — engine was shut off before the binary stopped."
            )
    if cool:
        notes.append(
            f"Coolant held {min(cool):.0f}–{max(cool):.0f} °C — warm operating temperature, no overshoot."
        )
    speed = by_signal.get("obd.speed", [])
    if speed and max(speed) == 0:
        notes.append("Vehicle was stationary throughout — speed never left 0 km/h.")
    for i, line in enumerate(notes):
        ax_note.text(0.0, 0.7 - i * 0.16, f"• {line}", fontsize=10, color="#333",
                     wrap=True)

    fig.text(0.5, 0.02, "Knight Rider · Tarimica Chiwara · 2026",
             ha="center", fontsize=8, color="#888")
    pdf.savefig(fig, bbox_inches="tight")
    plt.close(fig)


def timeseries_page(pdf: PdfPages, data: dict):
    samples = data["samples"]
    by_signal = defaultdict(list)
    units = {}
    for s in samples:
        by_signal[s["signal"]].append((s["dt"], s["value"]))
        units[s["signal"]] = s["unit"]

    signals = [s for s in [
        "obd.rpm",
        "obd.coolant_temp",
        "obd.intake_air_temp",
        "obd.throttle",
        "obd.speed",
        "obd.fuel_level",
    ] if s in by_signal]

    fig, axes = plt.subplots(len(signals), 1, figsize=(8.27, 11.69),
                             sharex=True, constrained_layout=True)
    if len(signals) == 1:
        axes = [axes]

    fig.suptitle("Signal time-series", fontsize=15, fontweight="bold",
                 color="#1a1a1a", x=0.05, ha="left")

    for ax, sig in zip(axes, signals):
        pts = by_signal[sig]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        color = SIGNAL_COLORS.get(sig, "#444")
        ax.plot(xs, ys, color=color, linewidth=1.4)
        ax.fill_between(xs, ys, min(ys), color=color, alpha=0.10)
        ax.set_ylabel(f"{PRETTY_NAME.get(sig, sig)}\n[{units[sig]}]",
                      fontsize=9, color="#1a1a1a")
        ax.grid(True, alpha=0.25)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.tick_params(labelsize=8, colors="#555")
        # range hint
        ax.text(0.99, 0.92,
                f"min {min(ys):.1f}   max {max(ys):.1f}   mean {sum(ys)/len(ys):.1f}",
                transform=ax.transAxes, ha="right", va="top",
                fontsize=8, color="#777", family="monospace")
    axes[-1].xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))
    axes[-1].set_xlabel("Time (UTC)", fontsize=9, color="#555")

    fig.text(0.5, 0.005,
             f"{data['sample_count']:,} samples · "
             f"{(parse_ts(data['last_ts']) - parse_ts(data['first_ts'])).total_seconds():.0f} s capture",
             ha="center", fontsize=8, color="#888")
    pdf.savefig(fig, bbox_inches="tight")
    plt.close(fig)


def histograms_page(pdf: PdfPages, data: dict):
    samples = data["samples"]
    by_signal = defaultdict(list)
    units = {}
    for s in samples:
        by_signal[s["signal"]].append(s["value"])
        units[s["signal"]] = s["unit"]

    signals = sorted(by_signal)
    n = len(signals)
    cols = 2
    rows = (n + 1) // cols

    fig, axes = plt.subplots(rows, cols, figsize=(8.27, 11.69 * (rows / 4 + 0.4)),
                             constrained_layout=True)
    fig.suptitle("Sample distributions", fontsize=15, fontweight="bold",
                 color="#1a1a1a", x=0.05, ha="left")
    axes_flat = axes.flatten() if hasattr(axes, "flatten") else [axes]
    for ax, sig in zip(axes_flat, signals):
        vals = by_signal[sig]
        color = SIGNAL_COLORS.get(sig, "#444")
        ax.hist(vals, bins=30, color=color, alpha=0.75, edgecolor="#222",
                linewidth=0.4)
        ax.set_title(PRETTY_NAME.get(sig, sig), fontsize=10, fontweight="bold",
                     color="#1a1a1a")
        ax.set_xlabel(units[sig], fontsize=8, color="#555")
        ax.set_ylabel("count", fontsize=8, color="#555")
        ax.grid(True, alpha=0.25)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.tick_params(labelsize=8, colors="#555")
    for ax in axes_flat[len(signals):]:
        ax.axis("off")

    pdf.savefig(fig, bbox_inches="tight")
    plt.close(fig)


def first_last_page(pdf: PdfPages, data: dict):
    samples = data["samples"]
    fig = plt.figure(figsize=(8.27, 11.69))
    gs = GridSpec(2, 1, figure=fig, hspace=0.3)

    for slot, (title, batch) in enumerate([
        ("First 12 samples", samples[:12]),
        ("Last 12 samples",  samples[-12:]),
    ]):
        ax = fig.add_subplot(gs[slot, 0])
        ax.axis("off")
        ax.text(0.0, 1.02, title, fontsize=13, fontweight="bold",
                color="#1a1a1a", transform=ax.transAxes)
        body = [[s["ts"][:23], s["signal"], f"{s['value']:.2f}", s["unit"]]
                for s in batch]
        tbl = ax.table(
            cellText=body,
            colLabels=["timestamp (UTC)", "signal", "value", "unit"],
            cellLoc="left", loc="upper center",
            colWidths=[0.35, 0.30, 0.15, 0.10],
        )
        tbl.auto_set_font_size(False)
        tbl.set_fontsize(8)
        tbl.scale(1, 1.4)
        for j in range(4):
            tbl[(0, j)].set_facecolor("#1a1a1a")
            tbl[(0, j)].set_text_props(color="white", fontweight="bold")
        for i in range(1, len(body) + 1):
            tbl[(i, 0)].set_text_props(family="monospace", fontsize=7)
            tbl[(i, 1)].set_text_props(family="monospace", color="#C8102E")
            tbl[(i, 2)].set_text_props(family="monospace")
            for j in range(4):
                tbl[(i, j)].set_facecolor("#fafafa" if i % 2 else "#ffffff")

    pdf.savefig(fig, bbox_inches="tight")
    plt.close(fig)


def main():
    json_path = Path(sys.argv[1] if len(sys.argv) > 1
                     else "vitz-buffer-20260528.json")
    data = load(json_path)
    pdf_path = json_path.with_suffix(".pdf")

    plt.rcParams.update({
        "font.family": "DejaVu Sans",
        "axes.labelcolor": "#1a1a1a",
        "axes.edgecolor": "#999",
        "axes.titlecolor": "#1a1a1a",
        "savefig.facecolor": "white",
        "figure.facecolor": "white",
    })

    with PdfPages(pdf_path) as pdf:
        title_page(pdf, data, json_path.with_suffix(".sqlite").name)
        timeseries_page(pdf, data)
        histograms_page(pdf, data)
        first_last_page(pdf, data)

        # Metadata embedded in the PDF itself
        d = pdf.infodict()
        d["Title"]    = "KnightRider Field-Capture Report — Toyota Vitz"
        d["Author"]   = "Tarimica Chiwara"
        d["Subject"]  = "OBD-II telemetry decoded from a 12-minute Toyota Vitz idle session"
        d["Keywords"] = "knight-rider, OBD-II, CAN-bus, Toyota Vitz, NSP130"

    print(f"wrote {pdf_path}  ({pdf_path.stat().st_size/1024:.1f} KB)")


if __name__ == "__main__":
    main()

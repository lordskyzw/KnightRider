"""Render a comprehensive, multi-page PDF report from a decoded buffer JSON.

Usage:
    python generate_report.py <decoded.json>   # writes <stem>.pdf alongside it

Pages: cover + executive summary · diagnostics (DTCs) · per-signal statistics
(paginated) · signal time-series (paginated) · sample distributions
(paginated) · sample log. Layout is paginated so nothing overlaps regardless
of how many signals a capture contains.
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
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle

A4 = (8.27, 11.69)
RED = "#C8102E"
INK = "#1a1a1a"
MUTED = "#555555"
GREEN = "#2e7d32"
AMBER = "#b8860b"
LEFT = 0.09

SIGNAL_COLORS = {
    "obd.rpm": "#C8102E", "obd.coolant_temp": "#f39c12",
    "obd.intake_air_temp": "#3498db", "obd.throttle": "#2ecc71",
    "obd.speed": "#9b59b6", "obd.fuel_level": "#1abc9c",
    "obd.battery_v": "#e67e22", "obd.maf": "#16a085",
    "obd.cat_temp_b1s1": "#d35400", "obd.cat_temp_b1s2": "#e74c3c",
    "obd.timing_advance": "#8e44ad", "obd.engine_load": "#c0392b",
    "obd.commanded_lambda": "#2980b9", "obd.o2_b1s2_v": "#27ae60",
}

PRETTY_NAME = {
    "obd.rpm": "Engine speed", "obd.coolant_temp": "Coolant temperature",
    "obd.intake_air_temp": "Intake air temperature", "obd.throttle": "Throttle position",
    "obd.speed": "Vehicle speed (OBD)", "obd.fuel_level": "Fuel tank level",
    "obd.battery_v": "Control-module voltage", "obd.engine_load": "Engine load",
    "obd.abs_load": "Absolute load", "obd.maf": "Mass air flow",
    "obd.map": "Manifold pressure", "obd.timing_advance": "Timing advance",
    "obd.stft_b1": "Short-term fuel trim B1", "obd.ltft_b1": "Long-term fuel trim B1",
    "obd.o2_b1s2_v": "O2 sensor B1S2", "obd.run_time_s": "Run time since start",
    "obd.cat_temp_b1s1": "Catalyst temp B1S1", "obd.cat_temp_b1s2": "Catalyst temp B1S2",
    "obd.baro_pressure": "Barometric pressure", "obd.commanded_lambda": "Commanded lambda",
    "obd.ambient_air_temp": "Ambient air temp", "obd.oil_temp": "Engine oil temp",
    "obd.dtc_count": "Stored DTC count",
    "dbc.toyota.SPEED.speed": "Vehicle speed (CAN sniffer)",
    "dbc.toyota.BRAKE.pressed": "Brake pressed (CAN)",
    "dbc.toyota.STOP_LAMP.on": "Stop lamp (CAN)",
    "dbc.toyota.DOORS.driver": "Driver door open (CAN)",
}

# Preferred plotting order (present signals only); rest appended alphabetically.
PLOT_ORDER = [
    "obd.rpm", "obd.engine_load", "obd.timing_advance",
    "obd.coolant_temp", "obd.intake_air_temp", "obd.cat_temp_b1s1", "obd.cat_temp_b1s2",
    "obd.maf", "obd.map", "obd.throttle", "obd.commanded_lambda",
    "obd.stft_b1", "obd.ltft_b1", "obd.o2_b1s2_v",
    "obd.battery_v", "obd.baro_pressure", "obd.fuel_level", "obd.run_time_s",
    "obd.speed", "dbc.toyota.SPEED.speed",
]

DTC_DESCRIPTIONS = {
    "p0420": "Catalyst System Efficiency Below Threshold (Bank 1)",
    "p0430": "Catalyst System Efficiency Below Threshold (Bank 2)",
    "p0171": "System Too Lean (Bank 1)", "p0174": "System Too Lean (Bank 2)",
    "p0172": "System Too Rich (Bank 1)",
    "p0300": "Random/Multiple Cylinder Misfire Detected",
    "p0301": "Cylinder 1 Misfire", "p0302": "Cylinder 2 Misfire",
    "p0303": "Cylinder 3 Misfire", "p0304": "Cylinder 4 Misfire",
    "p0128": "Coolant Thermostat Below Regulating Temperature",
    "p0442": "Evaporative Emission System Leak (small)",
    "p0455": "Evaporative Emission System Leak (large)",
    "p0011": "Camshaft Position — Timing Over-Advanced (Bank 1)",
}

DTC_MEANING = {
    "p0420": "Catalytic converter is converting exhaust gases less efficiently "
             "than the ECU expects (downstream O2 sensor tracks the upstream one "
             "too closely). Often an ageing catalyst or an O2-sensor issue; "
             "emissions-related, not immediately drivability-affecting.",
}


def describe_dtc(code):
    return DTC_DESCRIPTIONS.get(code.lower(), "Stored fault code — see service manual")


def meaning_dtc(code):
    return DTC_MEANING.get(code.lower())


def parse_ts(s):
    s = s.replace("Z", "+00:00")
    s = re.sub(r"\.(\d{6})\d+", r".\1", s)
    return datetime.fromisoformat(s)


def load(json_path):
    with json_path.open(encoding="utf-8") as f:
        data = json.load(f)
    for s in data["samples"]:
        s["dt"] = parse_ts(s["ts"])
    return data


def stats_of(values):
    n = len(values)
    mean = sum(values) / n
    std = (sum((v - mean) ** 2 for v in values) / n) ** 0.5
    return n, min(values), max(values), mean, std


def footer(fig, page, total, note=""):
    fig.text(0.5, 0.03,
             f"Knight Rider · Tarimica Chiwara · 2026      ·      page {page} of {total}",
             ha="center", fontsize=8, color="#888")
    if note:
        fig.text(0.5, 0.015, note, ha="center", fontsize=7.5, color="#aaa")


# ── Cover + executive summary ────────────────────────────────────────────────
def cover_page(pdf, data, source_db, page, total):
    samples = data["samples"]
    dt_first, dt_last = parse_ts(data["first_ts"]), parse_ts(data["last_ts"])
    duration = (dt_last - dt_first).total_seconds()
    by_signal = defaultdict(list)
    for s in samples:
        by_signal[s["signal"]].append(s["value"])
    dtcs = sorted({sig.rsplit(".", 1)[-1].upper()
                   for sig in by_signal if sig.startswith("dtc.stored.")})
    n_obd = sum(1 for s in samples if s["signal"].startswith("obd."))
    n_can = sum(1 for s in samples if s["signal"].startswith("dbc."))

    fig = plt.figure(figsize=A4)
    fig.text(LEFT, 0.955, "K N I G H T   R I D E R", fontsize=10, color=RED,
             fontweight="bold")
    fig.text(LEFT, 0.925, "Field-Capture Report", fontsize=23, fontweight="bold",
             color=INK)
    fig.text(LEFT, 0.902, "Decoded OBD-II + CAN telemetry capture", fontsize=11,
             color="#444", style="italic")
    fig.add_artist(Line2D([LEFT, 0.91], [0.893, 0.893], color=RED, lw=2,
                          transform=fig.transFigure))

    # Metadata (two columns of key/value)
    meta = [
        ("Source buffer", source_db),
        ("Device ID", data["device_id"]),
        ("Schema", f"envelope v{data['envelope_schema_v']} · payload v{data['payload_schema_v']}"),
        ("First sample", dt_first.strftime("%Y-%m-%d %H:%M:%S UTC")),
        ("Last sample", dt_last.strftime("%Y-%m-%d %H:%M:%S UTC")),
        ("Duration", f"{duration:.1f} s  ({duration/60:.2f} min)"),
        ("Total samples", f"{data['sample_count']:,}"),
        ("Effective rate", f"{data['sample_count']/duration:.1f} samples/sec"),
        ("Distinct signals", f"{len(by_signal)}"),
        ("Sources", f"{n_obd:,} OBD-poll · {n_can:,} CAN-sniffer"),
    ]
    y = 0.862
    for k, v in meta:
        fig.text(LEFT, y, k, fontsize=10, color=MUTED)
        fig.text(0.34, y, str(v), fontsize=10, color=INK, family="monospace")
        y -= 0.0265

    # Fault banner
    y -= 0.01
    if dtcs:
        fig.add_artist(Rectangle((LEFT, y - 0.008), 0.82, 0.040,
                                 transform=fig.transFigure, facecolor="#fdecea",
                                 edgecolor=RED, lw=1.2))
        fig.text(LEFT + 0.015, y + 0.005,
                 f"⚠  {len(dtcs)} stored DTC(s): " + ", ".join(dtcs)
                 + "   —   see Diagnostics page",
                 fontsize=10.5, color=RED, fontweight="bold")
    else:
        fig.add_artist(Rectangle((LEFT, y - 0.008), 0.82, 0.040,
                                 transform=fig.transFigure, facecolor="#eaf5ea",
                                 edgecolor=GREEN, lw=1.2))
        fig.text(LEFT + 0.015, y + 0.005, "✓  No stored DTCs in this capture.",
                 fontsize=10.5, color=GREEN, fontweight="bold")
    y -= 0.05

    # Executive summary
    fig.text(LEFT, y, "Executive summary", fontsize=14, fontweight="bold", color=INK)
    y -= 0.032
    notes = []
    rpm = [v for v in by_signal.get("obd.rpm", []) if v > 0]
    if rpm:
        notes.append(f"Engine speed ranged {min(rpm):.0f}–{max(rpm):.0f} rpm "
                     f"(mean {sum(rpm)/len(rpm):.0f}).")
    cool = by_signal.get("obd.coolant_temp", [])
    if cool:
        notes.append(f"Coolant warmed from {min(cool):.0f} to {max(cool):.0f} °C "
                     "over the session.")
    batt = by_signal.get("obd.battery_v", [])
    if batt:
        notes.append(f"Control-module voltage {min(batt):.2f}–{max(batt):.2f} V — "
                     "the engine-off vs running delta reflects alternator charging.")
    cat = by_signal.get("obd.cat_temp_b1s1", [])
    if cat:
        notes.append(f"Catalyst (B1S1) reached {max(cat):.0f} °C "
                     "(relevant to any catalyst-efficiency DTC).")
    spd = by_signal.get("obd.speed", []) or by_signal.get("dbc.toyota.SPEED.speed", [])
    if spd is not None and spd and max(spd) == 0:
        notes.append("Vehicle was stationary throughout — speed never left 0.")
    if dtcs:
        for c in dtcs:
            notes.append(f"Stored fault {c}: {describe_dtc(c)}.")
    notes.append(f"Captured {len(by_signal)} distinct signals across "
                 f"{data['sample_count']:,} samples ({n_obd:,} via OBD polling, "
                 f"{n_can:,} via passive CAN sniffing).")
    for line in notes:
        # simple wrap at ~95 chars
        wrapped = _wrap(line, 92)
        fig.text(LEFT + 0.012, y, "•", fontsize=10, color=RED)
        for wl in wrapped:
            fig.text(LEFT + 0.03, y, wl, fontsize=10, color="#333")
            y -= 0.022
        y -= 0.006

    footer(fig, page, total)
    pdf.savefig(fig)
    plt.close(fig)


def _wrap(text, width):
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        lines.append(cur)
    return lines


# ── Diagnostics (DTC) page ───────────────────────────────────────────────────
def dtc_page(pdf, data, page, total):
    samples = data["samples"]
    by_signal = defaultdict(list)
    for s in samples:
        by_signal[s["signal"]].append(s["value"])
    dtcs = sorted({sig.rsplit(".", 1)[-1].upper()
                   for sig in by_signal if sig.startswith("dtc.stored.")})
    dtc_count = by_signal.get("obd.dtc_count", [])
    sweeps = len(dtc_count)

    fig = plt.figure(figsize=A4)
    fig.text(LEFT, 0.945, "Diagnostics — Trouble Codes (Mode 03)", fontsize=16,
             fontweight="bold", color=INK)
    fig.add_artist(Line2D([LEFT, 0.91], [0.935, 0.935], color=RED, lw=1.5,
                          transform=fig.transFigure))
    y = 0.90
    fig.text(LEFT, y, f"Stored-DTC sweeps in capture: {sweeps}   ·   "
             f"distinct stored codes: {len(dtcs)}", fontsize=10, color=MUTED)
    y -= 0.04

    if not dtcs:
        fig.add_artist(Rectangle((LEFT, y - 0.03), 0.82, 0.05,
                                 transform=fig.transFigure, facecolor="#eaf5ea",
                                 edgecolor=GREEN, lw=1.2))
        fig.text(LEFT + 0.015, y - 0.012, "✓  No diagnostic trouble codes "
                 "were stored during this capture.", fontsize=12, color=GREEN,
                 fontweight="bold")
    else:
        for code in dtcs:
            mean = meaning_dtc(code)
            mean_lines = _wrap("What it means: " + mean, 88) if mean else []
            # box height = header + description + meaning lines + occurrence line
            box_h = 0.052 + 0.020 * len(mean_lines) + 0.024
            fig.add_artist(Rectangle((LEFT, y - box_h + 0.018), 0.82, box_h,
                                     transform=fig.transFigure, facecolor="#fdecea",
                                     edgecolor=RED, lw=1.0))
            fig.text(LEFT + 0.015, y - 0.005, f"⚠  {code}", fontsize=15,
                     fontweight="bold", color=RED)
            fig.text(LEFT + 0.02, y - 0.034, describe_dtc(code), fontsize=11,
                     color=INK, fontweight="bold")
            yy = y - 0.058
            for wl in mean_lines:
                fig.text(LEFT + 0.02, yy, wl, fontsize=9.5, color="#444")
                yy -= 0.020
            n_occ = len(by_signal.get(f"dtc.stored.{code.lower()}", []))
            fig.text(LEFT + 0.02, yy, f"Observed on {n_occ} of {sweeps} Mode-03 "
                     "sweeps (persistent across the capture).", fontsize=9.5,
                     color="#444")
            y -= box_h + 0.025

    # Methodology note
    y -= 0.03
    fig.text(LEFT, y, "How these are read", fontsize=12, fontweight="bold", color=INK)
    y -= 0.028
    for wl in _wrap("The Pi issues an OBD-II Mode 03 (stored DTCs) request every "
                    "30 s and diffs the result: new codes emit dtc.stored.<code>, "
                    "cleared codes emit dtc.cleared.<code>, and obd.dtc_count is a "
                    "heartbeat of the current count. Reads are non-intrusive "
                    "(read-only); the project never clears codes (Mode 04).", 95):
        fig.text(LEFT + 0.012, y, wl, fontsize=9.5, color="#444")
        y -= 0.021

    footer(fig, page, total)
    pdf.savefig(fig)
    plt.close(fig)


# ── Per-signal statistics (paginated table) ──────────────────────────────────
def stats_pages(pdf, data, page, total, rows_per_page=24):
    by_signal, units = defaultdict(list), {}
    for s in data["samples"]:
        by_signal[s["signal"]].append(s["value"])
        units[s["signal"]] = s["unit"]
    sigs = sorted(by_signal)
    chunks = [sigs[i:i + rows_per_page] for i in range(0, len(sigs), rows_per_page)]
    pages_used = 0
    for ci, chunk in enumerate(chunks):
        fig = plt.figure(figsize=A4)
        ttl = "Per-signal statistics" + (f"  ({ci+1}/{len(chunks)})" if len(chunks) > 1 else "")
        fig.text(LEFT, 0.945, ttl, fontsize=16, fontweight="bold", color=INK)
        fig.add_artist(Line2D([LEFT, 0.91], [0.935, 0.935], color=RED, lw=1.5,
                              transform=fig.transFigure))
        ax = fig.add_axes([0.06, 0.06, 0.88, 0.85])
        ax.axis("off")
        header = ["signal", "unit", "n", "min", "max", "mean", "std"]
        body = []
        for sig in chunk:
            n, lo, hi, mean, std = stats_of(by_signal[sig])
            body.append([sig, units.get(sig, ""), f"{n:,}", f"{lo:.2f}",
                         f"{hi:.2f}", f"{mean:.2f}", f"{std:.2f}"])
        tbl = ax.table(cellText=body, colLabels=header, loc="upper center",
                       cellLoc="center",
                       colWidths=[0.30, 0.08, 0.10, 0.11, 0.11, 0.11, 0.11])
        tbl.auto_set_font_size(False)
        tbl.set_fontsize(8.5)
        tbl.scale(1, 1.45)
        for j in range(len(header)):
            c = tbl[(0, j)]
            c.set_facecolor(INK)
            c.set_text_props(color="white", fontweight="bold")
        for i in range(1, len(body) + 1):
            tbl[(i, 0)].set_text_props(family="monospace", color=RED)
            for j in range(len(header)):
                tbl[(i, j)].set_facecolor("#fafafa" if i % 2 else "#ffffff")
        footer(fig, page + pages_used, total)
        pdf.savefig(fig)
        plt.close(fig)
        pages_used += 1
    return pages_used


# ── Time-series (paginated) ──────────────────────────────────────────────────
def plottable_signals(by_signal):
    sigs = [s for s in PLOT_ORDER if s in by_signal]
    extra = sorted(s for s in by_signal
                   if s not in sigs and not s.startswith("dtc.stored."))
    return sigs + extra


def timeseries_pages(pdf, data, page, total, per_page=5):
    by_signal, units = defaultdict(list), {}
    for s in data["samples"]:
        by_signal[s["signal"]].append((s["dt"], s["value"]))
        units[s["signal"]] = s["unit"]
    sigs = plottable_signals(by_signal)
    chunks = [sigs[i:i + per_page] for i in range(0, len(sigs), per_page)]
    pages_used = 0
    for ci, chunk in enumerate(chunks):
        fig, axes = plt.subplots(len(chunk), 1, figsize=A4, sharex=True,
                                 constrained_layout=True)
        if len(chunk) == 1:
            axes = [axes]
        ttl = "Signal time-series" + (f"  ({ci+1}/{len(chunks)})" if len(chunks) > 1 else "")
        fig.suptitle(ttl, fontsize=15, fontweight="bold", color=INK, x=0.06, ha="left")
        for ax, sig in zip(axes, chunk):
            pts = by_signal[sig]
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            color = SIGNAL_COLORS.get(sig, "#34495e")
            ax.plot(xs, ys, color=color, linewidth=1.3)
            ax.fill_between(xs, ys, min(ys), color=color, alpha=0.10)
            ax.set_ylabel(f"{PRETTY_NAME.get(sig, sig)}\n[{units.get(sig,'')}]",
                          fontsize=8.5, color=INK)
            ax.grid(True, alpha=0.25)
            ax.spines["top"].set_visible(False)
            ax.spines["right"].set_visible(False)
            ax.tick_params(labelsize=8, colors=MUTED)
            ax.text(0.99, 0.93,
                    f"min {min(ys):.1f}   max {max(ys):.1f}   mean {sum(ys)/len(ys):.1f}",
                    transform=ax.transAxes, ha="right", va="top", fontsize=7.5,
                    color="#777", family="monospace")
        axes[-1].xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))
        axes[-1].set_xlabel("Time (UTC)", fontsize=9, color=MUTED)
        footer(fig, page + pages_used, total)
        pdf.savefig(fig)
        plt.close(fig)
        pages_used += 1
    return pages_used


# ── Distributions (paginated) ────────────────────────────────────────────────
def histogram_pages(pdf, data, page, total, per_page=6):
    by_signal, units = defaultdict(list), {}
    for s in data["samples"]:
        by_signal[s["signal"]].append(s["value"])
        units[s["signal"]] = s["unit"]
    sigs = plottable_signals(by_signal)
    chunks = [sigs[i:i + per_page] for i in range(0, len(sigs), per_page)]
    pages_used = 0
    for ci, chunk in enumerate(chunks):
        fig, axes = plt.subplots(3, 2, figsize=A4, constrained_layout=True)
        ttl = "Sample distributions" + (f"  ({ci+1}/{len(chunks)})" if len(chunks) > 1 else "")
        fig.suptitle(ttl, fontsize=15, fontweight="bold", color=INK, x=0.06, ha="left")
        flat = axes.flatten()
        for ax, sig in zip(flat, chunk):
            vals = by_signal[sig]
            color = SIGNAL_COLORS.get(sig, "#34495e")
            ax.hist(vals, bins=30, color=color, alpha=0.78, edgecolor="#222",
                    linewidth=0.4)
            ax.set_title(PRETTY_NAME.get(sig, sig), fontsize=10, fontweight="bold",
                         color=INK)
            ax.set_xlabel(units.get(sig, ""), fontsize=8, color=MUTED)
            ax.set_ylabel("count", fontsize=8, color=MUTED)
            ax.grid(True, alpha=0.25)
            ax.spines["top"].set_visible(False)
            ax.spines["right"].set_visible(False)
            ax.tick_params(labelsize=7.5, colors=MUTED)
        for ax in flat[len(chunk):]:
            ax.axis("off")
        footer(fig, page + pages_used, total)
        pdf.savefig(fig)
        plt.close(fig)
        pages_used += 1
    return pages_used


# ── Sample log ───────────────────────────────────────────────────────────────
def sample_log_page(pdf, data, page, total):
    samples = data["samples"]
    fig = plt.figure(figsize=A4)
    fig.text(LEFT, 0.945, "Sample log (first & last)", fontsize=16,
             fontweight="bold", color=INK)
    fig.add_artist(Line2D([LEFT, 0.91], [0.935, 0.935], color=RED, lw=1.5,
                          transform=fig.transFigure))
    for slot, (title, batch, top) in enumerate([
        ("First 15 samples", samples[:15], 0.46),
        ("Last 15 samples", samples[-15:], 0.46),
    ]):
        ax = fig.add_axes([0.06, 0.50 - slot * 0.44, 0.88, 0.38])
        ax.axis("off")
        ax.text(0.0, 1.04, title, fontsize=12, fontweight="bold", color=INK,
                transform=ax.transAxes)
        body = [[s["ts"][:23], s["signal"], f"{s['value']:.2f}", s["unit"]]
                for s in batch]
        tbl = ax.table(cellText=body,
                       colLabels=["timestamp (UTC)", "signal", "value", "unit"],
                       cellLoc="left", loc="upper center",
                       colWidths=[0.34, 0.34, 0.14, 0.10])
        tbl.auto_set_font_size(False)
        tbl.set_fontsize(8)
        tbl.scale(1, 1.35)
        for j in range(4):
            tbl[(0, j)].set_facecolor(INK)
            tbl[(0, j)].set_text_props(color="white", fontweight="bold")
        for i in range(1, len(body) + 1):
            tbl[(i, 0)].set_text_props(family="monospace", fontsize=7)
            tbl[(i, 1)].set_text_props(family="monospace", color=RED)
            tbl[(i, 2)].set_text_props(family="monospace")
            for j in range(4):
                tbl[(i, j)].set_facecolor("#fafafa" if i % 2 else "#ffffff")
    footer(fig, page, total)
    pdf.savefig(fig)
    plt.close(fig)


def main():
    json_path = Path(sys.argv[1] if len(sys.argv) > 1 else "vitz-buffer-20260528.json")
    data = load(json_path)
    pdf_path = json_path.with_suffix(".pdf")

    plt.rcParams.update({
        "font.family": "DejaVu Sans", "axes.labelcolor": INK,
        "axes.edgecolor": "#999", "axes.titlecolor": INK,
        "savefig.facecolor": "white", "figure.facecolor": "white",
    })

    by_signal = defaultdict(list)
    for s in data["samples"]:
        by_signal[s["signal"]].append(s["value"])
    n_sig = len(by_signal)
    n_plot = len(plottable_signals(by_signal))
    # Pre-count pages for accurate "page X of Y".
    stats_p = max(1, -(-n_sig // 24))
    ts_p = max(1, -(-n_plot // 5))
    hist_p = max(1, -(-n_plot // 6))
    total = 1 + 1 + stats_p + ts_p + hist_p + 1

    with PdfPages(pdf_path) as pdf:
        p = 1
        cover_page(pdf, data, json_path.with_suffix(".sqlite").name, p, total); p += 1
        dtc_page(pdf, data, p, total); p += 1
        p += stats_pages(pdf, data, p, total)
        p += timeseries_pages(pdf, data, p, total)
        p += histogram_pages(pdf, data, p, total)
        sample_log_page(pdf, data, p, total)

        d = pdf.infodict()
        d["Title"] = "KnightRider Field-Capture Report"
        d["Author"] = "Tarimica Chiwara"
        d["Subject"] = "Decoded OBD-II + CAN telemetry capture"
        d["Keywords"] = "knight-rider, OBD-II, CAN-bus, DTC, telemetry"

    print(f"wrote {pdf_path}  ({pdf_path.stat().st_size/1024:.1f} KB, {total} pages)")


if __name__ == "__main__":
    main()

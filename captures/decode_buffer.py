"""Decode a knight-rider buffer SQLite into docs-friendly formats.

Usage:
    python decode_buffer.py <buffer.sqlite>

Writes alongside the input file:
    <stem>.csv         every sample, one row each, sorted by timestamp
    <stem>.json        same data structured, plus session metadata
    <stem>.summary.md  per-signal statistics + plain-language commentary
    <stem>.wide.csv    one row per OBD poll cycle (timestamps + each signal as a column)
"""
import csv
import json
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import cbor2


def _parse_ts(s: str) -> datetime:
    """Parse an ISO timestamp with nanosecond precision (chrono emits 9 decimals
    of seconds, Python stdlib only handles 6). Truncate excess decimals."""
    s = s.replace("Z", "+00:00")
    s = re.sub(r"\.(\d{6})\d+", r".\1", s)
    return datetime.fromisoformat(s)


def _as_bytes(field):
    """Rust ciborium encodes Vec<u8> as a CBOR array of small ints. Recover bytes."""
    return bytes(field) if isinstance(field, list) else field


def decode(db_path: Path):
    con = sqlite3.connect(db_path)
    rows = con.execute(
        "SELECT batch_id, envelope FROM batches ORDER BY batch_id"
    ).fetchall()

    if not rows:
        sys.exit("no batches in buffer")

    # session metadata from first envelope
    first_env = cbor2.loads(rows[0][1])
    import uuid as _uuid
    raw = first_env["device_id"]
    if isinstance(raw, (bytes, bytearray)) and len(raw) == 16:
        device_id = str(_uuid.UUID(bytes=bytes(raw)))
    elif isinstance(raw, _uuid.UUID):
        device_id = str(raw)
    else:
        device_id = str(raw)
    envelope_schema_v = first_env["envelope_schema_v"]
    payload_schema_v = first_env["payload_schema_v"]

    all_samples = []
    for batch_id, env_blob in rows:
        env = cbor2.loads(env_blob)
        pl = cbor2.loads(_as_bytes(env["payload"]))
        for s in pl["samples"]:
            ts = s["ts"]
            # cbor2 hands back datetime objects directly for ts tag-0 — normalize to ISO
            if isinstance(ts, datetime):
                ts_iso = ts.isoformat()
            else:
                ts_iso = str(ts)
            all_samples.append(
                {
                    "batch_id": batch_id,
                    "ts": ts_iso,
                    "source": s["source"],
                    "signal": s["signal"],
                    "value": float(s["value"]),
                    "unit": s["unit"],
                }
            )

    all_samples.sort(key=lambda r: r["ts"])
    return device_id, envelope_schema_v, payload_schema_v, all_samples


def write_long_csv(out: Path, samples):
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f, fieldnames=["batch_id", "ts", "source", "signal", "value", "unit"]
        )
        w.writeheader()
        w.writerows(samples)


def write_wide_csv(out: Path, samples):
    """One row per OBD poll cycle (~600 ms): timestamp + each signal as a column."""
    signals = sorted({s["signal"] for s in samples})
    units = {s["signal"]: s["unit"] for s in samples}

    # group samples within a 700 ms window into one row
    rows = []
    current_ts = None
    current_row = {sig: "" for sig in signals}
    window_ms = 700.0

    for s in samples:
        ts = _parse_ts(s["ts"])
        if current_ts is None or (ts - current_ts).total_seconds() * 1000 > window_ms:
            if current_ts is not None:
                rows.append({"ts": current_ts.isoformat(), **current_row})
            current_ts = ts
            current_row = {sig: "" for sig in signals}
        current_row[s["signal"]] = s["value"]
    if current_ts is not None:
        rows.append({"ts": current_ts.isoformat(), **current_row})

    headers = ["ts"] + [f"{sig} [{units[sig]}]" for sig in signals]
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(headers)
        for row in rows:
            w.writerow([row["ts"]] + [row[sig] for sig in signals])


def write_json(out: Path, device_id, envelope_v, payload_v, samples):
    obj = {
        "device_id": device_id,
        "envelope_schema_v": envelope_v,
        "payload_schema_v": payload_v,
        "sample_count": len(samples),
        "first_ts": samples[0]["ts"],
        "last_ts": samples[-1]["ts"],
        "samples": samples,
    }
    with out.open("w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)


def write_summary_md(out: Path, device_id, envelope_v, payload_v, samples):
    by_signal = defaultdict(list)
    for s in samples:
        by_signal[s["signal"]].append(s["value"])

    units = {s["signal"]: s["unit"] for s in samples}
    first_ts = samples[0]["ts"]
    last_ts = samples[-1]["ts"]
    dt_first = _parse_ts(first_ts)
    dt_last = _parse_ts(last_ts)
    dur_s = (dt_last - dt_first).total_seconds()

    lines = []
    lines.append("# Knight Rider capture — decoded summary")
    lines.append("")
    lines.append(f"- **Device ID:** `{device_id}`")
    lines.append(f"- **Envelope schema:** v{envelope_v}")
    lines.append(f"- **Payload schema:** v{payload_v}")
    lines.append(f"- **Total samples:** {len(samples)}")
    lines.append(f"- **First sample:** {first_ts}")
    lines.append(f"- **Last sample:** {last_ts}")
    lines.append(f"- **Duration:** {dur_s:.1f} s  ({dur_s/60:.2f} min)")
    lines.append("")
    lines.append("## Per-signal statistics")
    lines.append("")
    lines.append("| signal | unit | n | min | max | mean | std |")
    lines.append("|---|---|---:|---:|---:|---:|---:|")
    for sig in sorted(by_signal):
        vals = by_signal[sig]
        n = len(vals)
        mn = min(vals)
        mx = max(vals)
        mean = sum(vals) / n
        var = sum((v - mean) ** 2 for v in vals) / n
        std = var**0.5
        lines.append(
            f"| `{sig}` | {units[sig]} | {n} | {mn:.2f} | {mx:.2f} | {mean:.2f} | {std:.2f} |"
        )
    lines.append("")
    lines.append("## First 10 samples")
    lines.append("")
    lines.append("| ts | signal | value | unit |")
    lines.append("|---|---|---:|---|")
    for s in samples[:10]:
        lines.append(f"| {s['ts']} | `{s['signal']}` | {s['value']:.2f} | {s['unit']} |")
    lines.append("")
    lines.append("## Last 10 samples")
    lines.append("")
    lines.append("| ts | signal | value | unit |")
    lines.append("|---|---|---:|---|")
    for s in samples[-10:]:
        lines.append(f"| {s['ts']} | `{s['signal']}` | {s['value']:.2f} | {s['unit']} |")
    lines.append("")

    out.write_text("\n".join(lines), encoding="utf-8")


def main():
    db_path = Path(sys.argv[1] if len(sys.argv) > 1 else "vitz-buffer-20260528.sqlite")
    stem = db_path.with_suffix("")

    device_id, env_v, pl_v, samples = decode(db_path)
    print(f"Decoded {len(samples)} samples from {db_path.name}")

    long_csv = stem.with_name(stem.name + ".csv")
    wide_csv = stem.with_name(stem.name + ".wide.csv")
    json_out = stem.with_name(stem.name + ".json")
    md_out = stem.with_name(stem.name + ".summary.md")

    write_long_csv(long_csv, samples)
    write_wide_csv(wide_csv, samples)
    write_json(json_out, device_id, env_v, pl_v, samples)
    write_summary_md(md_out, device_id, env_v, pl_v, samples)

    print()
    print("Wrote:")
    for p in (long_csv, wide_csv, json_out, md_out):
        size_kb = p.stat().st_size / 1024
        print(f"  {p.name:45s} {size_kb:>8.1f} KB")


if __name__ == "__main__":
    main()

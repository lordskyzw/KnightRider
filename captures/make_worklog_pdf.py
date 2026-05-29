"""Render the 2026-05-29 work-summary PDF for sharing with the team.

Standalone (content inline) — run: python make_worklog_pdf.py
Output: knight-rider-worklog-2026-05-29.pdf
"""
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable,
)

OUT = "knight-rider-worklog-2026-05-29.pdf"

INK = colors.HexColor("#16181d")
MUTED = colors.HexColor("#5a5f6b")
ACCENT = colors.HexColor("#c0392b")  # KITT red
RULE = colors.HexColor("#d9dce1")
CHIPBG = colors.HexColor("#f2f3f5")

styles = getSampleStyleSheet()
H1 = ParagraphStyle("H1", parent=styles["Title"], fontName="Helvetica-Bold",
                    fontSize=20, textColor=INK, spaceAfter=2, leading=24)
SUB = ParagraphStyle("SUB", parent=styles["Normal"], fontSize=10,
                     textColor=MUTED, spaceAfter=10)
H2 = ParagraphStyle("H2", parent=styles["Heading2"], fontName="Helvetica-Bold",
                    fontSize=13, textColor=ACCENT, spaceBefore=12, spaceAfter=5)
BODY = ParagraphStyle("BODY", parent=styles["Normal"], fontSize=9.7,
                      textColor=INK, leading=14, spaceAfter=4, alignment=TA_LEFT)
BULLET = ParagraphStyle("BULLET", parent=BODY, leftIndent=12, bulletIndent=2,
                        spaceAfter=2)
SMALL = ParagraphStyle("SMALL", parent=BODY, fontSize=8.5, textColor=MUTED)


def b(text):
    return Paragraph("• " + text, BULLET)


def rule():
    return HRFlowable(width="100%", thickness=0.6, color=RULE,
                      spaceBefore=4, spaceAfter=8)


story = []
story.append(Paragraph("Knight Rider — Work Summary", H1))
story.append(Paragraph("2026-05-29 &nbsp;·&nbsp; Toyota Corolla Axio field session, "
                       "firmware + mobile app, end-to-end validation", SUB))
story.append(rule())

# ── Headline ────────────────────────────────────────────────────────────────
story.append(Paragraph("Headline", H2))
story.append(Paragraph(
    "Characterised a new vehicle (Toyota Corolla Axio E160) end-to-end, validated "
    "the full product chain on it (Pi → phone → cloud), expanded the Pi firmware, "
    "and shipped two mobile improvements. New binary deployed and running on the Pi; "
    "new app build (v0.9.0+12) shared with the team.", BODY))

# ── Field session ─────────────────────────────────────────────────────────────
story.append(Paragraph("1 · Toyota Axio field session", H2))
story.append(b("<b>Standard OBD-II works.</b> All 20 polled PIDs decode "
               "(RPM, coolant, load, MAF, fuel trims, timing, O2, etc.)."))
story.append(b("<b>Battery / alternator confirmed healthy</b> via PID 0x42: "
               "11.89 V (ignition on, engine off) → 14.10 V (running) — a clean "
               "alternator/charging test."))
story.append(b("<b>Stored fault code P0420</b> (Catalyst Efficiency Below "
               "Threshold, Bank 1) read from the car — a real, detectable fault."))
story.append(b("<b>CAN body signals reverse-engineered</b> (baseline-vs-active "
               "candump diff): brake switch (ID 0x224 bit5), stop-lamp echo "
               "(0x3B4 bit0), driver door (0x620 bit5)."))
story.append(b("<b>Exterior lighting is gatewayed off</b> the OBD-II bus on the "
               "Axio (turn signals, hazards, headlights not visible) — so live "
               "brake + door are recoverable; lights stay on a manual override."))
story.append(b("<b>VIN is NOT OBD-readable</b> on the Axio (Mode 09 PID 02 "
               "unsupported; UDS 22 F190 service-not-supported). Only the "
               "calibration ID (31254100) is readable. Same blocker seen on Honda. "
               "<b>Design impact: the app identifies the car via a picker, not VIN.</b>"))

# ── Product validation ────────────────────────────────────────────────────────
story.append(Paragraph("2 · Product validated end-to-end", H2))
story.append(b("<b>Live LAN dashboard:</b> phone showed live RPM + the P0420 code "
               "off the Pi over WebSocket — the 20-PID envelopes parse correctly."))
story.append(b("<b>Courier → cloud:</b> 453 batches carried Pi → phone → Railway "
               "Postgres, idempotent (verified directly in the database)."))

# ── Firmware ──────────────────────────────────────────────────────────────────
story.append(Paragraph("3 · Pi firmware (Rust) — deployed", H2))
story.append(b("OBD poller expanded 14 → 20 PIDs: added battery/charging voltage, "
               "catalyst temps (B1S1/B1S2, for P0420), barometric pressure, "
               "commanded lambda, absolute load."))
story.append(b("Passive CAN sniffer gained bit-level signal decoding (was "
               "byte-aligned only) + a field-verified Toyota Axio profile "
               "(brake / stop-lamp / driver-door)."))
story.append(b("45 unit tests pass (was 41). Built and deployed to the Pi; "
               "journal confirms “20 PIDs” + “Prius PT + Axio body · 6 messages”."))

# ── Mobile ────────────────────────────────────────────────────────────────────
story.append(Paragraph("4 · Mobile app — build v0.9.0+12", H2))
story.append(b("<b>Honest connection status:</b> the ambiguous “LIVE” is now "
               "three states — OFFLINE (can’t reach Pi) / LINKED (reached Pi, no "
               "fresh data) / LIVE (telemetry flowing within 3 s). LIVE now "
               "reliably means real data is arriving."))
story.append(b("<b>Per-car 3D model + Settings car picker:</b> choose the vehicle "
               "(Vitz renders its 3D model; Axio listed, 3D model pending an "
               "asset). Chosen manually because VIN can’t auto-detect it."))
story.append(b("Cloud API unchanged. APK shared: knight-rider-v0.9.0+12.apk "
               "(sideload; allow “unknown sources”)."))

# ── Open items ────────────────────────────────────────────────────────────────
story.append(Paragraph("Open items / next", H2))
story.append(b("Live-verify the brake-bit on a car (press brake → "
               "dbc.toyota.BRAKE.pressed = 1) — needs car access."))
story.append(b("Source a CC-BY licensed Toyota Axio/Corolla 3D model (GLB) → "
               "drop-in to the car picker."))
story.append(b("Optionally wire the dashboard 3D brake light to the live CAN "
               "signal."))

story.append(Spacer(1, 8))
story.append(rule())
story.append(Paragraph(
    "Detailed field notes: captures/axio-re-20260529/FINDINGS.md &nbsp;·&nbsp; "
    "decoded telemetry: captures/axio-only-20260529.* &nbsp;·&nbsp; "
    "code on GitHub: lordskyzw/KnightRider (main).", SMALL))

SimpleDocTemplate(
    OUT, pagesize=A4,
    leftMargin=20 * mm, rightMargin=20 * mm,
    topMargin=18 * mm, bottomMargin=16 * mm,
    title="Knight Rider — Work Summary 2026-05-29",
    author="Knight Rider team",
).build(story)
print("wrote", OUT)

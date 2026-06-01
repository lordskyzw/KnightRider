import 'dart:async';
import 'package:flutter/material.dart';

import 'app_theme.dart';
import 'drives_db.dart';

/// Drive replay: scrub or play back a recorded drive. The chart shows a few
/// signals over the whole drive with a playhead; the grid shows every signal's
/// value at the scrubbed moment. Data is the per-drive [DriveSeries] (1 Hz,
/// forward-filled).
class ReplayScreen extends StatefulWidget {
  final int driveId;
  final String title;
  const ReplayScreen({super.key, required this.driveId, required this.title});

  @override
  State<ReplayScreen> createState() => _ReplayScreenState();
}

class _ReplayScreenState extends State<ReplayScreen> {
  final _db = DrivesDb();
  DriveSeries? _series;
  int _t = 0; // current second
  Timer? _player;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _player?.cancel();
    super.dispose();
  }

  Future<void> _load() async {
    final s = await _db.getSeries(widget.driveId);
    if (!mounted) return;
    setState(() => _series = s);
  }

  void _togglePlay() {
    final s = _series;
    if (s == null) return;
    if (_player != null) {
      _player!.cancel();
      setState(() => _player = null);
      return;
    }
    if (_t >= s.durationS) _t = 0; // restart if at the end
    setState(() {
      _player = Timer.periodic(const Duration(milliseconds: 100), (_) {
        if (_t >= s.durationS) {
          _player?.cancel();
          setState(() => _player = null);
        } else {
          setState(() => _t = (_t + 10).clamp(0, s.durationS)); // ~10x playback
        }
      });
    });
  }

  String _clock(int sec) {
    final m = sec ~/ 60, s = sec % 60;
    return '${m.toString().padLeft(2, '0')}:${s.toString().padLeft(2, '0')}';
  }

  @override
  Widget build(BuildContext context) {
    final s = _series;
    return Scaffold(
      backgroundColor: AppPalette.bg,
      appBar: AppBar(
        backgroundColor: AppPalette.bg,
        foregroundColor: AppPalette.textHi,
        elevation: 0,
        title: Text('Replay · ${widget.title}'),
      ),
      body: s == null
          ? const Center(child: CircularProgressIndicator())
          : s.frames.isEmpty
              ? const Center(
                  child: Padding(
                    padding: EdgeInsets.all(40),
                    child: Text('No replay data for this drive.',
                        style: TextStyle(color: AppPalette.textMid)),
                  ),
                )
              : _buildReplay(s),
    );
  }

  Widget _buildReplay(DriveSeries s) {
    final i = s.indexAt(_t);
    return Column(
      children: [
        // chart
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
          child: SizedBox(
            height: 180,
            child: CustomPaint(
              painter: _ReplayChart(series: s, playheadFrac: s.durationS == 0 ? 0 : _t / s.durationS),
              child: const SizedBox.expand(),
            ),
          ),
        ),
        const _Legend(),
        // transport
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 12),
          child: Row(
            children: [
              IconButton(
                onPressed: _togglePlay,
                iconSize: 34,
                color: AppPalette.accent,
                icon: Icon(_player == null ? Icons.play_circle_fill : Icons.pause_circle_filled),
              ),
              Expanded(
                child: Slider(
                  value: _t.toDouble().clamp(0, s.durationS.toDouble()),
                  max: s.durationS.toDouble(),
                  activeColor: AppPalette.accent,
                  onChanged: (v) {
                    _player?.cancel();
                    setState(() { _player = null; _t = v.round(); });
                  },
                ),
              ),
              SizedBox(
                width: 84,
                child: Text('${_clock(_t)} / ${_clock(s.durationS)}',
                    textAlign: TextAlign.right,
                    style: const TextStyle(color: AppPalette.textMid, fontSize: 13,
                        fontFeatures: [FontFeature.tabularFigures()])),
              ),
              const SizedBox(width: 8),
            ],
          ),
        ),
        const Divider(height: 1, color: AppPalette.divider),
        // readout grid at current moment
        Expanded(
          child: GridView.count(
            crossAxisCount: 2,
            childAspectRatio: 3.0,
            padding: const EdgeInsets.all(12),
            children: [
              for (final spec in _readouts)
                _Readout(label: spec.$1, unit: spec.$3, value: s.valueAt(i, spec.$2)),
            ],
          ),
        ),
      ],
    );
  }
}

// (label, signal-key, unit)
const List<(String, String, String)> _readouts = [
  ('Speed', 'speed', 'km/h'),
  ('RPM', 'rpm', ''),
  ('O₂ ↑', 'o2up', 'λ'),
  ('O₂ ↓', 'o2down', 'V'),
  ('STFT', 'stft', '%'),
  ('LTFT', 'ltft', '%'),
  ('MAF', 'maf', 'g/s'),
  ('Throttle', 'throttle', '%'),
  ('Coolant', 'coolant', '°C'),
  ('Catalyst', 'cat', '°C'),
];

// signals drawn in the chart, with colours
const List<(String, Color)> _chartLines = [
  ('speed', AppPalette.cool),
  ('rpm', AppPalette.warning),
  ('o2up', AppPalette.live),
];

class _Readout extends StatelessWidget {
  final String label;
  final String unit;
  final double? value;
  const _Readout({required this.label, required this.unit, required this.value});

  @override
  Widget build(BuildContext context) {
    final v = value;
    final txt = v == null
        ? '—'
        : (v.abs() >= 100 ? v.round().toString() : v.toStringAsFixed(2));
    return Row(
      mainAxisAlignment: MainAxisAlignment.spaceBetween,
      children: [
        Text(label, style: const TextStyle(color: AppPalette.textMid, fontSize: 13)),
        Text('$txt${unit.isNotEmpty ? ' $unit' : ''}',
            style: const TextStyle(color: AppPalette.textHi, fontSize: 15,
                fontWeight: FontWeight.w600,
                fontFeatures: [FontFeature.tabularFigures()])),
      ],
    );
  }
}

class _Legend extends StatelessWidget {
  const _Legend();
  @override
  Widget build(BuildContext context) {
    const labels = {'speed': 'Speed', 'rpm': 'RPM', 'o2up': 'O₂ ↑'};
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 16),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          for (final (key, color) in _chartLines) ...[
            Container(width: 14, height: 3, color: color),
            const SizedBox(width: 5),
            Text(labels[key] ?? key,
                style: const TextStyle(color: AppPalette.textMid, fontSize: 11)),
            const SizedBox(width: 16),
          ],
        ],
      ),
    );
  }
}

class _ReplayChart extends CustomPainter {
  final DriveSeries series;
  final double playheadFrac;
  _ReplayChart({required this.series, required this.playheadFrac});

  @override
  void paint(Canvas canvas, Size size) {
    final n = series.frames.length;
    if (n < 2) return;
    // each line normalised to its own observed min/max
    for (final (key, color) in _chartLines) {
      final si = series.signals.indexOf(key);
      if (si < 0) continue;
      double lo = double.infinity, hi = -double.infinity;
      for (final f in series.frames) {
        final v = f[si + 1];
        if (v != null) { lo = v < lo ? v.toDouble() : lo; hi = v > hi ? v.toDouble() : hi; }
      }
      if (lo == double.infinity || hi <= lo) continue;
      final path = Path();
      bool started = false;
      for (int x = 0; x < n; x++) {
        final v = series.frames[x][si + 1];
        if (v == null) continue;
        final px = size.width * x / (n - 1);
        final py = size.height * (1 - (v.toDouble() - lo) / (hi - lo));
        if (!started) { path.moveTo(px, py); started = true; } else { path.lineTo(px, py); }
      }
      canvas.drawPath(path, Paint()
        ..color = color..style = PaintingStyle.stroke..strokeWidth = 1.6
        ..strokeJoin = StrokeJoin.round);
    }
    // playhead
    final hx = size.width * playheadFrac.clamp(0.0, 1.0);
    canvas.drawLine(Offset(hx, 0), Offset(hx, size.height),
        Paint()..color = AppPalette.textHi.withValues(alpha: 0.6)..strokeWidth = 1.2);
  }

  @override
  bool shouldRepaint(_ReplayChart old) =>
      old.playheadFrac != playheadFrac || old.series != series;
}

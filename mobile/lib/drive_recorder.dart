import 'dart:convert';

import 'drives_db.dart';
import 'sample.dart';

/// Turns the live sample stream into recorded [DriveRecord]s plus a downsampled
/// (~1 Hz) replay series.
///
/// A drive opens on the first telemetry sample and closes after [gap] with no
/// telemetry (disconnect, or parked with ignition off). Drives shorter than
/// [minDuration] are discarded as noise. The recorder is fed from the
/// dashboard's sample handler and polled once per UI tick to detect the closing
/// gap even when no further samples arrive.
class DriveRecorder {
  final DrivesDb db;
  final Duration gap;
  final Duration minDuration;

  DriveRecorder({
    required this.db,
    this.gap = const Duration(minutes: 2),
    this.minDuration = const Duration(seconds: 60),
  });

  /// Replay signals, in frame-column order (must match the bundled asset).
  static const Map<String, String> replaySignals = {
    'obd.speed': 'speed',
    'obd.rpm': 'rpm',
    'obd.coolant_temp': 'coolant',
    'obd.o2s1_eq_ratio': 'o2up',
    'obd.o2_b1s2_v': 'o2down',
    'obd.stft_b1': 'stft',
    'obd.ltft_b1': 'ltft',
    'obd.maf': 'maf',
    'obd.throttle': 'throttle',
    'obd.cat_temp_b1s1': 'cat',
  };
  static final List<String> _shortKeys = replaySignals.values.toList();

  DateTime? _startedAt;
  DateTime? _lastSampleAt;
  String _vehicle = 'Vehicle';
  int _count = 0;
  double? _maxSpeed, _maxRpm, _maxCoolant, _o2upMin, _o2upMax;
  int _dtc = 0;

  // Replay series accumulators.
  final Map<String, double> _last = {};
  final List<List<num?>> _frames = [];
  int _lastFrameSec = -1;

  bool get isRecording => _startedAt != null;

  void onSample(Sample s, {required String vehicle}) {
    final now = DateTime.now();
    if (_startedAt != null && _lastSampleAt != null &&
        now.difference(_lastSampleAt!) > gap) {
      _finalize();
    }
    _startedAt ??= now;
    _vehicle = vehicle;
    _lastSampleAt = now;
    _count++;

    switch (s.signal) {
      case 'obd.speed':
        _maxSpeed = _max(_maxSpeed, s.value);
      case 'obd.rpm':
        _maxRpm = _max(_maxRpm, s.value);
      case 'obd.coolant_temp':
        _maxCoolant = _max(_maxCoolant, s.value);
      case 'obd.o2s1_eq_ratio':
        _o2upMin = _o2upMin == null ? s.value : (s.value < _o2upMin! ? s.value : _o2upMin!);
        _o2upMax = _max(_o2upMax, s.value);
      case 'obd.dtc_count':
        _dtc = s.value.round();
    }

    // Replay frame buffering: keep last-known values, emit one frame per second.
    final short = replaySignals[s.signal];
    if (short != null) _last[short] = s.value;
    final sec = now.difference(_startedAt!).inSeconds;
    if (sec > _lastFrameSec) {
      _frames.add([sec, ..._shortKeys.map((k) => _last[k])]);
      _lastFrameSec = sec;
    }
  }

  /// Polled from the UI tick. Closes the drive once the telemetry gap elapses.
  void tick(DateTime now) {
    if (_startedAt != null && _lastSampleAt != null &&
        now.difference(_lastSampleAt!) > gap) {
      _finalize();
    }
  }

  /// Close any in-progress drive immediately (e.g. on dispose).
  void flush() => _finalize();

  void _finalize() {
    final start = _startedAt, last = _lastSampleAt;
    final rec = (start == null || last == null)
        ? null
        : DriveRecord(
            vehicle: _vehicle,
            startedAt: start,
            endedAt: last,
            sampleCount: _count,
            maxSpeed: _maxSpeed,
            maxRpm: _maxRpm,
            maxCoolant: _maxCoolant,
            o2upMin: _o2upMin,
            o2upMax: _o2upMax,
            dtcCount: _dtc,
          );
    final frames = List<List<num?>>.from(_frames);
    final durationS = _lastFrameSec;
    _reset();
    if (rec == null || rec.duration < minDuration) return;
    _persist(rec, frames, durationS);
  }

  Future<void> _persist(DriveRecord rec, List<List<num?>> frames, int durationS) async {
    final id = await db.insertDrive(rec);
    if (frames.isNotEmpty) {
      final json = jsonEncode({
        'vehicle': rec.vehicle,
        'duration_s': durationS,
        'signals': _shortKeys,
        'frames': frames,
      });
      await db.putSeries(id, json);
    }
  }

  void _reset() {
    _startedAt = null;
    _lastSampleAt = null;
    _count = 0;
    _maxSpeed = _maxRpm = _maxCoolant = _o2upMin = _o2upMax = null;
    _dtc = 0;
    _last.clear();
    _frames.clear();
    _lastFrameSec = -1;
  }

  static double _max(double? a, double b) => (a == null || b > a) ? b : a;
}

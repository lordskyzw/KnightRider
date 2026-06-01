import 'drives_db.dart';
import 'sample.dart';

/// Turns the live sample stream into recorded [DriveRecord]s.
///
/// A drive opens on the first telemetry sample and closes after [gap] with no
/// telemetry (disconnect, or parked with ignition off). Drives shorter than
/// [minDuration] are discarded as noise (e.g. a brief reconnect). The recorder
/// is fed from the dashboard's existing sample handler and polled once per UI
/// tick to detect the closing gap even when no further samples arrive.
class DriveRecorder {
  final DrivesDb db;
  final Duration gap;
  final Duration minDuration;

  DriveRecorder({
    required this.db,
    this.gap = const Duration(minutes: 2),
    this.minDuration = const Duration(seconds: 60),
  });

  // Open-drive accumulators (null when no drive is in progress).
  DateTime? _startedAt;
  DateTime? _lastSampleAt;
  String _vehicle = 'Vehicle';
  int _count = 0;
  double? _maxSpeed, _maxRpm, _maxCoolant, _o2upMin, _o2upMax;
  int _dtc = 0;

  bool get isRecording => _startedAt != null;

  void onSample(Sample s, {required String vehicle}) {
    final now = DateTime.now();
    // A telemetry gap closes the previous drive before this one starts.
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
    // Capture everything into locals, then reset, then persist — so the
    // (un-awaited) async insert can't race the cleared accumulators.
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
    _reset();
    if (rec == null) return;
    if (rec.duration < minDuration) return; // too short, discard
    db.insertDrive(rec);
  }

  void _reset() {
    _startedAt = null;
    _lastSampleAt = null;
    _count = 0;
    _maxSpeed = _maxRpm = _maxCoolant = _o2upMin = _o2upMax = null;
    _dtc = 0;
  }

  static double _max(double? a, double b) => (a == null || b > a) ? b : a;
}

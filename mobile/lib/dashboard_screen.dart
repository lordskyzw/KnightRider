import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_svg/flutter_svg.dart';

import 'app_theme.dart';
import 'backlog_db.dart';
import 'batches_screen.dart';
import 'build_info.dart';
import 'car_model.dart';
import 'config.dart';
import 'dtc_screen.dart';
import 'pi_client.dart';
import 'sample.dart';
import 'settings_screen.dart';
import 'uploader.dart';
import 'vehicle_catalog.dart';
import 'ws_client.dart';

// Tesla-inspired palette now lives in app_theme.dart (shared app-wide). `_T`
// stays as a short alias so the dashboard's many references are untouched;
// `_T.accent` is the user-customisable colour.
typedef _T = AppPalette;

/// Honest, data-driven connection status. Splits the single ambiguous "LIVE"
/// into three: you can tell "can't reach the Pi" from "reached it but no
/// telemetry yet (ignition off)" from "actually receiving fresh data".
enum LinkStatus { offline, linked, live }

/// A sample must arrive within this window for the link to count as LIVE;
/// otherwise the channel is open but quiet → LINKED.
const Duration _kLiveWindow = Duration(seconds: 3);

class DashboardScreen extends StatefulWidget {
  const DashboardScreen({super.key});

  @override
  State<DashboardScreen> createState() => _DashboardScreenState();
}

class _DashboardScreenState extends State<DashboardScreen> {
  WsClient? _ws;
  String _host = PiConfig.defaultHost;
  String _cloudUrl = PiConfig.defaultCloudUrl;
  WsState _state = WsState.disconnected;
  String? _wsError;
  DateTime? _lastSampleAt; // when the most recent sample arrived (data freshness)
  LinkStatus _shownStatus = LinkStatus.offline; // last status painted (for repaint trigger)
  final Map<String, Sample> _latest = {};

  String? _vin;        // captured from session.vin sample
  String? _ecuName;    // captured from session.ecu_name
  String _buildLabel = '';
  bool _car3d = false; // feature flag: 3D model vs SVG silhouette
  VehicleModel _vehicle = vehicleById(null); // which car's model to render
  Color? _carColor;    // null = factory paint
  Color _wheelColor = const Color(0xFF0A0A0A); // default black
  bool _lightsOn = false;

  // Backlog + uploader state
  final _db = BacklogDb();
  int _backlogTotal = 0;
  int _backlogPending = 0;
  int _backlogUploaded = 0;
  bool _pulling = false;
  Timer? _pullTimer;

  Uploader? _uploader;
  UploadTick? _lastTick;
  StreamSubscription<UploadTick>? _tickSub;

  StreamSubscription<Sample>? _sampleSub;
  StreamSubscription<WsState>? _stateSub;

  // Samples can arrive at 40+ Hz. Rather than setState per sample (which
  // rebuilds the whole dashboard, incl. the 3D WebView's parent, and causes
  // jank), we coalesce into the latest map and repaint at a steady ~12 Hz.
  Timer? _uiTimer;
  bool _dirty = false;

  @override
  void initState() {
    super.initState();
    _boot();
  }

  Future<void> _boot() async {
    _host = await PiConfig.host();
    _cloudUrl = await PiConfig.cloudUrl();
    _buildLabel = await BuildInfo.displayVersion();
    _car3d = await PiConfig.car3dEnabled();
    _vehicle = vehicleById(await PiConfig.vehicleId());
    final carColorArgb = await PiConfig.carColor();
    _carColor = carColorArgb == null ? null : Color(carColorArgb);
    _wheelColor = Color(await PiConfig.wheelColor());
    _lightsOn = await PiConfig.lightsOn();
    _attachWs();

    _uploader = Uploader(db: _db, cloudUrlProvider: () => _cloudUrl);
    _tickSub = _uploader!.ticks.listen((t) {
      if (!mounted) return;
      setState(() => _lastTick = t);
      _refreshBacklogCounts();
    });
    _uploader!.start();

    await _refreshBacklogCounts();
    _pullTimer = Timer.periodic(const Duration(seconds: 30), (_) => _kickBacklogPull());

    // Coalesced UI repaint: flush at ~12 Hz when data changed, OR when the
    // link status would change (e.g. LIVE→LINKED once telemetry goes quiet for
    // longer than _kLiveWindow — that lapse produces no new sample, so without
    // this the pill would stay green on stale data).
    _uiTimer = Timer.periodic(const Duration(milliseconds: 80), (_) {
      if (!mounted) return;
      final statusChanged = _linkStatus != _shownStatus;
      if (_dirty || statusChanged) {
        _dirty = false;
        _shownStatus = _linkStatus;
        setState(() {});
      }
    });
  }

  void _attachWs() {
    _ws?.dispose();
    _sampleSub?.cancel();
    _stateSub?.cancel();
    _ws = WsClient(hostProvider: () => _host);
    _sampleSub = _ws!.stream.listen(_onSample);
    _stateSub = _ws!.stateStream.listen((s) {
      if (!mounted) return;
      setState(() {
        _state = s;
        _wsError = (s == WsState.connected) ? null : _ws?.lastError;
      });
      if (s == WsState.connected) _kickBacklogPull();
    });
    _ws!.start();
  }

  /// Channel state + data freshness collapsed into one honest status.
  LinkStatus get _linkStatus {
    if (_state != WsState.connected) return LinkStatus.offline;
    final last = _lastSampleAt;
    if (last != null && DateTime.now().difference(last) < _kLiveWindow) {
      return LinkStatus.live;
    }
    return LinkStatus.linked; // socket open to the Pi, but no recent telemetry
  }

  void _onSample(Sample s) {
    // No setState here — just stash the latest value and mark dirty. The
    // _uiTimer flushes to a single setState at ~12 Hz (see _boot).
    _lastSampleAt = DateTime.now();
    _latest[s.signal] = s;
    // Session metadata samples carry the string payload in `unit`.
    if (s.signal == 'session.vin') _vin = s.unit;
    if (s.signal == 'session.ecu_name') _ecuName = s.unit;
    _dirty = true;
  }

  Future<void> _refreshBacklogCounts() async {
    final total = await _db.totalCount();
    final pending = await _db.pendingCount();
    final uploaded = await _db.uploadedCount();
    if (!mounted) return;
    setState(() {
      _backlogTotal = total;
      _backlogPending = pending;
      _backlogUploaded = uploaded;
    });
  }

  Future<void> _kickBacklogPull() async {
    if (_pulling) return;
    _pulling = true;
    try {
      final since = await _db.highestBatchId();
      final client = PiClient(_host);
      try {
        final rows = await client.backlog(since: since, limit: 500);
        for (final r in rows) {
          await _db.upsert(r);
        }
        if (rows.isNotEmpty) {
          await PiConfig.setBacklogCursor(rows.last.batchId);
        }
      } finally {
        client.close();
      }
    } catch (_) {
      // retry next tick
    } finally {
      _pulling = false;
      await _refreshBacklogCounts();
    }
  }

  Future<void> _openSettings() async {
    final updated = await Navigator.of(context).push<bool>(
      MaterialPageRoute(builder: (_) => const SettingsScreen()),
    );
    if (updated == true) {
      _host = await PiConfig.host();
      _cloudUrl = await PiConfig.cloudUrl();
      final car3d = await PiConfig.car3dEnabled();
      final vehicle = vehicleById(await PiConfig.vehicleId());
      final carColorArgb = await PiConfig.carColor();
      final wheelArgb = await PiConfig.wheelColor();
      final lightsOn = await PiConfig.lightsOn();
      if (mounted) {
        setState(() {
          _car3d = car3d;
          _vehicle = vehicle;
          _carColor = carColorArgb == null ? null : Color(carColorArgb);
          _wheelColor = Color(wheelArgb);
          _lightsOn = lightsOn;
        });
      }
      _attachWs();
    }
  }

  void _openMore() {
    showModalBottomSheet<void>(
      context: context,
      backgroundColor: Colors.transparent,
      isScrollControlled: true,
      builder: (_) => _MoreSheet(latest: _latest),
    );
  }

  void _openBatches(BatchFilter filter) {
    Navigator.of(context).push(MaterialPageRoute(
      builder: (_) => BatchesScreen(filter: filter),
    ));
  }

  void _openDtc() {
    Navigator.of(context).push(MaterialPageRoute(
      builder: (_) => DtcScreen(latest: Map.of(_latest)),
    ));
  }

  @override
  void dispose() {
    _sampleSub?.cancel();
    _stateSub?.cancel();
    _tickSub?.cancel();
    _pullTimer?.cancel();
    _uiTimer?.cancel();
    _ws?.dispose();
    _uploader?.dispose();
    super.dispose();
  }

  // ─── Build ──────────────────────────────────────────────────────────────

  double? _v(String key) => _latest[key]?.value;

  /// True if any of [keys] is present in the live feed with a non-zero value.
  bool _sig(List<String> keys) {
    for (final k in keys) {
      final s = _latest[k];
      if (s != null && s.value != 0) return true;
    }
    return false;
  }

  /// Lamp state for the 3D car: the manual "Lights" toggle turns the running
  /// lights on; live DBC body signals (when the Pi emits them) drive the rest.
  /// Signal-key names here are the convention the sniffer should emit — they're
  /// harmless until those samples start arriving.
  LampState _lampState() => LampState(
        head: _lightsOn ||
            _sig(const [
              'dbc.toyota.LIGHT_STALK.low_beam',
              'dbc.toyota.LIGHT_STALK.high_beam',
              'dbc.toyota.LIGHTS.headlights',
            ]),
        brake: _sig(const [
          'dbc.toyota.BRAKE_MODULE.brake_pressed',
          'obd.brake',
        ]),
        left: _sig(const [
          'dbc.toyota.TURN_SIGNALS.left',
          'dbc.toyota.BLINKERS.left',
        ]),
        right: _sig(const [
          'dbc.toyota.TURN_SIGNALS.right',
          'dbc.toyota.BLINKERS.right',
        ]),
        reverse: _sig(const ['dbc.toyota.GEAR_PACKET.reverse']),
      );

  @override
  Widget build(BuildContext context) {
    final rpm = _v('obd.rpm') ?? 0;
    final speed = _v('obd.speed') ?? 0;
    final coolant = _v('obd.coolant_temp');
    final intake = _v('obd.intake_air_temp');
    final battery = _v('obd.battery_v');
    final fuel = _v('obd.fuel_level');
    final throttle = _v('obd.throttle') ?? 0;
    final maf = _v('obd.maf');
    final map = _v('obd.map');
    final dtcCount = _v('obd.dtc_count')?.toInt() ?? 0;

    final dbcRpm = _v('dbc.toyota.POWERTRAIN.engine_rpm');
    final liveRpm = (dbcRpm != null && dbcRpm > 0) ? dbcRpm : rpm;

    return Scaffold(
      backgroundColor: _T.bg,
      body: SafeArea(
        child: Column(
          children: [
            _TopStatusBar(
              status: _linkStatus,
              host: _host,
              vin: _vin,
              buildLabel: _buildLabel,
              onSettings: _openSettings,
            ),
            // Only nag about reaching the Pi when we genuinely can't (OFFLINE).
            // LINKED (open but quiet) is normal — the amber pill says it all.
            if (_linkStatus == LinkStatus.offline && _wsError != null)
              _ErrorBanner(detail: _wsError!),
            const SizedBox(height: 6),
            Expanded(
              child: Padding(
                padding: const EdgeInsets.fromLTRB(16, 8, 16, 8),
                child: Column(
                  children: [
                    _HeroRpm(value: liveRpm, isLive: dbcRpm != null && dbcRpm > 0),
                    const SizedBox(height: 12),
                    Expanded(
                      child: _CarVisualizer(
                        coolantC: coolant,
                        intakeC: intake,
                        batteryV: battery,
                        fuelPct: fuel,
                        rpmForPulse: liveRpm,
                        use3d: _car3d,
                        vehicle: _vehicle,
                        carColor: _carColor,
                        wheelColor: _wheelColor,
                        lamps: _lampState(),
                      ),
                    ),
                    const SizedBox(height: 10),
                    _MetricBar(
                      label: 'SPEED',
                      value: speed,
                      unit: 'km/h',
                      max: 200,
                    ),
                    const SizedBox(height: 8),
                    _MetricBar(
                      label: 'THROTTLE',
                      value: throttle,
                      unit: '%',
                      max: 100,
                    ),
                    if (maf != null) ...[
                      const SizedBox(height: 8),
                      _MetricBar(label: 'MAF', value: maf, unit: 'g/s', max: 60),
                    ],
                    if (map != null) ...[
                      const SizedBox(height: 8),
                      _MetricBar(label: 'MAP', value: map, unit: 'kPa', max: 110),
                    ],
                  ],
                ),
              ),
            ),
            _BottomStrip(
              backlogTotal: _backlogTotal,
              backlogPending: _backlogPending,
              backlogUploaded: _backlogUploaded,
              lastTick: _lastTick,
              dtcCount: dtcCount,
              ecuName: _ecuName,
              onStored: () => _openBatches(BatchFilter.all),
              onSynced: () => _openBatches(BatchFilter.uploaded),
              onWait: () => _openBatches(BatchFilter.pending),
              onDtc: _openDtc,
              onMore: _openMore,
            ),
          ],
        ),
      ),
    );
  }
}

// ─── Top status bar ─────────────────────────────────────────────────────────
class _TopStatusBar extends StatelessWidget {
  final LinkStatus status;
  final String host;
  final String? vin;
  final String buildLabel;
  final VoidCallback onSettings;
  const _TopStatusBar({
    required this.status,
    required this.host,
    required this.vin,
    required this.buildLabel,
    required this.onSettings,
  });

  @override
  Widget build(BuildContext context) {
    // Three honest states: OFFLINE (can't reach Pi), LINKED (reached the Pi but
    // no fresh telemetry — e.g. ignition off), LIVE (data flowing now). Only
    // LIVE pulses green, so "LIVE" reliably means you're seeing real RPMs.
    final (label, color) = switch (status) {
      LinkStatus.live => ('LIVE', _T.live),
      LinkStatus.linked => ('LINKED', _T.warning),
      LinkStatus.offline => ('OFFLINE', _T.accent),
    };
    return Container(
      padding: const EdgeInsets.fromLTRB(16, 12, 8, 10),
      child: Row(
        children: [
          _PulseDot(color: color, pulsing: status == LinkStatus.live),
          const SizedBox(width: 8),
          Text(label,
              style: TextStyle(
                fontSize: 11, color: color,
                fontWeight: FontWeight.w700, letterSpacing: 2.0,
              )),
          const SizedBox(width: 14),
          const Icon(Icons.router_outlined, size: 13, color: _T.textMid),
          const SizedBox(width: 5),
          Text(host,
              style: const TextStyle(
                fontSize: 11, color: _T.textMid,
                fontFamily: 'monospace',
              )),
          const Spacer(),
          if (vin != null) ...[
            const Icon(Icons.fingerprint, size: 13, color: _T.textMid),
            const SizedBox(width: 4),
            Text(_shortVin(vin!),
                style: const TextStyle(
                  fontSize: 10, color: _T.textMid, fontFamily: 'monospace',
                )),
            const SizedBox(width: 10),
          ],
          Text(buildLabel,
              style: const TextStyle(
                fontSize: 10, color: _T.textLow, fontFamily: 'monospace',
              )),
          IconButton(
            iconSize: 18,
            onPressed: onSettings,
            icon: const Icon(Icons.settings, color: _T.textMid),
            tooltip: 'Settings',
          ),
        ],
      ),
    );
  }

  String _shortVin(String v) {
    if (v.length <= 8) return v;
    return '${v.substring(0, 4)}…${v.substring(v.length - 4)}';
  }
}

class _PulseDot extends StatefulWidget {
  final Color color;
  final bool pulsing;
  const _PulseDot({required this.color, required this.pulsing});
  @override
  State<_PulseDot> createState() => _PulseDotState();
}

class _PulseDotState extends State<_PulseDot> with SingleTickerProviderStateMixin {
  late final AnimationController _c = AnimationController(
    vsync: this, duration: const Duration(milliseconds: 1400),
  )..repeat(reverse: true);

  @override
  void dispose() { _c.dispose(); super.dispose(); }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: _c,
      builder: (_, _) {
        final t = widget.pulsing ? (0.5 + 0.5 * _c.value) : 1.0;
        return Container(
          width: 8, height: 8,
          decoration: BoxDecoration(
            shape: BoxShape.circle,
            color: widget.color.withValues(alpha: t),
            boxShadow: widget.pulsing
                ? [BoxShadow(color: widget.color.withValues(alpha: 0.5 * t), blurRadius: 8)]
                : null,
          ),
        );
      },
    );
  }
}

// ─── Hero RPM ───────────────────────────────────────────────────────────────
class _HeroRpm extends StatelessWidget {
  final double value;
  final bool isLive;
  const _HeroRpm({required this.value, required this.isLive});

  @override
  Widget build(BuildContext context) {
    return TweenAnimationBuilder<double>(
      tween: Tween(begin: 0, end: value),
      duration: const Duration(milliseconds: 220),
      curve: Curves.easeOutCubic,
      builder: (_, v, _) {
        return Column(
          children: [
            Text(v.round().toString(),
                style: const TextStyle(
                  fontSize: 92, height: 0.95,
                  color: _T.textHi, fontWeight: FontWeight.w200,
                  letterSpacing: -3,
                  fontFeatures: [FontFeature.tabularFigures()],
                )),
            const SizedBox(height: 2),
            Row(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                const Text('RPM',
                    style: TextStyle(
                      fontSize: 11, color: _T.textMid,
                      letterSpacing: 3, fontWeight: FontWeight.w600,
                    )),
                if (isLive) ...[
                  const SizedBox(width: 8),
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 1),
                    decoration: BoxDecoration(
                      color: _T.live.withValues(alpha: 0.12),
                      borderRadius: BorderRadius.circular(3),
                    ),
                    child: const Text('DBC 42 Hz',
                        style: TextStyle(
                          fontSize: 8, color: _T.live,
                          letterSpacing: 1.5, fontWeight: FontWeight.w700,
                        )),
                  ),
                ],
              ],
            ),
          ],
        );
      },
    );
  }
}

// ─── Car + corner pods ──────────────────────────────────────────────────────
class _CarVisualizer extends StatelessWidget {
  final double? coolantC;
  final double? intakeC;
  final double? batteryV;
  final double? fuelPct;
  final double rpmForPulse;
  final bool use3d;
  final VehicleModel vehicle;
  final Color? carColor;
  final Color wheelColor;
  final LampState lamps;
  const _CarVisualizer({
    required this.coolantC,
    required this.intakeC,
    required this.batteryV,
    required this.fuelPct,
    required this.rpmForPulse,
    required this.vehicle,
    this.use3d = false,
    this.carColor,
    this.wheelColor = const Color(0xFF0A0A0A),
    this.lamps = const LampState(),
  });

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(builder: (_, c) {
      final w = c.maxWidth;
      final carW = w * 0.40;
      final carH = c.maxHeight.clamp(180.0, 320.0);
      return Stack(
        alignment: Alignment.center,
        children: [
          // glow behind the car, pulses with RPM
          Center(child: _RpmGlow(rpm: rpmForPulse, size: carW * 1.6)),
          // Centre: rotatable 3D model when enabled AND this vehicle has a GLB;
          // otherwise the flat SVG silhouette (also the safe WebView fallback).
          if (use3d && vehicle.has3d)
            Align(
              // Lifted slightly to fill the space below the RPM readout and
              // sized large so the car reads as the centrepiece.
              alignment: const Alignment(0, -0.10),
              child: SizedBox(
                width: w * 0.88, height: carH * 1.20,
                child: CarModel3D(
                  src: vehicle.glbAsset!,
                  alt: '${vehicle.name} 3D model',
                  credit: vehicle.credit ?? '',
                  materials: vehicle.materials,
                  bodyColor: carColor,
                  wheelColor: wheelColor,
                  lamps: lamps,
                ),
              ),
            )
          else
            Center(
              child: SizedBox(
                width: carW, height: carH,
                child: SvgPicture.asset(
                  vehicle.silhouetteAsset,
                  fit: BoxFit.contain,
                  semanticsLabel: '${vehicle.name} silhouette',
                ),
              ),
            ),
          // When 3D is on but this car has no GLB yet, say so rather than
          // silently showing a placeholder silhouette.
          if (use3d && !vehicle.has3d)
            Positioned(
              left: 0, right: 0, bottom: 4,
              child: Center(
                child: Text(
                  '${vehicle.name} · 3D model coming soon',
                  style: const TextStyle(
                      fontSize: 9, color: Color(0xFF5A5A5E), letterSpacing: 0.2),
                ),
              ),
            ),
          // 4 corner pods
          Positioned(
            left: 0, top: 0,
            child: _CornerPod(
              label: 'COOLANT',
              value: coolantC == null ? '—' : coolantC!.round().toString(),
              unit: '°C',
              color: _coolantColor(coolantC),
            ),
          ),
          Positioned(
            right: 0, top: 0,
            child: _CornerPod(
              label: 'INTAKE',
              value: intakeC == null ? '—' : intakeC!.round().toString(),
              unit: '°C',
              color: _T.cool,
              align: CrossAxisAlignment.end,
            ),
          ),
          Positioned(
            left: 0, bottom: 0,
            child: _CornerPod(
              label: 'BATTERY',
              value: batteryV == null ? '—' : batteryV!.toStringAsFixed(1),
              unit: 'V',
              color: _batteryColor(batteryV),
            ),
          ),
          Positioned(
            right: 0, bottom: 0,
            child: _CornerPod(
              label: 'FUEL',
              value: fuelPct == null ? '—' : fuelPct!.round().toString(),
              unit: '%',
              color: _T.textHi,
              align: CrossAxisAlignment.end,
            ),
          ),
        ],
      );
    });
  }

  Color _coolantColor(double? c) {
    if (c == null) return _T.textMid;
    if (c >= 105) return _T.accent;
    if (c >= 100) return _T.warning;
    if (c >= 75) return _T.live;
    return _T.cool;
  }

  Color _batteryColor(double? v) {
    if (v == null) return _T.textMid;
    if (v < 11.8) return _T.accent;
    if (v < 12.4) return _T.warning;
    return _T.live;
  }
}

class _RpmGlow extends StatelessWidget {
  final double rpm;
  final double size;
  const _RpmGlow({required this.rpm, required this.size});

  @override
  Widget build(BuildContext context) {
    // Map RPM 0-7000 to glow alpha 0.02-0.25
    final t = (rpm.clamp(0, 7000) / 7000.0).toDouble();
    final alpha = 0.04 + 0.22 * t;
    return TweenAnimationBuilder<double>(
      tween: Tween(begin: 0, end: alpha),
      duration: const Duration(milliseconds: 180),
      builder: (_, a, _) => Container(
        width: size, height: size,
        decoration: BoxDecoration(
          shape: BoxShape.circle,
          gradient: RadialGradient(
            colors: [
              _T.accent.withValues(alpha: a),
              _T.accent.withValues(alpha: 0),
            ],
          ),
        ),
      ),
    );
  }
}

class _CornerPod extends StatelessWidget {
  final String label;
  final String value;
  final String unit;
  final Color color;
  final CrossAxisAlignment align;
  const _CornerPod({
    required this.label,
    required this.value,
    required this.unit,
    required this.color,
    this.align = CrossAxisAlignment.start,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 84,
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
      decoration: BoxDecoration(
        color: _T.surface,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: _T.divider, width: 1),
      ),
      child: Column(
        crossAxisAlignment: align,
        mainAxisSize: MainAxisSize.min,
        children: [
          Text(label,
              style: const TextStyle(
                fontSize: 9, color: _T.textMid,
                letterSpacing: 1.5, fontWeight: FontWeight.w600,
              )),
          const SizedBox(height: 4),
          Row(
            crossAxisAlignment: CrossAxisAlignment.baseline,
            textBaseline: TextBaseline.alphabetic,
            children: [
              Text(value,
                  style: TextStyle(
                    fontSize: 24, color: color,
                    fontWeight: FontWeight.w400, height: 1.0,
                    fontFeatures: const [FontFeature.tabularFigures()],
                  )),
              const SizedBox(width: 2),
              Text(unit,
                  style: const TextStyle(
                    fontSize: 10, color: _T.textMid,
                  )),
            ],
          ),
        ],
      ),
    );
  }
}

// ─── Metric bar (linear gauges below the car) ───────────────────────────────
class _MetricBar extends StatelessWidget {
  final String label;
  final double value;
  final String unit;
  final double max;
  const _MetricBar({
    required this.label, required this.value,
    required this.unit, required this.max,
  });

  @override
  Widget build(BuildContext context) {
    final fraction = (value / max).clamp(0.0, 1.0);
    return Row(
      children: [
        SizedBox(
          width: 78,
          child: Text(label,
              style: const TextStyle(
                fontSize: 10, color: _T.textMid,
                letterSpacing: 1.5, fontWeight: FontWeight.w600,
              )),
        ),
        Expanded(
          child: Container(
            height: 6,
            decoration: BoxDecoration(
              color: _T.surface,
              borderRadius: BorderRadius.circular(3),
            ),
            child: ClipRRect(
              borderRadius: BorderRadius.circular(3),
              child: Stack(
                children: [
                  TweenAnimationBuilder<double>(
                    tween: Tween(begin: 0, end: fraction),
                    duration: const Duration(milliseconds: 220),
                    curve: Curves.easeOutCubic,
                    builder: (_, f, _) => FractionallySizedBox(
                      widthFactor: f,
                      child: Container(color: _T.textHi),
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),
        const SizedBox(width: 10),
        SizedBox(
          width: 92,
          child: FittedBox(
            alignment: Alignment.centerRight,
            fit: BoxFit.scaleDown,
            child: Row(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.baseline,
              textBaseline: TextBaseline.alphabetic,
              children: [
                Text(value.toStringAsFixed(value > 99 ? 0 : 1),
                    style: const TextStyle(
                      fontSize: 14, color: _T.textHi,
                      fontFeatures: [FontFeature.tabularFigures()],
                    )),
                const SizedBox(width: 3),
                Text(unit,
                    style: const TextStyle(fontSize: 10, color: _T.textMid)),
              ],
            ),
          ),
        ),
      ],
    );
  }
}

// ─── Bottom strip ───────────────────────────────────────────────────────────
class _BottomStrip extends StatelessWidget {
  final int backlogTotal;
  final int backlogPending;
  final int backlogUploaded;
  final UploadTick? lastTick;
  final int dtcCount;
  final String? ecuName;
  final VoidCallback onStored;
  final VoidCallback onSynced;
  final VoidCallback onWait;
  final VoidCallback onDtc;
  final VoidCallback onMore;
  const _BottomStrip({
    required this.backlogTotal,
    required this.backlogPending,
    required this.backlogUploaded,
    required this.lastTick,
    required this.dtcCount,
    required this.ecuName,
    required this.onStored,
    required this.onSynced,
    required this.onWait,
    required this.onDtc,
    required this.onMore,
  });

  @override
  Widget build(BuildContext context) {
    final cloudColor = switch (lastTick) {
      null => _T.textLow,
      UploadTick(success: true, attempted: 0) => _T.textMid,
      UploadTick(success: true) => _T.live,
      UploadTick(error: final e?) when e.isNotEmpty => _T.accent,
      _ => _T.textMid,
    };
    final dtcColor = dtcCount > 0 ? _T.warning : _T.textMid;

    return Container(
      decoration: const BoxDecoration(
        color: _T.surface,
        border: Border(top: BorderSide(color: _T.divider)),
      ),
      padding: const EdgeInsets.fromLTRB(8, 6, 8, 8),
      child: Row(
        children: [
          Expanded(
            child: _StatButton(
              onTap: onStored,
              child: _Stat(icon: Icons.storage,
                  value: '$backlogTotal', label: 'STORED'),
            ),
          ),
          const _Divider(),
          Expanded(
            child: _StatButton(
              onTap: onSynced,
              child: _Stat(icon: Icons.cloud_upload,
                  value: '$backlogUploaded', label: 'SYNCED',
                  color: cloudColor),
            ),
          ),
          const _Divider(),
          Expanded(
            child: _StatButton(
              onTap: onWait,
              child: _Stat(icon: Icons.pending,
                  value: '$backlogPending', label: 'WAIT'),
            ),
          ),
          const _Divider(),
          Expanded(
            child: _StatButton(
              onTap: onDtc,
              child: _Stat(icon: Icons.warning_amber,
                  value: '$dtcCount', label: 'DTC', color: dtcColor),
            ),
          ),
          const _Divider(),
          _StatButton(
            onTap: onMore,
            child: const Padding(
              padding: EdgeInsets.symmetric(horizontal: 4, vertical: 6),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Icon(Icons.expand_less, size: 16, color: _T.textMid),
                  SizedBox(height: 2),
                  Text('MORE',
                      style: TextStyle(
                        fontSize: 8, color: _T.textMid,
                        letterSpacing: 1.2, fontWeight: FontWeight.w700,
                      )),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _StatButton extends StatelessWidget {
  final Widget child;
  final VoidCallback onTap;
  const _StatButton({required this.child, required this.onTap});

  @override
  Widget build(BuildContext context) {
    return Material(
      color: Colors.transparent,
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(6),
        splashColor: _T.surface2,
        highlightColor: _T.surface2,
        child: Padding(
          padding: const EdgeInsets.symmetric(vertical: 4, horizontal: 2),
          child: child,
        ),
      ),
    );
  }
}

class _Stat extends StatelessWidget {
  final IconData icon;
  final String value;
  final String label;
  final Color? color;
  const _Stat({required this.icon, required this.value, required this.label,
               this.color});

  @override
  Widget build(BuildContext context) {
    final c = color ?? _T.textHi;
    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.center,
      children: [
        Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(icon, size: 11, color: c),
            const SizedBox(width: 4),
            Flexible(
              child: Text(
                value,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(
                  fontSize: 13, color: c,
                  fontFeatures: const [FontFeature.tabularFigures()],
                ),
              ),
            ),
          ],
        ),
        const SizedBox(height: 1),
        Text(label,
            overflow: TextOverflow.ellipsis,
            style: const TextStyle(
              fontSize: 8, color: _T.textMid,
              letterSpacing: 1.2, fontWeight: FontWeight.w700,
            )),
      ],
    );
  }
}

class _Divider extends StatelessWidget {
  const _Divider();
  @override
  Widget build(BuildContext context) => Container(
        width: 1, height: 16, color: _T.divider,
        margin: const EdgeInsets.symmetric(horizontal: 6),
      );
}

// ─── Error banner (WS issues) ───────────────────────────────────────────────
class _ErrorBanner extends StatelessWidget {
  final String detail;
  const _ErrorBanner({required this.detail});

  @override
  Widget build(BuildContext context) {
    final d = detail.toLowerCase();
    final isOldApk = d.contains('operation not permitted') || d.contains('errno = 1');
    // Translate raw socket exceptions into a calm, human message. Offline is a
    // normal state (e.g. not on the Pi's network yet) — don't show a stack trace.
    final String message;
    final Color tone;
    if (isOldApk) {
      message = 'Old APK — uninstall and reinstall the current build to '
          'restore network access.';
      tone = _T.warning;
    } else {
      message = "Not connected to the Pi. Join the Pi's hotspot/Wi-Fi and it "
          'will link automatically.';
      tone = _T.textMid;
    }
    return Container(
      margin: const EdgeInsets.symmetric(horizontal: 12),
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
      decoration: BoxDecoration(
        color: _T.surface,
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: _T.divider),
      ),
      child: Row(
        children: [
          Icon(isOldApk ? Icons.warning_amber_rounded : Icons.wifi_off_rounded,
              size: 16, color: tone),
          const SizedBox(width: 10),
          Expanded(
            child: Text(message,
                style: TextStyle(
                    color: isOldApk ? _T.warning : _T.textMid, fontSize: 12)),
          ),
        ],
      ),
    );
  }
}

// ─── Bottom sheet showing every signal ──────────────────────────────────────
class _MoreSheet extends StatelessWidget {
  final Map<String, Sample> latest;
  const _MoreSheet({required this.latest});

  @override
  Widget build(BuildContext context) {
    final keys = latest.keys.toList()..sort();
    return DraggableScrollableSheet(
      expand: false,
      initialChildSize: 0.62,
      minChildSize: 0.25,
      maxChildSize: 0.95,
      builder: (_, scroll) => Container(
        decoration: const BoxDecoration(
          color: _T.surface,
          borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
          border: Border(
            top: BorderSide(color: _T.divider),
            left: BorderSide(color: _T.divider),
            right: BorderSide(color: _T.divider),
          ),
        ),
        child: Column(
          children: [
            Container(
              margin: const EdgeInsets.only(top: 8),
              width: 40, height: 4,
              decoration: BoxDecoration(
                color: _T.textLow,
                borderRadius: BorderRadius.circular(2),
              ),
            ),
            const Padding(
              padding: EdgeInsets.fromLTRB(20, 16, 20, 8),
              child: Row(children: [
                Text('ALL SIGNALS', style: TextStyle(
                  fontSize: 11, color: _T.textMid,
                  letterSpacing: 2, fontWeight: FontWeight.w700,
                )),
              ]),
            ),
            const Divider(height: 1, color: _T.divider),
            Expanded(
              child: ListView.separated(
                controller: scroll,
                itemCount: keys.length,
                separatorBuilder: (_, _) =>
                    const Divider(height: 1, color: _T.divider),
                itemBuilder: (_, i) {
                  final s = latest[keys[i]]!;
                  final isString = s.signal.startsWith('session.');
                  return Padding(
                    padding: const EdgeInsets.symmetric(
                        horizontal: 20, vertical: 10),
                    child: Row(
                      children: [
                        Expanded(
                          child: Text(s.signal,
                              style: const TextStyle(
                                  color: _T.textHi, fontSize: 13,
                                  fontFamily: 'monospace')),
                        ),
                        Text(
                          isString
                              ? s.unit
                              : '${_fmt(s.value)} ${s.unit}'.trim(),
                          style: const TextStyle(
                            color: _T.textMid, fontSize: 13,
                            fontFeatures: [FontFeature.tabularFigures()],
                          ),
                        ),
                      ],
                    ),
                  );
                },
              ),
            ),
          ],
        ),
      ),
    );
  }

  String _fmt(double v) {
    if (v.abs() >= 1000) return v.toStringAsFixed(0);
    if (v.abs() >= 10) return v.toStringAsFixed(1);
    return v.toStringAsFixed(2);
  }
}

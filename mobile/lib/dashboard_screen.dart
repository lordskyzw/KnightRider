import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_svg/flutter_svg.dart';

import 'app_theme.dart';
import 'backlog_db.dart';
import 'batches_screen.dart';
import 'car_model.dart';
import 'config.dart';
import 'dtc_screen.dart';
import 'pi_client.dart';
import 'sample.dart';
import 'sensor_map.dart';
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

  bool _car3d = true; // 3D model by default (silhouette only as fallback)
  bool _autoRotate = true; // showroom turntable
  bool _inDepth = false; // X-ray sensor mode
  CarBackground _carBg = kCarBackgrounds.first; // backdrop behind the car
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
    _car3d = await PiConfig.car3dEnabled();
    _autoRotate = await PiConfig.carAutoRotate();
    _carBg = carBackgroundById(await PiConfig.carBackground());
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
      final autoRotate = await PiConfig.carAutoRotate();
      final carBg = carBackgroundById(await PiConfig.carBackground());
      final vehicle = vehicleById(await PiConfig.vehicleId());
      final carColorArgb = await PiConfig.carColor();
      final wheelArgb = await PiConfig.wheelColor();
      final lightsOn = await PiConfig.lightsOn();
      if (mounted) {
        setState(() {
          _car3d = car3d;
          _autoRotate = autoRotate;
          _carBg = carBg;
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

  /// Sensor-node status for the X-ray hotspots: fault if a mapped DTC is
  /// stored, live if we've received any of its signals, else available.
  String _sensorStatus(SensorNode n) {
    for (final k in n.faultKeys) {
      if (_latest.containsKey(k)) return 'fault';
    }
    for (final k in n.signals) {
      if (_latest.containsKey(k)) return 'live';
    }
    return 'available';
  }

  void _openSensor(String id) {
    final node = kSensorNodes.firstWhere((n) => n.id == id,
        orElse: () => kSensorNodes.first);
    showModalBottomSheet<void>(
      context: context,
      backgroundColor: Colors.transparent,
      // Close by dragging the tray down only — tapping outside must NOT dismiss
      // it (that abrupt uncover was reloading the WebView and moving the camera).
      isDismissible: false,
      enableDrag: true,
      builder: (_) => _SensorSheet(node: node, latest: Map.of(_latest)),
    );
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
          'dbc.toyota.BRAKE.pressed', // field-verified Axio brake switch
          'dbc.toyota.STOP_LAMP.on',
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
    final dtcCount = _v('obd.dtc_count')?.toInt() ?? 0;

    final dbcRpm = _v('dbc.toyota.POWERTRAIN.engine_rpm');
    final liveRpm = (dbcRpm != null && dbcRpm > 0) ? dbcRpm : rpm;
    final live = _linkStatus == LinkStatus.live;

    return Scaffold(
      backgroundColor: _T.bg,
      body: _Backdrop(
        bg: _carBg,
        child: SafeArea(
        child: Column(
          children: [
            _TopStatusBar(status: _linkStatus, onSettings: _openSettings),
            if (_linkStatus == LinkStatus.offline && _wsError != null)
              _ErrorBanner(detail: _wsError!),
            // The car is the hero — it gets the lion's share of the screen.
            Expanded(
              child: _CarVisualizer(
                rpmForPulse: liveRpm,
                use3d: _car3d,
                autoRotate: _autoRotate,
                vehicle: _vehicle,
                carColor: _carColor,
                wheelColor: _wheelColor,
                lamps: _lampState(),
                inDepth: _inDepth,
                onToggleDepth: () => setState(() => _inDepth = !_inDepth),
                sensorStatus: {
                  for (final n in kSensorNodes) n.id: _sensorStatus(n),
                },
                onHotspotTap: _openSensor,
              ),
            ),
            Padding(
                padding: const EdgeInsets.fromLTRB(24, 0, 24, 4),
                child: Column(
                  children: [
                    // One adaptive numeral: speed when moving, RPM when idling.
                    _HeroMetric(rpm: liveRpm, speedKmh: speed, isLive: live),
                    const SizedBox(height: 18),
                    // One quiet row of secondary stats — no boxes.
                    _StatRow(
                      coolant: coolant, battery: battery,
                      fuel: fuel, intake: intake, live: live,
                    ),
                    const SizedBox(height: 16),
                    _ThinStatus(
                      total: _backlogTotal,
                      pending: _backlogPending,
                      uploaded: _backlogUploaded,
                      lastTick: _lastTick,
                      dtcCount: dtcCount,
                      onSync: () => _openBatches(BatchFilter.all),
                      onDtc: _openDtc,
                      onMore: _openMore,
                    ),
                  ],
                ),
              ),
          ],
        ),
      ),
      ),
    );
  }
}

// ─── Car backdrop (gradient + optional blueprint grid) ──────────────────────
class _Backdrop extends StatelessWidget {
  final CarBackground bg;
  final Widget child;
  const _Backdrop({required this.bg, required this.child});

  @override
  Widget build(BuildContext context) {
    return DecoratedBox(
      decoration: BoxDecoration(gradient: bg.gradient),
      child: bg.grid == null
          ? child
          : CustomPaint(painter: _GridPainter(bg.grid!), child: child),
    );
  }
}

/// A faint engineering-blueprint grid. Drawn over the gradient, behind the car.
class _GridPainter extends CustomPainter {
  final Color color;
  const _GridPainter(this.color);

  static const double _cell = 38; // grid spacing in logical px

  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..color = color
      ..strokeWidth = 1;
    for (double x = 0; x <= size.width; x += _cell) {
      canvas.drawLine(Offset(x, 0), Offset(x, size.height), paint);
    }
    for (double y = 0; y <= size.height; y += _cell) {
      canvas.drawLine(Offset(0, y), Offset(size.width, y), paint);
    }
  }

  @override
  bool shouldRepaint(_GridPainter old) => old.color != color;
}

// ─── Top status bar ─────────────────────────────────────────────────────────
class _TopStatusBar extends StatelessWidget {
  final LinkStatus status;
  final VoidCallback onSettings;
  const _TopStatusBar({required this.status, required this.onSettings});

  @override
  Widget build(BuildContext context) {
    // Three honest states: OFFLINE (can't reach Pi), LINKED (reached the Pi but
    // no fresh telemetry), LIVE (data flowing). Only LIVE pulses green. Host,
    // VIN and build version are deliberately not here — they live in Settings.
    final (label, color) = switch (status) {
      LinkStatus.live => ('LIVE', _T.live),
      LinkStatus.linked => ('LINKED', _T.warning),
      LinkStatus.offline => ('OFFLINE', _T.accent),
    };
    return Padding(
      padding: const EdgeInsets.fromLTRB(22, 14, 8, 6),
      child: Row(
        children: [
          _PulseDot(color: color, pulsing: status == LinkStatus.live),
          const SizedBox(width: 10),
          Text(label,
              style: TextStyle(
                fontSize: 11, color: color,
                fontWeight: FontWeight.w700, letterSpacing: 2.5,
              )),
          const Spacer(),
          IconButton(
            iconSize: 19,
            onPressed: onSettings,
            icon: const Icon(Icons.settings_outlined, color: _T.textMid),
            tooltip: 'Settings',
          ),
        ],
      ),
    );
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

// ─── Hero metric — adaptive: speed when moving, RPM when idling ──────────────
class _HeroMetric extends StatelessWidget {
  final double rpm;
  final double speedKmh;
  final bool isLive;
  const _HeroMetric({
    required this.rpm,
    required this.speedKmh,
    required this.isLive,
  });

  @override
  Widget build(BuildContext context) {
    final moving = speedKmh > 3;
    final value = moving ? speedKmh : rpm;
    final label = moving ? 'KM/H' : 'RPM';
    // Rest dim when there's no live data, so offline reads as 'asleep', not broken.
    final color = isLive ? _T.textHi : _T.textLow;
    return TweenAnimationBuilder<double>(
      tween: Tween(begin: 0, end: value),
      duration: const Duration(milliseconds: 240),
      curve: Curves.easeOutCubic,
      builder: (_, v, _) => Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Text(v.round().toString(),
              style: TextStyle(
                fontSize: 80, height: 0.95, color: color,
                fontWeight: FontWeight.w200, letterSpacing: -2,
                fontFeatures: const [FontFeature.tabularFigures()],
              )),
          const SizedBox(height: 4),
          Text(label,
              style: const TextStyle(
                fontSize: 11, color: _T.textMid,
                letterSpacing: 4, fontWeight: FontWeight.w600,
              )),
        ],
      ),
    );
  }
}

// ─── Car (the hero) ─────────────────────────────────────────────────────────
class _CarVisualizer extends StatelessWidget {
  final double rpmForPulse;
  final bool use3d;
  final bool autoRotate;
  final VehicleModel vehicle;
  final Color? carColor;
  final Color wheelColor;
  final LampState lamps;
  final bool inDepth;
  final VoidCallback onToggleDepth;
  final Map<String, String> sensorStatus;
  final void Function(String id) onHotspotTap;
  const _CarVisualizer({
    required this.rpmForPulse,
    required this.vehicle,
    required this.onToggleDepth,
    required this.sensorStatus,
    required this.onHotspotTap,
    this.use3d = false,
    this.autoRotate = true,
    this.inDepth = false,
    this.carColor,
    this.wheelColor = const Color(0xFF0A0A0A),
    this.lamps = const LampState(),
  });

  @override
  Widget build(BuildContext context) {
    final canXray = use3d && vehicle.has3d;
    return LayoutBuilder(builder: (_, c) {
      final w = c.maxWidth;
      final carH = c.maxHeight.clamp(180.0, 360.0);
      final glowSize = (w * 0.72).clamp(180.0, 340.0).toDouble();
      return Stack(
        alignment: Alignment.center,
        children: [
          // Soft glow behind the car; brightens gently with RPM.
          Center(child: _RpmGlow(rpm: rpmForPulse, size: glowSize)),
          // The car fills the hero space: rotatable 3D model when enabled AND
          // this vehicle has a GLB; otherwise the flat SVG silhouette.
          if (canXray)
            Positioned.fill(
              child: CarModel3D(
                src: vehicle.glbAsset!,
                alt: '${vehicle.name} 3D model',
                credit: vehicle.credit ?? '',
                materials: vehicle.materials,
                frontZ: vehicle.frontZ,
                autoRotate: autoRotate,
                inDepth: inDepth,
                sensorStatus: sensorStatus,
                onHotspotTap: onHotspotTap,
                bodyColor: carColor,
                wheelColor: wheelColor,
                lamps: lamps,
              ),
            )
          else
            Center(
              child: SizedBox(
                width: w * 0.55, height: carH,
                child: SvgPicture.asset(
                  vehicle.silhouetteAsset,
                  fit: BoxFit.contain,
                  semanticsLabel: '${vehicle.name} silhouette',
                ),
              ),
            ),
          // 3D on but no GLB for this car yet — say so rather than imply it's real.
          if (use3d && !vehicle.has3d)
            Positioned(
              left: 0, right: 0, bottom: 6,
              child: Center(
                child: Text(
                  '${vehicle.name} · 3D model coming soon',
                  style: const TextStyle(
                      fontSize: 9, color: Color(0xFF5A5A5E), letterSpacing: 0.2),
                ),
              ),
            ),
          // X-ray toggle — reveals the glowing sensor map. Only when there's a
          // 3D model to ghost.
          if (canXray)
            Positioned(
              right: 2, top: 2,
              child: _XrayToggle(active: inDepth, onTap: onToggleDepth),
            ),
        ],
      );
    });
  }
}

// ─── X-ray toggle (enters/exits the in-depth sensor view) ───────────────────
class _XrayToggle extends StatelessWidget {
  final bool active;
  final VoidCallback onTap;
  const _XrayToggle({required this.active, required this.onTap});

  @override
  Widget build(BuildContext context) {
    final c = active ? _T.accent : _T.textMid;
    return Material(
      color: Colors.transparent,
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(20),
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 11, vertical: 7),
          decoration: BoxDecoration(
            color: active ? _T.accent.withValues(alpha: 0.14) : _T.surface,
            borderRadius: BorderRadius.circular(20),
            border: Border.all(
                color: active ? _T.accent.withValues(alpha: 0.6) : _T.divider),
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(active ? Icons.sensors : Icons.sensors_outlined,
                  size: 14, color: c),
              const SizedBox(width: 6),
              Text('X-RAY',
                  style: TextStyle(
                    fontSize: 10, color: c,
                    letterSpacing: 1.5, fontWeight: FontWeight.w700,
                  )),
            ],
          ),
        ),
      ),
    );
  }
}

// ─── Quiet stat row (replaces the floating corner pods) ─────────────────────
class _StatRow extends StatelessWidget {
  final double? coolant; // °C
  final double? battery; // V
  final double? fuel;    // %
  final double? intake;  // °C
  final bool live;
  const _StatRow({
    required this.coolant,
    required this.battery,
    required this.fuel,
    required this.intake,
    required this.live,
  });

  static Color _coolantColor(double? c) {
    if (c == null) return _T.textMid;
    if (c >= 105) return _T.accent;
    if (c >= 100) return _T.warning;
    if (c >= 75) return _T.live;
    return _T.cool;
  }

  static Color _batteryColor(double? v) {
    if (v == null) return _T.textMid;
    if (v < 11.8) return _T.accent;
    if (v < 12.4) return _T.warning;
    return _T.live;
  }

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisAlignment: MainAxisAlignment.spaceBetween,
      children: [
        _StatCell(
          label: 'COOLANT',
          value: coolant == null ? '—' : '${coolant!.round()}°',
          color: live ? _coolantColor(coolant) : _T.textLow,
        ),
        _StatCell(
          label: 'BATTERY',
          value: battery == null ? '—' : '${battery!.toStringAsFixed(1)}v',
          color: live ? _batteryColor(battery) : _T.textLow,
        ),
        _StatCell(
          label: 'FUEL',
          value: fuel == null ? '—' : '${fuel!.round()}%',
          color: live ? _T.textHi : _T.textLow,
        ),
        _StatCell(
          label: 'INTAKE',
          value: intake == null ? '—' : '${intake!.round()}°',
          color: live ? _T.textHi : _T.textLow,
        ),
      ],
    );
  }
}

class _StatCell extends StatelessWidget {
  final String label;
  final String value;
  final Color color;
  const _StatCell({required this.label, required this.value, required this.color});

  @override
  Widget build(BuildContext context) {
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        Text(value,
            style: TextStyle(
              fontSize: 20, color: color, fontWeight: FontWeight.w400,
              height: 1.0,
              fontFeatures: const [FontFeature.tabularFigures()],
            )),
        const SizedBox(height: 5),
        Text(label,
            style: const TextStyle(
              fontSize: 9, color: _T.textMid,
              letterSpacing: 1.5, fontWeight: FontWeight.w600,
            )),
      ],
    );
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

// ─── Thin status line (replaces the heavy tab strip) ────────────────────────
class _ThinStatus extends StatelessWidget {
  final int total;
  final int pending;
  final int uploaded;
  final UploadTick? lastTick;
  final int dtcCount;
  final VoidCallback onSync;
  final VoidCallback onDtc;
  final VoidCallback onMore;
  const _ThinStatus({
    required this.total,
    required this.pending,
    required this.uploaded,
    required this.lastTick,
    required this.dtcCount,
    required this.onSync,
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
    final syncText = total == 0
        ? 'no data yet'
        : (pending == 0 ? '$total synced' : '$uploaded / $total synced');
    final dtcOk = dtcCount == 0;

    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(height: 1, color: _T.divider),
        const SizedBox(height: 6),
        Row(
          children: [
            _StatusChip(onTap: onSync, dotColor: cloudColor, text: syncText),
            const Spacer(),
            _StatusChip(
              onTap: onDtc,
              icon: dtcOk ? Icons.verified_outlined : Icons.warning_amber_rounded,
              text: dtcOk ? 'no faults' : '$dtcCount DTC',
              color: dtcOk ? _T.textMid : _T.accent,
            ),
            const SizedBox(width: 2),
            IconButton(
              onPressed: onMore,
              iconSize: 18,
              visualDensity: VisualDensity.compact,
              icon: const Icon(Icons.more_horiz, color: _T.textMid),
              tooltip: 'All signals',
            ),
          ],
        ),
      ],
    );
  }
}

/// A quiet, tappable status chip: either a coloured heartbeat dot (sync) or a
/// small icon (DTC), followed by text.
class _StatusChip extends StatelessWidget {
  final VoidCallback onTap;
  final String text;
  final IconData? icon;
  final Color? color;
  final Color? dotColor;
  const _StatusChip({
    required this.onTap,
    required this.text,
    this.icon,
    this.color,
    this.dotColor,
  });

  @override
  Widget build(BuildContext context) {
    final c = color ?? _T.textMid;
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(8),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 6),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            if (dotColor != null)
              Container(width: 6, height: 6, decoration: BoxDecoration(
                  shape: BoxShape.circle, color: dotColor))
            else if (icon != null)
              Icon(icon, size: 13, color: c),
            const SizedBox(width: 7),
            Text(text,
                style: TextStyle(
                  fontSize: 11.5, color: c,
                  letterSpacing: 0.3, fontWeight: FontWeight.w500,
                )),
          ],
        ),
      ),
    );
  }
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

// ─── Sensor hotspot detail sheet (tapped in X-ray mode) ─────────────────────
class _SensorSheet extends StatelessWidget {
  final SensorNode node;
  final Map<String, Sample> latest;
  const _SensorSheet({required this.node, required this.latest});

  @override
  Widget build(BuildContext context) {
    final faults = node.faultKeys.where(latest.containsKey).toList();
    final hasLive = node.signals.any(latest.containsKey);
    final (statusText, statusColor) = faults.isNotEmpty
        ? ('FAULT', _T.accent)
        : (hasLive ? ('LIVE', _T.live) : ('AVAILABLE', _T.textMid));

    return Container(
      decoration: const BoxDecoration(
        color: _T.surface,
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
        border: Border(
          top: BorderSide(color: _T.divider),
          left: BorderSide(color: _T.divider),
          right: BorderSide(color: _T.divider),
        ),
      ),
      child: SafeArea(
        top: false,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Center(
              child: Container(
                margin: const EdgeInsets.only(top: 8, bottom: 4),
                width: 40, height: 4,
                decoration: BoxDecoration(
                    color: _T.textLow, borderRadius: BorderRadius.circular(2)),
              ),
            ),
            Padding(
              padding: const EdgeInsets.fromLTRB(20, 12, 20, 4),
              child: Row(
                children: [
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(node.label,
                            style: const TextStyle(
                                color: _T.textHi, fontSize: 18,
                                fontWeight: FontWeight.w600)),
                        const SizedBox(height: 2),
                        Text(node.where,
                            style: const TextStyle(
                                color: _T.textMid, fontSize: 12)),
                      ],
                    ),
                  ),
                  Container(
                    padding:
                        const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                    decoration: BoxDecoration(
                      color: statusColor.withValues(alpha: 0.14),
                      borderRadius: BorderRadius.circular(5),
                    ),
                    child: Text(statusText,
                        style: TextStyle(
                          fontSize: 9, color: statusColor,
                          letterSpacing: 1.5, fontWeight: FontWeight.w700,
                        )),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 4),
            const Divider(height: 1, color: _T.divider),
            for (final k in node.signals)
              _SensorSignalRow(signalKey: k, sample: latest[k]),
            if (faults.isNotEmpty)
              Padding(
                padding: const EdgeInsets.fromLTRB(20, 10, 20, 4),
                child: Row(
                  children: [
                    Icon(Icons.warning_amber_rounded,
                        size: 15, color: _T.accent),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Text(
                        'Stored fault: ${faults.map((f) => f.split('.').last.toUpperCase()).join(', ')}',
                        style: TextStyle(color: _T.accent, fontSize: 12),
                      ),
                    ),
                  ],
                ),
              ),
            const SizedBox(height: 14),
          ],
        ),
      ),
    );
  }
}

class _SensorSignalRow extends StatelessWidget {
  final String signalKey;
  final Sample? sample;
  const _SensorSignalRow({required this.signalKey, required this.sample});

  @override
  Widget build(BuildContext context) {
    final s = sample;
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 9),
      child: Row(
        children: [
          Expanded(
            child: Text(signalKey,
                style: TextStyle(
                  fontFamily: 'monospace', fontSize: 12, color: _T.accent,
                )),
          ),
          const SizedBox(width: 12),
          if (s == null)
            const Text('— not received',
                style: TextStyle(fontSize: 12, color: _T.textLow))
          else
            Row(
              crossAxisAlignment: CrossAxisAlignment.baseline,
              textBaseline: TextBaseline.alphabetic,
              children: [
                Text(s.value.toStringAsFixed(s.value.abs() >= 100 ? 0 : 2),
                    style: const TextStyle(
                      fontSize: 15, color: _T.textHi,
                      fontFeatures: [FontFeature.tabularFigures()],
                    )),
                if (s.unit.isNotEmpty) ...[
                  const SizedBox(width: 3),
                  Text(s.unit,
                      style: const TextStyle(fontSize: 10, color: _T.textMid)),
                ],
              ],
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

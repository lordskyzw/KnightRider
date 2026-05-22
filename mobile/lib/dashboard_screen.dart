import 'dart:async';

import 'package:flutter/material.dart';

import 'backlog_db.dart';
import 'config.dart';
import 'pi_client.dart';
import 'sample.dart';
import 'settings_screen.dart';
import 'uploader.dart';
import 'ws_client.dart';

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
  String? _wsUri;
  String? _backlogPullError;
  final Map<String, Sample> _latest = {};

  // Backlog state
  final _db = BacklogDb();
  int _backlogTotal = 0;
  int _backlogPending = 0;
  int _backlogUploaded = 0;
  bool _pulling = false;
  Timer? _pullTimer;

  // Uploader state
  Uploader? _uploader;
  UploadTick? _lastTick;
  StreamSubscription<UploadTick>? _tickSub;

  StreamSubscription<Sample>? _sampleSub;
  StreamSubscription<WsState>? _stateSub;

  @override
  void initState() {
    super.initState();
    _boot();
  }

  Future<void> _boot() async {
    _host = await PiConfig.host();
    _cloudUrl = await PiConfig.cloudUrl();
    _attachWs();

    _uploader = Uploader(db: _db, cloudUrlProvider: () => _cloudUrl);
    _tickSub = _uploader!.ticks.listen((t) {
      setState(() => _lastTick = t);
      _refreshBacklogCounts();
    });
    _uploader!.start();

    await _refreshBacklogCounts();
    _pullTimer = Timer.periodic(const Duration(seconds: 30), (_) => _kickBacklogPull());
  }

  void _onSample(Sample s) {
    setState(() => _latest[s.signal] = s);
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
      // Successful pull — clear any stale error from a previous failure.
      if (mounted) setState(() => _backlogPullError = null);
    } catch (e) {
      if (mounted) setState(() => _backlogPullError = e.toString());
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
      // Clear stale diagnostics from the previous host before reconnecting.
      setState(() {
        _wsError = null;
        _wsUri = null;
        _backlogPullError = null;
      });
      await _ws?.dispose();
      _sampleSub?.cancel();
      _stateSub?.cancel();
      _attachWs();
      // Uploader reads _cloudUrl via the provider closure each tick — no
      // restart needed.
    }
  }

  /// Creates a fresh WsClient + listeners. Used by both _boot and
  /// _openSettings so the diagnostic fields (_wsError, _wsUri) get updated
  /// consistently on every state change.
  void _attachWs() {
    _ws = WsClient(hostProvider: () => _host);
    _sampleSub = _ws!.stream.listen((sample) {
      // First sample after a (re)connect proves the channel is genuinely
      // up — clear any lingering banner from the previous failure.
      if (_wsError != null && mounted) {
        setState(() => _wsError = null);
      }
      _onSample(sample);
    });
    _stateSub = _ws!.stateStream.listen((s) {
      setState(() {
        _state = s;
        _wsUri = _ws?.lastTriedUri;
        // Only surface an error when fully disconnected — during CONNECTING
        // the previous error is stale and would flash on every reconnect.
        if (s == WsState.connected || s == WsState.connecting) {
          _wsError = null;
        } else {
          _wsError = _ws?.lastError;
        }
      });
      if (s == WsState.connected) {
        _kickBacklogPull();
      }
    });
    _ws!.start();
  }

  @override
  void dispose() {
    _sampleSub?.cancel();
    _stateSub?.cancel();
    _tickSub?.cancel();
    _pullTimer?.cancel();
    _ws?.dispose();
    _uploader?.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final rpm = _latest['obd.rpm'];
    final speed = _latest['obd.speed'];
    final coolant = _latest['obd.coolant_temp'];
    final throttle = _latest['obd.throttle'];
    final intake = _latest['obd.intake_air_temp'];
    final fuel = _latest['obd.fuel_level'];

    return Scaffold(
      appBar: AppBar(
        title: const Text('Knight Rider'),
        actions: [
          _StatusChip(state: _state),
          IconButton(
            icon: const Icon(Icons.settings),
            onPressed: _openSettings,
          ),
        ],
      ),
      body: Column(
        children: [
          if (_state != WsState.connected && _wsError != null)
            _DiagBanner(
              label: 'WS',
              uri: _wsUri,
              detail: _wsError!,
              color: Colors.red.shade900,
            ),
          if (_backlogPullError != null && _state == WsState.connected)
            _DiagBanner(
              label: 'BACKLOG',
              uri: 'http://$_host/backlog',
              detail: _backlogPullError!,
              color: Colors.orange.shade900,
            ),
          Expanded(
            child: SingleChildScrollView(
              padding: const EdgeInsets.all(12),
              child: Column(
                children: [
                  _Gauge(label: 'RPM', sample: rpm, big: true),
            const SizedBox(height: 12),
            Row(
              children: [
                Expanded(child: _Gauge(label: 'Speed', sample: speed)),
                const SizedBox(width: 12),
                Expanded(child: _Gauge(label: 'Throttle', sample: throttle)),
              ],
            ),
            const SizedBox(height: 12),
            Row(
              children: [
                Expanded(child: _Gauge(label: 'Coolant', sample: coolant)),
                const SizedBox(width: 12),
                Expanded(child: _Gauge(label: 'Intake Air', sample: intake)),
              ],
            ),
            const SizedBox(height: 12),
                  Row(
                    children: [
                      Expanded(child: _Gauge(label: 'Fuel', sample: fuel)),
                      const SizedBox(width: 12),
                      Expanded(
                        child: _BacklogCard(
                          total: _backlogTotal,
                          pending: _backlogPending,
                          uploaded: _backlogUploaded,
                          lastTick: _lastTick,
                        ),
                      ),
                    ],
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _DiagBanner extends StatelessWidget {
  final String label;
  final String? uri;
  final String detail;
  final Color color;

  const _DiagBanner({
    required this.label,
    required this.uri,
    required this.detail,
    required this.color,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      color: color,
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            '$label  ${uri ?? ''}',
            style: const TextStyle(
              fontSize: 10,
              fontWeight: FontWeight.bold,
              color: Colors.white70,
              letterSpacing: 1.2,
            ),
          ),
          Text(
            detail,
            maxLines: 3,
            overflow: TextOverflow.ellipsis,
            style: const TextStyle(fontSize: 11, color: Colors.white),
          ),
        ],
      ),
    );
  }
}

class _StatusChip extends StatelessWidget {
  final WsState state;
  const _StatusChip({required this.state});

  @override
  Widget build(BuildContext context) {
    final (label, color) = switch (state) {
      WsState.connected => ('LIVE', Colors.green),
      WsState.connecting => ('CONNECTING', Colors.orange),
      WsState.disconnected => ('OFFLINE', Colors.red),
    };
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 12),
      child: Chip(
        label: Text(label, style: const TextStyle(fontSize: 11)),
        backgroundColor: color.withValues(alpha: 0.2),
        side: BorderSide(color: color),
        padding: const EdgeInsets.symmetric(horizontal: 4),
      ),
    );
  }
}

class _Gauge extends StatelessWidget {
  final String label;
  final Sample? sample;
  final bool big;
  const _Gauge({required this.label, required this.sample, this.big = false});

  @override
  Widget build(BuildContext context) {
    final valueStr = sample == null ? '—' : sample!.value.toStringAsFixed(0);
    final unit = sample?.unit ?? '';
    final ts = sample?.ts;
    final valueStyle = TextStyle(
      fontSize: big ? 96 : 36,
      fontWeight: FontWeight.bold,
      fontFeatures: const [FontFeature.tabularFigures()],
    );
    return Card(
      elevation: 2,
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(label.toUpperCase(),
                style: const TextStyle(fontSize: 12, letterSpacing: 1.5)),
            const SizedBox(height: 4),
            Row(
              crossAxisAlignment: CrossAxisAlignment.baseline,
              textBaseline: TextBaseline.alphabetic,
              children: [
                Text(valueStr, style: valueStyle),
                const SizedBox(width: 6),
                Text(unit, style: const TextStyle(fontSize: 14, color: Colors.grey)),
              ],
            ),
            if (ts != null)
              Text(
                _ago(ts),
                style: const TextStyle(fontSize: 10, color: Colors.grey),
              ),
          ],
        ),
      ),
    );
  }

  String _ago(DateTime ts) {
    final ms = DateTime.now().difference(ts).inMilliseconds;
    if (ms < 1000) return '${ms}ms ago';
    return '${(ms / 1000).toStringAsFixed(1)}s ago';
  }
}

class _BacklogCard extends StatelessWidget {
  final int total;
  final int pending;
  final int uploaded;
  final UploadTick? lastTick;

  const _BacklogCard({
    required this.total,
    required this.pending,
    required this.uploaded,
    required this.lastTick,
  });

  @override
  Widget build(BuildContext context) {
    final tick = lastTick;
    final (iconColor, statusLine) = switch (tick) {
      null => (Colors.grey, 'idle'),
      UploadTick(success: true, attempted: 0) => (Colors.blueGrey, 'caught up'),
      UploadTick(success: true) => (Colors.green, 'last sync ${_ago(tick.at)}'),
      UploadTick(error: final e?) => (Colors.red, _shortError(e)),
      _ => (Colors.grey, 'idle'),
    };

    return Card(
      elevation: 2,
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                const Text('BACKLOG',
                    style: TextStyle(fontSize: 12, letterSpacing: 1.5)),
                const Spacer(),
                Icon(Icons.cloud_upload, size: 14, color: iconColor),
              ],
            ),
            const SizedBox(height: 4),
            Text('$uploaded / $total',
                style: const TextStyle(fontSize: 28, fontWeight: FontWeight.bold)),
            Text('$pending pending',
                style: const TextStyle(fontSize: 11, color: Colors.grey)),
            Text(statusLine,
                style: TextStyle(fontSize: 10, color: iconColor),
                overflow: TextOverflow.ellipsis),
          ],
        ),
      ),
    );
  }

  String _ago(DateTime ts) {
    final s = DateTime.now().difference(ts).inSeconds;
    if (s < 60) return '${s}s ago';
    final m = s ~/ 60;
    return '${m}m ago';
  }

  String _shortError(String e) {
    final t = e.length > 32 ? '${e.substring(0, 32)}…' : e;
    return 'err: $t';
  }
}

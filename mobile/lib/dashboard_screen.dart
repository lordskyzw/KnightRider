import 'dart:async';

import 'package:flutter/material.dart';

import 'backlog_db.dart';
import 'config.dart';
import 'pi_client.dart';
import 'sample.dart';
import 'settings_screen.dart';
import 'ws_client.dart';

class DashboardScreen extends StatefulWidget {
  const DashboardScreen({super.key});

  @override
  State<DashboardScreen> createState() => _DashboardScreenState();
}

class _DashboardScreenState extends State<DashboardScreen> {
  WsClient? _ws;
  String _host = PiConfig.defaultHost;
  WsState _state = WsState.disconnected;
  final Map<String, Sample> _latest = {};

  // Backlog state
  final _db = BacklogDb();
  int _backlogTotal = 0;
  int _backlogPending = 0;
  bool _pulling = false;
  Timer? _pullTimer;

  StreamSubscription<Sample>? _sampleSub;
  StreamSubscription<WsState>? _stateSub;

  @override
  void initState() {
    super.initState();
    _boot();
  }

  Future<void> _boot() async {
    _host = await PiConfig.host();
    _ws = WsClient(hostProvider: () => _host);
    _sampleSub = _ws!.stream.listen(_onSample);
    _stateSub = _ws!.stateStream.listen((s) {
      setState(() => _state = s);
      if (s == WsState.connected) {
        _kickBacklogPull();
      }
    });
    _ws!.start();
    await _refreshBacklogCounts();
    _pullTimer = Timer.periodic(const Duration(seconds: 30), (_) => _kickBacklogPull());
  }

  void _onSample(Sample s) {
    setState(() => _latest[s.signal] = s);
  }

  Future<void> _refreshBacklogCounts() async {
    final total = await _db.totalCount();
    final pending = await _db.pendingCount();
    if (!mounted) return;
    setState(() {
      _backlogTotal = total;
      _backlogPending = pending;
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
      // Will retry on next connect / timer tick.
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
      // Reconnect with new host.
      await _ws?.dispose();
      _ws = WsClient(hostProvider: () => _host);
      _sampleSub?.cancel();
      _stateSub?.cancel();
      _sampleSub = _ws!.stream.listen(_onSample);
      _stateSub = _ws!.stateStream.listen((s) {
        setState(() => _state = s);
        if (s == WsState.connected) _kickBacklogPull();
      });
      _ws!.start();
    }
  }

  @override
  void dispose() {
    _sampleSub?.cancel();
    _stateSub?.cancel();
    _pullTimer?.cancel();
    _ws?.dispose();
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
      body: SingleChildScrollView(
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
                  ),
                ),
              ],
            ),
          ],
        ),
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
  const _BacklogCard({required this.total, required this.pending});

  @override
  Widget build(BuildContext context) {
    return Card(
      elevation: 2,
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text('BACKLOG',
                style: TextStyle(fontSize: 12, letterSpacing: 1.5)),
            const SizedBox(height: 4),
            Text('$total', style: const TextStyle(fontSize: 36, fontWeight: FontWeight.bold)),
            Text('$pending pending', style: const TextStyle(fontSize: 11, color: Colors.grey)),
          ],
        ),
      ),
    );
  }
}

import 'package:flutter/material.dart';
import 'package:intl/intl.dart';

import 'app_theme.dart';
import 'drives_db.dart';

/// History of recorded drives: a list, tap for a per-drive detail rollup.
class DrivesScreen extends StatefulWidget {
  const DrivesScreen({super.key});

  @override
  State<DrivesScreen> createState() => _DrivesScreenState();
}

class _DrivesScreenState extends State<DrivesScreen> {
  final _db = DrivesDb();
  List<DriveRecord>? _rows;
  List<ActionRecord>? _actions;
  int _tab = 0; // 0 = Drives, 1 = Actions

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    await _db.ensureSeededHistory(); // backfill the 2026-06-01 Vitz reference drive
    await _db.ensureSeededActions(); // backfill the historical P0420 clear (Axio)
    final rows = await _db.recent();
    final acts = await _db.recentActions();
    if (!mounted) return;
    setState(() {
      _rows = rows;
      _actions = acts;
    });
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppPalette.bg,
      appBar: AppBar(
        backgroundColor: AppPalette.bg,
        foregroundColor: AppPalette.textHi,
        elevation: 0,
        title: const Text('History'),
      ),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 8, 16, 12),
            child: SegmentedButton<int>(
              segments: const [
                ButtonSegment(value: 0, label: Text('Drives'), icon: Icon(Icons.route_outlined)),
                ButtonSegment(value: 1, label: Text('Actions'), icon: Icon(Icons.bolt_outlined)),
              ],
              selected: {_tab},
              onSelectionChanged: (s) => setState(() => _tab = s.first),
            ),
          ),
          Expanded(child: _tab == 0 ? _buildDrives() : _buildActions()),
        ],
      ),
    );
  }

  Widget _buildDrives() {
    final rows = _rows;
    if (rows == null) return const Center(child: CircularProgressIndicator());
    if (rows.isEmpty) {
      return const _Empty(
        icon: Icons.route_outlined,
        title: 'No drives recorded yet',
        body: 'Connect to the car and drive — each session is saved here automatically.',
      );
    }
    return ListView.separated(
      padding: const EdgeInsets.symmetric(vertical: 4),
      itemCount: rows.length,
      separatorBuilder: (_, _) => const Divider(height: 1, color: AppPalette.divider),
      itemBuilder: (_, i) => _DriveTile(
        d: rows[i],
        onTap: () => Navigator.of(context).push(MaterialPageRoute(
          builder: (_) => _DriveDetail(d: rows[i]),
        )),
      ),
    );
  }

  Widget _buildActions() {
    final acts = _actions;
    if (acts == null) return const Center(child: CircularProgressIndicator());
    if (acts.isEmpty) {
      return const _Empty(
        icon: Icons.bolt_outlined,
        title: 'No actions yet',
        body: 'Write operations you perform on the car — like clearing fault codes — are logged here.',
      );
    }
    return ListView.separated(
      padding: const EdgeInsets.symmetric(vertical: 4),
      itemCount: acts.length,
      separatorBuilder: (_, _) => const Divider(height: 1, color: AppPalette.divider),
      itemBuilder: (_, i) => _ActionTile(a: acts[i]),
    );
  }
}

class _ActionTile extends StatelessWidget {
  final ActionRecord a;
  const _ActionTile({required this.a});

  @override
  Widget build(BuildContext context) {
    final color = a.ok ? AppPalette.live : AppPalette.warning;
    return ListTile(
      contentPadding: const EdgeInsets.symmetric(horizontal: 20, vertical: 6),
      leading: Icon(a.ok ? Icons.check_circle_outline : Icons.error_outline, color: color),
      title: Text(a.title,
          style: const TextStyle(color: AppPalette.textHi, fontWeight: FontWeight.w600)),
      subtitle: Padding(
        padding: const EdgeInsets.only(top: 4),
        child: Text(
          '${DateFormat('EEE d MMM, HH:mm').format(a.performedAt.toLocal())}'
          '  ·  ${a.vehicle}'
          '${a.detail != null ? '\n${a.detail}' : ''}',
          style: const TextStyle(color: AppPalette.textMid, fontSize: 13),
        ),
      ),
      isThreeLine: a.detail != null,
    );
  }
}

String _fmtDuration(Duration d) {
  final m = d.inMinutes, s = d.inSeconds % 60;
  if (m >= 60) return '${m ~/ 60}h ${m % 60}m';
  return m > 0 ? '${m}m ${s}s' : '${s}s';
}

class _DriveTile extends StatelessWidget {
  final DriveRecord d;
  final VoidCallback onTap;
  const _DriveTile({required this.d, required this.onTap});

  @override
  Widget build(BuildContext context) {
    final faulty = d.dtcCount > 0;
    return ListTile(
      onTap: onTap,
      contentPadding: const EdgeInsets.symmetric(horizontal: 20, vertical: 6),
      title: Text(
        DateFormat('EEE d MMM, HH:mm').format(d.startedAt.toLocal()),
        style: const TextStyle(color: AppPalette.textHi, fontWeight: FontWeight.w600),
      ),
      subtitle: Padding(
        padding: const EdgeInsets.only(top: 4),
        child: Text(
          '${d.vehicle}  ·  ${_fmtDuration(d.duration)}'
          '${d.maxSpeed != null ? '  ·  max ${d.maxSpeed!.round()} km/h' : ''}',
          style: const TextStyle(color: AppPalette.textMid, fontSize: 13),
        ),
      ),
      trailing: Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
        decoration: BoxDecoration(
          color: (faulty ? AppPalette.warning : AppPalette.live).withValues(alpha: 0.15),
          borderRadius: BorderRadius.circular(20),
        ),
        child: Text(
          faulty ? '${d.dtcCount} DTC' : 'OK',
          style: TextStyle(
            color: faulty ? AppPalette.warning : AppPalette.live,
            fontSize: 12, fontWeight: FontWeight.w700,
          ),
        ),
      ),
    );
  }
}

class _DriveDetail extends StatelessWidget {
  final DriveRecord d;
  const _DriveDetail({required this.d});

  @override
  Widget build(BuildContext context) {
    final stats = <(String, String)>[
      ('Vehicle', d.vehicle),
      ('Started', DateFormat('EEE d MMM yyyy, HH:mm:ss').format(d.startedAt.toLocal())),
      ('Duration', _fmtDuration(d.duration)),
      ('Samples', '${d.sampleCount}'),
      if (d.maxSpeed != null) ('Max speed', '${d.maxSpeed!.round()} km/h'),
      if (d.maxRpm != null) ('Max RPM', '${d.maxRpm!.round()}'),
      if (d.maxCoolant != null) ('Peak coolant', '${d.maxCoolant!.round()} °C'),
      if (d.o2upSwing != null)
        ('Upstream O₂ swing', '${d.o2upMin!.toStringAsFixed(2)} – ${d.o2upMax!.toStringAsFixed(2)} λ'),
      ('Stored faults', d.dtcCount > 0 ? '${d.dtcCount}' : 'none'),
    ];
    return Scaffold(
      backgroundColor: AppPalette.bg,
      appBar: AppBar(
        backgroundColor: AppPalette.bg,
        foregroundColor: AppPalette.textHi,
        elevation: 0,
        title: Text(DateFormat('d MMM, HH:mm').format(d.startedAt.toLocal())),
      ),
      body: ListView.separated(
        padding: const EdgeInsets.symmetric(vertical: 8),
        itemCount: stats.length,
        separatorBuilder: (_, _) =>
            const Divider(height: 1, color: AppPalette.divider),
        itemBuilder: (_, i) {
          final (label, value) = stats[i];
          return Padding(
            padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 14),
            child: Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Text(label, style: const TextStyle(color: AppPalette.textMid, fontSize: 14)),
                Text(value, style: const TextStyle(color: AppPalette.textHi, fontSize: 14, fontWeight: FontWeight.w600)),
              ],
            ),
          );
        },
      ),
    );
  }
}

class _Empty extends StatelessWidget {
  final IconData icon;
  final String title;
  final String body;
  const _Empty({required this.icon, required this.title, required this.body});
  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(40),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(icon, size: 48, color: AppPalette.textLow),
            const SizedBox(height: 16),
            Text(title,
                style: const TextStyle(color: AppPalette.textMid, fontSize: 16, fontWeight: FontWeight.w600)),
            const SizedBox(height: 8),
            Text(body,
                textAlign: TextAlign.center,
                style: const TextStyle(color: AppPalette.textLow, fontSize: 13)),
          ],
        ),
      ),
    );
  }
}

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

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    await _db.ensureSeededHistory(); // backfill the 2026-06-01 Vitz reference drive
    final rows = await _db.recent();
    if (!mounted) return;
    setState(() => _rows = rows);
  }

  @override
  Widget build(BuildContext context) {
    final rows = _rows;
    return Scaffold(
      backgroundColor: AppPalette.bg,
      appBar: AppBar(
        backgroundColor: AppPalette.bg,
        foregroundColor: AppPalette.textHi,
        elevation: 0,
        title: const Text('Drive history'),
      ),
      body: rows == null
          ? const Center(child: CircularProgressIndicator())
          : rows.isEmpty
              ? const _Empty()
              : ListView.separated(
                  padding: const EdgeInsets.symmetric(vertical: 8),
                  itemCount: rows.length,
                  separatorBuilder: (_, _) =>
                      const Divider(height: 1, color: AppPalette.divider),
                  itemBuilder: (_, i) => _DriveTile(
                    d: rows[i],
                    onTap: () => Navigator.of(context).push(MaterialPageRoute(
                      builder: (_) => _DriveDetail(d: rows[i]),
                    )),
                  ),
                ),
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
  const _Empty();
  @override
  Widget build(BuildContext context) {
    return const Center(
      child: Padding(
        padding: EdgeInsets.all(40),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.route_outlined, size: 48, color: AppPalette.textLow),
            SizedBox(height: 16),
            Text('No drives recorded yet',
                style: TextStyle(color: AppPalette.textMid, fontSize: 16, fontWeight: FontWeight.w600)),
            SizedBox(height: 8),
            Text('Connect to the car and drive — each session is saved here automatically.',
                textAlign: TextAlign.center,
                style: TextStyle(color: AppPalette.textLow, fontSize: 13)),
          ],
        ),
      ),
    );
  }
}

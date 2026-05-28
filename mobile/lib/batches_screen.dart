import 'package:flutter/material.dart';
import 'package:intl/intl.dart';

import 'backlog_db.dart';

class BatchesScreen extends StatefulWidget {
  final BatchFilter filter;
  const BatchesScreen({super.key, required this.filter});

  @override
  State<BatchesScreen> createState() => _BatchesScreenState();
}

class _BatchesScreenState extends State<BatchesScreen> {
  final _db = BacklogDb();
  List<BatchRecord>? _rows;
  int _total = 0;
  int _samples = 0;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final rows = await _db.recent(filter: widget.filter, limit: 500);
    if (!mounted) return;
    final samples = rows.fold<int>(0, (a, r) => a + r.sampleCount);
    setState(() {
      _rows = rows;
      _total = rows.length;
      _samples = samples;
    });
  }

  String get _title => switch (widget.filter) {
        BatchFilter.all      => 'Stored batches',
        BatchFilter.uploaded => 'Synced batches',
        BatchFilter.pending  => 'Pending upload',
      };

  Color get _accent => switch (widget.filter) {
        BatchFilter.all      => const Color(0xFFF0F0F2),
        BatchFilter.uploaded => const Color(0xFF34C759),
        BatchFilter.pending  => const Color(0xFFFF9F0A),
      };

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFF0A0A0B),
      appBar: AppBar(
        backgroundColor: const Color(0xFF0A0A0B),
        elevation: 0,
        title: Text(_title,
            style: const TextStyle(
              color: Color(0xFFF0F0F2),
              fontWeight: FontWeight.w500,
              fontSize: 17,
            )),
        iconTheme: const IconThemeData(color: Color(0xFFF0F0F2)),
      ),
      body: _rows == null
          ? const Center(
              child: CircularProgressIndicator(color: Color(0xFFF0F0F2)))
          : Column(
              children: [
                _Header(total: _total, samples: _samples, accent: _accent),
                Expanded(
                  child: RefreshIndicator(
                    onRefresh: _load,
                    color: _accent,
                    backgroundColor: const Color(0xFF16171A),
                    child: _rows!.isEmpty
                        ? _EmptyState(filter: widget.filter)
                        : ListView.separated(
                            padding: const EdgeInsets.symmetric(vertical: 4),
                            itemCount: _rows!.length,
                            separatorBuilder: (_, _) => const Divider(
                                height: 1, color: Color(0xFF2A2B2F)),
                            itemBuilder: (_, i) =>
                                _BatchTile(record: _rows![i]),
                          ),
                  ),
                ),
              ],
            ),
    );
  }
}

class _Header extends StatelessWidget {
  final int total;
  final int samples;
  final Color accent;
  const _Header({
    required this.total,
    required this.samples,
    required this.accent,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.fromLTRB(20, 4, 20, 16),
      child: Row(
        children: [
          _stat(total.toString(), 'BATCHES', accent),
          const SizedBox(width: 32),
          _stat(NumberFormat.compact().format(samples), 'SAMPLES',
              const Color(0xFFF0F0F2)),
        ],
      ),
    );
  }

  Widget _stat(String value, String label, Color color) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: [
        Text(value,
            style: TextStyle(
              fontSize: 32, color: color,
              fontWeight: FontWeight.w200, height: 1.0,
              fontFeatures: const [FontFeature.tabularFigures()],
            )),
        const SizedBox(height: 2),
        Text(label,
            style: const TextStyle(
              fontSize: 9, color: Color(0xFF8E8E92),
              letterSpacing: 2, fontWeight: FontWeight.w700,
            )),
      ],
    );
  }
}

class _EmptyState extends StatelessWidget {
  final BatchFilter filter;
  const _EmptyState({required this.filter});

  @override
  Widget build(BuildContext context) {
    final (icon, msg) = switch (filter) {
      BatchFilter.all => (
        Icons.storage,
        'No batches yet. The phone hasn\'t pulled anything from the Pi'
            ' — connect to the Pi LAN and wait for the next 30s tick.',
      ),
      BatchFilter.uploaded => (
        Icons.cloud_off,
        'Nothing uploaded yet. The courier runs every 10s when there\'s'
            ' something to send — if it stays at zero, check the cloud URL'
            ' in Settings.',
      ),
      BatchFilter.pending => (
        Icons.cloud_done,
        'All caught up. Every batch the phone has has been delivered to'
            ' the cloud.',
      ),
    };
    return ListView(
      children: [
        const SizedBox(height: 80),
        Icon(icon, size: 48, color: const Color(0xFF5A5A5E)),
        const SizedBox(height: 16),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 40),
          child: Text(msg,
              textAlign: TextAlign.center,
              style: const TextStyle(
                color: Color(0xFF8E8E92), fontSize: 13, height: 1.5,
              )),
        ),
      ],
    );
  }
}

class _BatchTile extends StatelessWidget {
  final BatchRecord record;
  const _BatchTile({required this.record});

  static final _timeFmt = DateFormat('HH:mm:ss');
  static final _dateFmt = DateFormat('MMM d');

  @override
  Widget build(BuildContext context) {
    final now = DateTime.now();
    final created = record.createdAt.toLocal();
    final showDate = !_sameDay(now, created);

    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 12),
      child: Row(
        children: [
          // Batch ID column
          SizedBox(
            width: 56,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('#${record.batchId}',
                    style: const TextStyle(
                      fontSize: 16, color: Color(0xFFF0F0F2),
                      fontWeight: FontWeight.w500,
                      fontFeatures: [FontFeature.tabularFigures()],
                    )),
                const SizedBox(height: 2),
                Text('${record.sampleCount} smp',
                    style: const TextStyle(
                      fontSize: 10, color: Color(0xFF8E8E92),
                    )),
              ],
            ),
          ),
          const SizedBox(width: 16),

          // Timestamps column
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    const Icon(Icons.adjust, size: 11, color: Color(0xFF8E8E92)),
                    const SizedBox(width: 5),
                    Text(_timeFmt.format(created),
                        style: const TextStyle(
                          fontSize: 13, color: Color(0xFFF0F0F2),
                          fontFamily: 'monospace',
                        )),
                    if (showDate) ...[
                      const SizedBox(width: 6),
                      Text(_dateFmt.format(created),
                          style: const TextStyle(
                            fontSize: 11, color: Color(0xFF8E8E92),
                          )),
                    ],
                  ],
                ),
                if (record.uploadedAt != null) ...[
                  const SizedBox(height: 4),
                  Row(
                    children: [
                      const Icon(Icons.cloud_upload, size: 11,
                          color: Color(0xFF34C759)),
                      const SizedBox(width: 5),
                      Text('synced ${_relative(record.uploadedAt!.toLocal())}',
                          style: const TextStyle(
                            fontSize: 11, color: Color(0xFF8E8E92),
                          )),
                    ],
                  ),
                ],
                const SizedBox(height: 4),
                Text('device ${record.deviceId.length >= 8
                        ? record.deviceId.substring(record.deviceId.length - 8)
                        : record.deviceId}'
                    ' · ${_compactSize(record.envelopeB64Length)}',
                    style: const TextStyle(
                      fontSize: 10, color: Color(0xFF5A5A5E),
                      fontFamily: 'monospace',
                    )),
              ],
            ),
          ),

          // Status pill
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
            decoration: BoxDecoration(
              color: (record.isUploaded
                      ? const Color(0xFF34C759)
                      : const Color(0xFFFF9F0A))
                  .withValues(alpha: 0.15),
              borderRadius: BorderRadius.circular(10),
              border: Border.all(
                color: (record.isUploaded
                        ? const Color(0xFF34C759)
                        : const Color(0xFFFF9F0A))
                    .withValues(alpha: 0.5),
                width: 1,
              ),
            ),
            child: Text(
              record.isUploaded ? 'SYNCED' : 'WAITING',
              style: TextStyle(
                fontSize: 9,
                color: record.isUploaded
                    ? const Color(0xFF34C759)
                    : const Color(0xFFFF9F0A),
                letterSpacing: 1.4, fontWeight: FontWeight.w700,
              ),
            ),
          ),
        ],
      ),
    );
  }

  bool _sameDay(DateTime a, DateTime b) =>
      a.year == b.year && a.month == b.month && a.day == b.day;

  String _compactSize(int b64Len) {
    // Decoded bytes are roughly len * 3 / 4.
    final bytes = b64Len * 3 ~/ 4;
    if (bytes < 1024) return '${bytes}B';
    if (bytes < 1024 * 1024) return '${(bytes / 1024).toStringAsFixed(1)}KB';
    return '${(bytes / 1024 / 1024).toStringAsFixed(1)}MB';
  }

  String _relative(DateTime when) {
    final delta = DateTime.now().difference(when);
    if (delta.inSeconds < 60) return '${delta.inSeconds}s ago';
    if (delta.inMinutes < 60) return '${delta.inMinutes}m ago';
    if (delta.inHours < 24) return '${delta.inHours}h ago';
    return '${delta.inDays}d ago';
  }
}

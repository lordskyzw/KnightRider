import 'package:flutter/material.dart';
import 'package:intl/intl.dart';

import 'dtc_codes.dart';
import 'pi_client.dart';
import 'sample.dart';

/// Detail screen for the dashboard's DTC counter. Lists every currently-
/// stored DTC (every sample with signal `dtc.stored.*`) plus any recently-
/// cleared codes (signal `dtc.cleared.*`) from the live sample feed.
class DtcScreen extends StatelessWidget {
  final Map<String, Sample> latest;
  /// Pi host:port — used to POST the Mode 0x04 clear request.
  final String host;
  const DtcScreen({super.key, required this.latest, required this.host});

  @override
  Widget build(BuildContext context) {
    final stored = latest.entries
        .where((e) => e.key.startsWith('dtc.stored.'))
        .map((e) => e.value)
        .toList()
      ..sort((a, b) => a.signal.compareTo(b.signal));

    final cleared = latest.entries
        .where((e) => e.key.startsWith('dtc.cleared.'))
        .map((e) => e.value)
        .toList()
      ..sort((a, b) => b.ts.compareTo(a.ts));

    final lastSweep = latest['obd.dtc_count'];

    return Scaffold(
      backgroundColor: const Color(0xFF0A0A0B),
      appBar: AppBar(
        backgroundColor: const Color(0xFF0A0A0B),
        elevation: 0,
        title: const Text('Diagnostic codes',
            style: TextStyle(
              color: Color(0xFFF0F0F2),
              fontWeight: FontWeight.w500,
              fontSize: 17,
            )),
        iconTheme: const IconThemeData(color: Color(0xFFF0F0F2)),
      ),
      body: ListView(
        padding: const EdgeInsets.all(0),
        children: [
          _SweepStatus(stored: stored.length, lastSweep: lastSweep),
          if (stored.isEmpty)
            const _AllClear()
          else ...[
            // Always non-empty in this branch (see `if (stored.isEmpty)` above).
            _SectionLabel('STORED · ${stored.length}',
                color: const Color(0xFFFF9F0A)),
            ...stored.map((s) => _DtcRow(sample: s, isStored: true)),
          ],
          if (cleared.isNotEmpty) ...[
            const SizedBox(height: 8),
            const _SectionLabel('RECENTLY CLEARED',
                color: Color(0xFF34C759)),
            ...cleared.take(10).map((s) =>
                _DtcRow(sample: s, isStored: false)),
          ],
          const SizedBox(height: 20),
          _ClearCodesButton(host: host),
          const SizedBox(height: 24),
        ],
      ),
    );
  }
}

/// Triggers OBD-II Mode 0x04 on the Pi. Confirms first (with the honest caveat
/// that the light returns if the fault is still present), shows progress, then
/// reports the ECU's answer. The Pi enforces the engine-off safety gate.
class _ClearCodesButton extends StatefulWidget {
  final String host;
  const _ClearCodesButton({required this.host});
  @override
  State<_ClearCodesButton> createState() => _ClearCodesButtonState();
}

class _ClearCodesButtonState extends State<_ClearCodesButton> {
  bool _busy = false;

  Future<void> _confirmAndClear() async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: const Color(0xFF16171A),
        title: const Text('Clear codes & reset light?',
            style: TextStyle(color: Color(0xFFF0F0F2), fontSize: 17)),
        content: const Text(
          'Sends OBD-II Mode 04 to the ECU: clears stored codes and turns off '
          'the check-engine light.\n\n'
          '• If the fault is still present, the light comes back after a drive cycle.\n'
          '• Emissions readiness monitors reset and stay "not ready" until you drive.\n'
          '• The engine must be off (key on) — the Pi will refuse otherwise.',
          style: TextStyle(color: Color(0xFF8E8E92), fontSize: 13, height: 1.5),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('Cancel',
                style: TextStyle(color: Color(0xFF8E8E92))),
          ),
          TextButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('Clear codes',
                style: TextStyle(
                    color: Color(0xFFFF9F0A), fontWeight: FontWeight.w700)),
          ),
        ],
      ),
    );
    if (ok != true || !mounted) return;

    setState(() => _busy = true);
    final client = PiClient(widget.host);
    ClearDtcResult res;
    try {
      res = await client.clearDtcs();
    } catch (e) {
      res = ClearDtcResult(ok: false, message: 'Could not reach the Pi ($e).');
    } finally {
      client.close();
    }
    if (!mounted) return;
    setState(() => _busy = false);
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
      backgroundColor:
          res.ok ? const Color(0xFF1B3A24) : const Color(0xFF3A2A16),
      duration: const Duration(seconds: 6),
      content: Row(
        children: [
          Icon(res.ok ? Icons.check_circle_outline : Icons.error_outline,
              color: res.ok ? const Color(0xFF34C759) : const Color(0xFFFF9F0A),
              size: 20),
          const SizedBox(width: 10),
          Expanded(
            child: Text(res.message,
                style: const TextStyle(color: Color(0xFFF0F0F2), fontSize: 12.5)),
          ),
        ],
      ),
    ));
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 16),
      child: SizedBox(
        width: double.infinity,
        child: OutlinedButton.icon(
          onPressed: _busy ? null : _confirmAndClear,
          style: OutlinedButton.styleFrom(
            foregroundColor: const Color(0xFFFF9F0A),
            side: const BorderSide(color: Color(0xFF3A2A16)),
            padding: const EdgeInsets.symmetric(vertical: 14),
            shape: RoundedRectangleBorder(
                borderRadius: BorderRadius.circular(12)),
          ),
          icon: _busy
              ? const SizedBox(
                  width: 16, height: 16,
                  child: CircularProgressIndicator(
                      strokeWidth: 2, color: Color(0xFFFF9F0A)))
              : const Icon(Icons.cleaning_services_outlined, size: 18),
          label: Text(_busy ? 'Clearing…' : 'Clear codes & reset light',
              style: const TextStyle(
                  fontSize: 13.5, fontWeight: FontWeight.w600)),
        ),
      ),
    );
  }
}

class _SweepStatus extends StatelessWidget {
  final int stored;
  final Sample? lastSweep;
  const _SweepStatus({required this.stored, required this.lastSweep});

  @override
  Widget build(BuildContext context) {
    final color = stored == 0
        ? const Color(0xFF34C759)
        : const Color(0xFFFF9F0A);
    final label = lastSweep == null
        ? 'No sweep yet'
        : 'Last sweep ${_relative(lastSweep!.ts.toLocal())}';
    return Container(
      padding: const EdgeInsets.fromLTRB(20, 12, 20, 20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.baseline,
            textBaseline: TextBaseline.alphabetic,
            children: [
              Text(stored.toString(),
                  style: TextStyle(
                    fontSize: 56, color: color,
                    fontWeight: FontWeight.w200, height: 1.0,
                    fontFeatures: const [FontFeature.tabularFigures()],
                  )),
              const SizedBox(width: 10),
              Text(stored == 1 ? 'code' : 'codes',
                  style: const TextStyle(
                    fontSize: 14, color: Color(0xFF8E8E92),
                  )),
            ],
          ),
          const SizedBox(height: 4),
          Text(label,
              style: const TextStyle(
                fontSize: 11, color: Color(0xFF5A5A5E),
              )),
          const SizedBox(height: 12),
          const Text(
            'Mode 03 stored-code sweep runs every 30 seconds on the Pi.\n'
            'This screen reflects whatever the most recent sweep returned.',
            style: TextStyle(
              fontSize: 12, color: Color(0xFF8E8E92), height: 1.5,
            ),
          ),
        ],
      ),
    );
  }
}

class _AllClear extends StatelessWidget {
  const _AllClear();
  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.symmetric(horizontal: 20),
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: const Color(0xFF34C759).withValues(alpha: 0.08),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(
          color: const Color(0xFF34C759).withValues(alpha: 0.3),
        ),
      ),
      child: const Row(
        children: [
          Icon(Icons.check_circle_outline, color: Color(0xFF34C759), size: 28),
          SizedBox(width: 12),
          Expanded(
            child: Text(
              'No stored codes. The ECU has nothing to report.',
              style: TextStyle(color: Color(0xFFF0F0F2), fontSize: 13),
            ),
          ),
        ],
      ),
    );
  }
}

class _SectionLabel extends StatelessWidget {
  final String text;
  final Color color;
  const _SectionLabel(this.text, {required this.color});
  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(20, 12, 20, 8),
      child: Text(text,
          style: TextStyle(
            color: color, fontSize: 10,
            letterSpacing: 2.0, fontWeight: FontWeight.w700,
          )),
    );
  }
}

class _DtcRow extends StatelessWidget {
  final Sample sample;
  final bool isStored;
  const _DtcRow({required this.sample, required this.isStored});

  @override
  Widget build(BuildContext context) {
    final code = sample.signal.split('.').last.toUpperCase();
    final desc = describeDtc(sample.signal);
    final severity = dtcSeverity(sample.signal);
    final color = isStored ? _severityColor(severity) : const Color(0xFF34C759);

    return Container(
      margin: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: const Color(0xFF16171A),
        borderRadius: BorderRadius.circular(10),
        border: Border(left: BorderSide(color: color, width: 3)),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Text(code,
                        style: TextStyle(
                          fontSize: 18, color: color,
                          fontWeight: FontWeight.w600,
                          fontFamily: 'monospace',
                          letterSpacing: 0.5,
                        )),
                    if (isStored) ...[
                      const SizedBox(width: 8),
                      _severityChip(severity),
                    ],
                  ],
                ),
                const SizedBox(height: 4),
                Text(
                  desc ?? '(no description in lookup table)',
                  style: TextStyle(
                    fontSize: 13,
                    color: desc == null
                        ? const Color(0xFF5A5A5E)
                        : const Color(0xFFF0F0F2),
                    fontStyle: desc == null
                        ? FontStyle.italic
                        : FontStyle.normal,
                    height: 1.35,
                  ),
                ),
                const SizedBox(height: 6),
                Text(_relative(sample.ts.toLocal()),
                    style: const TextStyle(
                      fontSize: 10, color: Color(0xFF5A5A5E),
                    )),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _severityChip(DtcSeverity s) {
    final (label, color) = switch (s) {
      DtcSeverity.high    => ('HIGH',   const Color(0xFFC8102E)),
      DtcSeverity.medium  => ('MED',    const Color(0xFFFF9F0A)),
      DtcSeverity.low     => ('LOW',    const Color(0xFF8E8E92)),
      DtcSeverity.unknown => ('?',      const Color(0xFF5A5A5E)),
    };
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 2),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.15),
        borderRadius: BorderRadius.circular(3),
      ),
      child: Text(label,
          style: TextStyle(
            color: color, fontSize: 8.5,
            letterSpacing: 1.4, fontWeight: FontWeight.w700,
          )),
    );
  }

  Color _severityColor(DtcSeverity s) => switch (s) {
        DtcSeverity.high    => const Color(0xFFC8102E),
        DtcSeverity.medium  => const Color(0xFFFF9F0A),
        DtcSeverity.low     => const Color(0xFF8E8E92),
        DtcSeverity.unknown => const Color(0xFF5A5A5E),
      };
}

String _relative(DateTime when) {
  final delta = DateTime.now().difference(when);
  if (delta.inSeconds < 10) return 'just now';
  if (delta.inSeconds < 60) return '${delta.inSeconds}s ago';
  if (delta.inMinutes < 60) return '${delta.inMinutes}m ago';
  if (delta.inHours < 24) return '${delta.inHours}h ago';
  return DateFormat('MMM d HH:mm').format(when);
}

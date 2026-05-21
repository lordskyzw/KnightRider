import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;

import 'backlog_db.dart';

/// Result of a single uploader tick, surfaced to the UI for the heartbeat.
class UploadTick {
  final DateTime at;
  final int attempted;
  final int accepted;
  final int duplicates;
  final String? error;

  const UploadTick({
    required this.at,
    required this.attempted,
    required this.accepted,
    required this.duplicates,
    this.error,
  });

  bool get success => error == null;
}

/// Periodic courier: drains `BacklogDb.unuploaded()` and POSTs to the cloud's
/// `/v1/batches`. No connectivity detection — failures bounce, the cloud's
/// idempotency makes retries free.
class Uploader {
  final BacklogDb db;
  final String Function() cloudUrlProvider;
  final Duration tickInterval;
  final int chunkSize;

  Timer? _timer;
  bool _inFlight = false;
  bool _disposed = false;
  final _ticks = StreamController<UploadTick>.broadcast();

  Uploader({
    required this.db,
    required this.cloudUrlProvider,
    this.tickInterval = const Duration(seconds: 10),
    this.chunkSize = 100,
  });

  Stream<UploadTick> get ticks => _ticks.stream;

  void start() {
    _disposed = false;
    // Fire once immediately, then on the interval.
    scheduleMicrotask(_runTick);
    _timer = Timer.periodic(tickInterval, (_) => _runTick());
  }

  Future<void> _runTick() async {
    if (_disposed || _inFlight) return;
    _inFlight = true;
    try {
      final rows = await db.unuploaded(limit: chunkSize);
      if (rows.isEmpty) {
        _emit(UploadTick(
          at: DateTime.now(),
          attempted: 0,
          accepted: 0,
          duplicates: 0,
        ));
        return;
      }

      final body = jsonEncode(rows.map((r) => r.toUploadJson()).toList());
      final url = Uri.parse('${cloudUrlProvider()}/v1/batches');
      final resp = await http
          .post(url,
              headers: const {'content-type': 'application/json'}, body: body)
          .timeout(const Duration(seconds: 30));

      if (resp.statusCode != 200) {
        _emit(UploadTick(
          at: DateTime.now(),
          attempted: rows.length,
          accepted: 0,
          duplicates: 0,
          error: 'http ${resp.statusCode}',
        ));
        return;
      }

      final parsed = jsonDecode(resp.body) as Map<String, dynamic>;
      final accepted = (parsed['accepted'] as num?)?.toInt() ?? 0;
      final duplicates = (parsed['duplicates'] as num?)?.toInt() ?? 0;

      // Mark every row in this chunk as uploaded — whether the cloud took it
      // fresh or recognized it as a dup, it's durably stored cloud-side and
      // we shouldn't re-send.
      await db.markManyUploaded(rows.map((r) => r.batchId), DateTime.now());

      _emit(UploadTick(
        at: DateTime.now(),
        attempted: rows.length,
        accepted: accepted,
        duplicates: duplicates,
      ));
    } catch (e) {
      _emit(UploadTick(
        at: DateTime.now(),
        attempted: 0,
        accepted: 0,
        duplicates: 0,
        error: e.toString(),
      ));
    } finally {
      _inFlight = false;
    }
  }

  void _emit(UploadTick tick) {
    if (!_ticks.isClosed) _ticks.add(tick);
  }

  Future<void> dispose() async {
    _disposed = true;
    _timer?.cancel();
    await _ticks.close();
  }
}

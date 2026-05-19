import 'dart:convert';

import 'package:http/http.dart' as http;

/// One row from `/backlog?since=N` — opaque to the courier; `envelopeB64` is
/// forwarded to the cloud byte-for-byte so Pi signatures (when they ship)
/// stay intact end-to-end.
class BacklogRow {
  final int batchId;
  final DateTime createdAt;
  final int sampleCount;
  final String envelopeB64;

  BacklogRow({
    required this.batchId,
    required this.createdAt,
    required this.sampleCount,
    required this.envelopeB64,
  });

  factory BacklogRow.fromJson(Map<String, dynamic> j) => BacklogRow(
        batchId: j['batch_id'] as int,
        createdAt: DateTime.parse(j['created_at'] as String),
        sampleCount: j['sample_count'] as int,
        envelopeB64: j['envelope_b64'] as String,
      );
}

class PiHealth {
  final String deviceId;
  final int nextBatchId;
  final int pendingBatches;

  PiHealth({
    required this.deviceId,
    required this.nextBatchId,
    required this.pendingBatches,
  });

  factory PiHealth.fromJson(Map<String, dynamic> j) => PiHealth(
        deviceId: j['device_id'] as String,
        nextBatchId: j['next_batch_id'] as int,
        pendingBatches: j['pending_batches'] as int,
      );
}

class PiClient {
  final String host;
  final http.Client _http;

  PiClient(this.host, {http.Client? client}) : _http = client ?? http.Client();

  Uri _u(String path, [Map<String, String>? q]) =>
      Uri.parse('http://$host$path').replace(queryParameters: q);

  Future<PiHealth> health() async {
    final r = await _http.get(_u('/health')).timeout(const Duration(seconds: 5));
    if (r.statusCode != 200) {
      throw HttpException('health ${r.statusCode}');
    }
    return PiHealth.fromJson(jsonDecode(r.body) as Map<String, dynamic>);
  }

  /// Pulls backlog rows with `batch_id > since`. Returns rows in ascending
  /// batch_id order.
  Future<List<BacklogRow>> backlog({required int since, int limit = 500}) async {
    final r = await _http
        .get(_u('/backlog', {'since': '$since', 'limit': '$limit'}))
        .timeout(const Duration(seconds: 15));
    if (r.statusCode != 200) {
      throw HttpException('backlog ${r.statusCode}');
    }
    final out = <BacklogRow>[];
    for (final line in const LineSplitter().convert(r.body)) {
      if (line.trim().isEmpty) continue;
      out.add(BacklogRow.fromJson(jsonDecode(line) as Map<String, dynamic>));
    }
    return out;
  }

  void close() => _http.close();
}

class HttpException implements Exception {
  final String message;
  HttpException(this.message);
  @override
  String toString() => 'HttpException: $message';
}

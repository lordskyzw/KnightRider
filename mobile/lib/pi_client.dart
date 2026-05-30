import 'dart:convert';

import 'package:http/http.dart' as http;

/// One row from `/backlog?since=N` — opaque to the courier; `envelopeB64` is
/// forwarded to the cloud byte-for-byte so Pi signatures (when they ship)
/// stay intact end-to-end.
class BacklogRow {
  final int batchId;
  final String deviceId;
  final DateTime createdAt;
  final int sampleCount;
  final String envelopeB64;

  BacklogRow({
    required this.batchId,
    required this.deviceId,
    required this.createdAt,
    required this.sampleCount,
    required this.envelopeB64,
  });

  factory BacklogRow.fromJson(Map<String, dynamic> j) => BacklogRow(
        batchId: j['batch_id'] as int,
        deviceId: j['device_id'] as String,
        createdAt: DateTime.parse(j['created_at'] as String),
        sampleCount: j['sample_count'] as int,
        envelopeB64: j['envelope_b64'] as String,
      );

  /// Shape expected by the cloud's `POST /v1/batches`.
  Map<String, dynamic> toUploadJson() => {
        'batch_id': batchId,
        'device_id': deviceId,
        'created_at': createdAt.toIso8601String(),
        'sample_count': sampleCount,
        'envelope_b64': envelopeB64,
      };
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

  PiClient(String host, {http.Client? client})
      : host = _normalizeHost(host),
        _http = client ?? http.Client();

  /// Strips http://, https://, ws://, wss:// prefixes, trailing slashes, and
  /// whitespace from the host string.
  static String _normalizeHost(String raw) {
    var h = raw.trim();
    for (final scheme in const ['https://', 'http://', 'wss://', 'ws://']) {
      if (h.toLowerCase().startsWith(scheme)) {
        h = h.substring(scheme.length);
        break;
      }
    }
    while (h.endsWith('/')) {
      h = h.substring(0, h.length - 1);
    }
    return h;
  }

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

  /// `POST /clear-dtc` — asks the Pi to run OBD-II Mode 0x04 (clear codes +
  /// reset the check-engine light). The Pi gates this on the engine being off
  /// and only confirms once the ECU acknowledges. Returns the outcome; never
  /// throws on a clean refusal (engine running / ECU declined) — that comes
  /// back as `ClearDtcResult(ok: false, ...)`.
  Future<ClearDtcResult> clearDtcs() async {
    final r = await _http
        .post(_u('/clear-dtc'))
        .timeout(const Duration(seconds: 8));
    String message = '';
    try {
      final j = jsonDecode(r.body) as Map<String, dynamic>;
      message = (j['message'] as String?) ?? '';
    } catch (_) {
      message = 'Unexpected response (${r.statusCode}).';
    }
    return ClearDtcResult(ok: r.statusCode == 200, message: message);
  }

  void close() => _http.close();
}

/// Outcome of a clear-DTC request.
class ClearDtcResult {
  final bool ok;
  final String message;
  ClearDtcResult({required this.ok, required this.message});
}

class HttpException implements Exception {
  final String message;
  HttpException(this.message);
  @override
  String toString() => 'HttpException: $message';
}

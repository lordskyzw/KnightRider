/// Live sample pushed over `/ws/live`. Mirrors the Rust `Sample` type.
class Sample {
  final DateTime ts;
  final String source; // "obd_poller" | "sniffer"
  final String signal; // canonical name, e.g. "obd.rpm"
  final double value;
  final String unit;

  Sample({
    required this.ts,
    required this.source,
    required this.signal,
    required this.value,
    required this.unit,
  });

  factory Sample.fromJson(Map<String, dynamic> j) => Sample(
        ts: DateTime.parse(j['ts'] as String),
        source: j['source'] as String,
        signal: j['signal'] as String,
        value: (j['value'] as num).toDouble(),
        unit: j['unit'] as String,
      );
}

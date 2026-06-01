import 'package:path/path.dart' as p;
import 'package:path_provider/path_provider.dart';
import 'package:sqflite/sqflite.dart';

/// Local store of recorded drives.
///
/// A "drive" is owned by the app, NOT the Pi: the recorder opens one when live
/// telemetry starts flowing and closes it after a telemetry gap (disconnect /
/// parked-and-off). This is deliberately independent of the Pi's `batch_id`,
/// which resets to 1 every time the buffer is rolled and so cannot identify a
/// session. v1 stores a per-drive rollup; the raw series for replay can be
/// added later.
class DrivesDb {
  static const _schemaVersion = 2;
  Database? _db;

  Future<Database> _open() async {
    if (_db != null) return _db!;
    final dir = await getApplicationDocumentsDirectory();
    final path = p.join(dir.path, 'knight_rider_drives.db');
    _db = await openDatabase(
      path,
      version: _schemaVersion,
      onCreate: (db, _) async {
        await _createDrives(db);
        await _createActions(db);
      },
      onUpgrade: (db, oldV, newV) async {
        if (oldV < 2) await _createActions(db); // actions added in v2
      },
    );
    return _db!;
  }

  static Future<void> _createDrives(Database db) async {
    await db.execute('''
      CREATE TABLE drives (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        vehicle      TEXT NOT NULL,
        started_at   TEXT NOT NULL,
        ended_at     TEXT NOT NULL,
        sample_count INTEGER NOT NULL,
        max_speed    REAL,
        max_rpm      REAL,
        max_coolant  REAL,
        o2up_min     REAL,
        o2up_max     REAL,
        dtc_count    INTEGER
      )
    ''');
  }

  static Future<void> _createActions(Database db) async {
    await db.execute('''
      CREATE TABLE actions (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        type         TEXT NOT NULL,
        vehicle      TEXT NOT NULL,
        performed_at TEXT NOT NULL,
        ok           INTEGER NOT NULL,
        detail       TEXT
      )
    ''');
  }

  // ── actions (write operations performed on the car) ────────────────────────
  Future<int> insertAction(ActionRecord a) async {
    final db = await _open();
    return db.insert('actions', a.toMap());
  }

  Future<List<ActionRecord>> recentActions({int limit = 200}) async {
    final db = await _open();
    final rows =
        await db.query('actions', orderBy: 'performed_at DESC', limit: limit);
    return rows.map(ActionRecord.fromMap).toList();
  }

  Future<int> insertDrive(DriveRecord d) async {
    final db = await _open();
    return db.insert('drives', d.toMap());
  }

  /// One-time backfill of the 2026-06-01 Vitz reference drive. That session was
  /// captured before the recorder existed, so it can't be recorded live; these
  /// are its real decoded stats (see captures/vitz-drive-20260601.summary.md).
  /// Idempotent: keyed on started_at, so it inserts at most once.
  Future<void> ensureSeededHistory() async {
    final db = await _open();
    const started = '2026-06-01T08:04:25.000Z';
    final existing = await db.query('drives',
        where: 'started_at = ?', whereArgs: [started], limit: 1);
    if (existing.isNotEmpty) return;
    await db.insert('drives', DriveRecord(
      vehicle: 'Toyota Vitz (NSP130)',
      startedAt: DateTime.parse(started),
      endedAt: DateTime.parse('2026-06-01T08:28:04.000Z'),
      sampleCount: 489193,
      maxSpeed: 70, maxRpm: 3362, maxCoolant: 94,
      o2upMin: 0.814, o2upMax: 1.233, dtcCount: 0,
    ).toMap());
  }

  /// One-time backfill of the 2026-06-01 P0420 clear on the Vitz, performed
  /// before this action log existed. Timestamp + outcome are the real values
  /// from the Pi journal (clear OK at 07:54:30Z; stored DTC set went
  /// ["P0420"] -> [] by 07:55:07Z). Idempotent on performed_at.
  Future<void> ensureSeededActions() async {
    final db = await _open();
    const at = '2026-06-01T07:54:30.000Z';
    final existing = await db.query('actions',
        where: 'performed_at = ?', whereArgs: [at], limit: 1);
    if (existing.isNotEmpty) return;
    await db.insert('actions', ActionRecord(
      type: 'clear_dtc',
      vehicle: 'Toyota Vitz (NSP130)',
      performedAt: DateTime.parse(at),
      ok: true,
      detail: 'Cleared P0420; check-engine light reset (readiness monitors reset)',
    ).toMap());
  }

  Future<List<DriveRecord>> recent({int limit = 200}) async {
    final db = await _open();
    final rows = await db.query('drives', orderBy: 'started_at DESC', limit: limit);
    return rows.map(DriveRecord.fromMap).toList();
  }

  Future<void> delete(int id) async {
    final db = await _open();
    await db.delete('drives', where: 'id = ?', whereArgs: [id]);
  }
}

class DriveRecord {
  final int? id;
  final String vehicle;
  final DateTime startedAt;
  final DateTime endedAt;
  final int sampleCount;
  final double? maxSpeed;
  final double? maxRpm;
  final double? maxCoolant;
  final double? o2upMin;
  final double? o2upMax;
  final int dtcCount;

  DriveRecord({
    this.id,
    required this.vehicle,
    required this.startedAt,
    required this.endedAt,
    required this.sampleCount,
    this.maxSpeed,
    this.maxRpm,
    this.maxCoolant,
    this.o2upMin,
    this.o2upMax,
    this.dtcCount = 0,
  });

  Duration get duration => endedAt.difference(startedAt);

  /// On a healthy catalyst the upstream O2 (equivalence ratio) swings widely in
  /// closed loop; a wide observed range across the drive is a quick liveness cue.
  double? get o2upSwing =>
      (o2upMin != null && o2upMax != null) ? (o2upMax! - o2upMin!) : null;

  Map<String, Object?> toMap() => {
        'vehicle': vehicle,
        'started_at': startedAt.toIso8601String(),
        'ended_at': endedAt.toIso8601String(),
        'sample_count': sampleCount,
        'max_speed': maxSpeed,
        'max_rpm': maxRpm,
        'max_coolant': maxCoolant,
        'o2up_min': o2upMin,
        'o2up_max': o2upMax,
        'dtc_count': dtcCount,
      };

  static DriveRecord fromMap(Map<String, Object?> m) => DriveRecord(
        id: m['id'] as int?,
        vehicle: m['vehicle'] as String,
        startedAt: DateTime.parse(m['started_at'] as String),
        endedAt: DateTime.parse(m['ended_at'] as String),
        sampleCount: m['sample_count'] as int,
        maxSpeed: (m['max_speed'] as num?)?.toDouble(),
        maxRpm: (m['max_rpm'] as num?)?.toDouble(),
        maxCoolant: (m['max_coolant'] as num?)?.toDouble(),
        o2upMin: (m['o2up_min'] as num?)?.toDouble(),
        o2upMax: (m['o2up_max'] as num?)?.toDouble(),
        dtcCount: (m['dtc_count'] as int?) ?? 0,
      );
}

/// A write action performed on the car (the "leave no action behind" log).
/// v1 records DTC clears (OBD-II Mode 0x04); the schema is generic for future
/// write ops.
class ActionRecord {
  final int? id;
  final String type;      // 'clear_dtc'
  final String vehicle;
  final DateTime performedAt;
  final bool ok;
  final String? detail;   // e.g. 'Cleared P0420, P0171; MIL reset'

  ActionRecord({
    this.id,
    required this.type,
    required this.vehicle,
    required this.performedAt,
    required this.ok,
    this.detail,
  });

  String get title => switch (type) {
        'clear_dtc' => 'Cleared fault codes',
        _ => type,
      };

  Map<String, Object?> toMap() => {
        'type': type,
        'vehicle': vehicle,
        'performed_at': performedAt.toIso8601String(),
        'ok': ok ? 1 : 0,
        'detail': detail,
      };

  static ActionRecord fromMap(Map<String, Object?> m) => ActionRecord(
        id: m['id'] as int?,
        type: m['type'] as String,
        vehicle: m['vehicle'] as String,
        performedAt: DateTime.parse(m['performed_at'] as String),
        ok: (m['ok'] as int) == 1,
        detail: m['detail'] as String?,
      );
}

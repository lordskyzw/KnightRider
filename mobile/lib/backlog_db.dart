import 'package:path/path.dart' as p;
import 'package:path_provider/path_provider.dart';
import 'package:sqflite/sqflite.dart';

import 'pi_client.dart';

/// Local store of Pi-built batches awaiting upload to cloud.
///
/// Schema v2:
///   batches(batch_id PK, device_id, created_at, sample_count, envelope_b64,
///           uploaded_at)
///
/// `device_id` is needed for the cloud's idempotency key `(device_id, batch_id)`.
class BacklogDb {
  static const _schemaVersion = 2;

  Database? _db;

  Future<Database> _open() async {
    if (_db != null) return _db!;
    final dir = await getApplicationDocumentsDirectory();
    final path = p.join(dir.path, 'knight_rider_backlog.db');
    _db = await openDatabase(
      path,
      version: _schemaVersion,
      onCreate: (db, _) async {
        await _createSchema(db);
      },
      onUpgrade: (db, oldV, newV) async {
        if (oldV < 2) {
          // v1 rows had no device_id and can't be uploaded; drop and
          // rebuild. Demo-phase only — no production data to preserve.
          await db.execute('DROP TABLE IF EXISTS batches');
          await _createSchema(db);
        }
      },
    );
    return _db!;
  }

  static Future<void> _createSchema(Database db) async {
    await db.execute('''
      CREATE TABLE batches (
        batch_id     INTEGER PRIMARY KEY,
        device_id    TEXT NOT NULL,
        created_at   TEXT NOT NULL,
        sample_count INTEGER NOT NULL,
        envelope_b64 TEXT NOT NULL,
        uploaded_at  TEXT
      )
    ''');
    await db.execute(
      'CREATE INDEX idx_pending ON batches(batch_id) WHERE uploaded_at IS NULL',
    );
  }

  /// Inserts a batch if not already present. Returns true if new.
  Future<bool> upsert(BacklogRow row) async {
    final db = await _open();
    final n = await db.insert(
      'batches',
      {
        'batch_id': row.batchId,
        'device_id': row.deviceId,
        'created_at': row.createdAt.toIso8601String(),
        'sample_count': row.sampleCount,
        'envelope_b64': row.envelopeB64,
      },
      conflictAlgorithm: ConflictAlgorithm.ignore,
    );
    return n != 0;
  }

  Future<int> highestBatchId() async {
    final db = await _open();
    final rows = await db.rawQuery('SELECT MAX(batch_id) AS m FROM batches');
    return (rows.first['m'] as int?) ?? 0;
  }

  Future<int> pendingCount() async {
    final db = await _open();
    final rows = await db.rawQuery(
      'SELECT COUNT(*) AS n FROM batches WHERE uploaded_at IS NULL',
    );
    return (rows.first['n'] as int?) ?? 0;
  }

  Future<int> totalCount() async {
    final db = await _open();
    final rows = await db.rawQuery('SELECT COUNT(*) AS n FROM batches');
    return (rows.first['n'] as int?) ?? 0;
  }

  Future<int> uploadedCount() async {
    final db = await _open();
    final rows = await db.rawQuery(
      'SELECT COUNT(*) AS n FROM batches WHERE uploaded_at IS NOT NULL',
    );
    return (rows.first['n'] as int?) ?? 0;
  }

  /// Returns up to [limit] rows that haven't been uploaded yet, oldest
  /// first. The uploader drains these into the cloud.
  Future<List<BacklogRow>> unuploaded({int limit = 100}) async {
    final db = await _open();
    final rows = await db.query(
      'batches',
      where: 'uploaded_at IS NULL',
      orderBy: 'batch_id ASC',
      limit: limit,
    );
    return rows
        .map((r) => BacklogRow(
              batchId: r['batch_id'] as int,
              deviceId: r['device_id'] as String,
              createdAt: DateTime.parse(r['created_at'] as String),
              sampleCount: r['sample_count'] as int,
              envelopeB64: r['envelope_b64'] as String,
            ))
        .toList();
  }

  Future<void> markUploaded(int batchId, DateTime when) async {
    final db = await _open();
    await db.update(
      'batches',
      {'uploaded_at': when.toIso8601String()},
      where: 'batch_id = ?',
      whereArgs: [batchId],
    );
  }

  Future<void> markManyUploaded(Iterable<int> batchIds, DateTime when) async {
    final db = await _open();
    final ts = when.toIso8601String();
    await db.transaction((txn) async {
      for (final id in batchIds) {
        await txn.update(
          'batches',
          {'uploaded_at': ts},
          where: 'batch_id = ?',
          whereArgs: [id],
        );
      }
    });
  }
}

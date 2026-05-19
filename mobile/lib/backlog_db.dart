import 'package:path/path.dart' as p;
import 'package:path_provider/path_provider.dart';
import 'package:sqflite/sqflite.dart';

import 'pi_client.dart';

/// Local store of Pi-built batches awaiting upload to cloud.
///
/// Schema:
///   batches(batch_id PK, created_at, sample_count, envelope_b64, uploaded_at)
class BacklogDb {
  Database? _db;

  Future<Database> _open() async {
    if (_db != null) return _db!;
    final dir = await getApplicationDocumentsDirectory();
    final path = p.join(dir.path, 'knight_rider_backlog.db');
    _db = await openDatabase(
      path,
      version: 1,
      onCreate: (db, _) async {
        await db.execute('''
          CREATE TABLE batches (
            batch_id     INTEGER PRIMARY KEY,
            created_at   TEXT NOT NULL,
            sample_count INTEGER NOT NULL,
            envelope_b64 TEXT NOT NULL,
            uploaded_at  TEXT
          )
        ''');
        await db.execute(
          'CREATE INDEX idx_pending ON batches(batch_id) WHERE uploaded_at IS NULL',
        );
      },
    );
    return _db!;
  }

  /// Inserts a batch if not already present. Returns true if new.
  Future<bool> upsert(BacklogRow row) async {
    final db = await _open();
    final n = await db.insert(
      'batches',
      {
        'batch_id': row.batchId,
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

  Future<void> markUploaded(int batchId, DateTime when) async {
    final db = await _open();
    await db.update(
      'batches',
      {'uploaded_at': when.toIso8601String()},
      where: 'batch_id = ?',
      whereArgs: [batchId],
    );
  }
}

import 'package:shared_preferences/shared_preferences.dart';

/// Persistent Pi-server configuration. Open-LAN demo: just host:port.
class PiConfig {
  static const _kHost = 'pi_host';
  static const _kBacklogCursor = 'backlog_cursor';

  static const defaultHost = 'raspberrypi.local:8080';

  static Future<String> host() async {
    final prefs = await SharedPreferences.getInstance();
    return prefs.getString(_kHost) ?? defaultHost;
  }

  static Future<void> setHost(String value) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_kHost, value);
  }

  static Future<int> backlogCursor() async {
    final prefs = await SharedPreferences.getInstance();
    return prefs.getInt(_kBacklogCursor) ?? 0;
  }

  static Future<void> setBacklogCursor(int batchId) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setInt(_kBacklogCursor, batchId);
  }
}

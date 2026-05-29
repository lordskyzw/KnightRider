import 'package:shared_preferences/shared_preferences.dart';

/// Persistent app configuration. Open-LAN demo: just host:port for the Pi
/// and a base URL for the cloud.
class PiConfig {
  static const _kHost = 'pi_host';
  static const _kBacklogCursor = 'backlog_cursor';
  static const _kCloudUrl = 'cloud_url';
  static const _kCar3d = 'car_3d_enabled';
  static const _kAccent = 'accent_color';
  static const _kCarColor = 'car_color';
  static const _kWheelColor = 'wheel_color';
  static const _kLightsOn = 'lights_on';
  static const _kVehicleId = 'vehicle_id';

  // Matches the Pi's hostname set during the 2026-05-28 field-setup session.
  // mDNS resolves this on any device on the same LAN/hotspot, regardless of
  // what IP the iPhone hands the Pi this session.
  static const defaultHost = 'kitt.local:8080';
  static const defaultCloudUrl =
      'https://knight-rider-cloud-production.up.railway.app';

  static Future<String> host() async {
    final prefs = await SharedPreferences.getInstance();
    return prefs.getString(_kHost) ?? defaultHost;
  }

  static Future<void> setHost(String value) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_kHost, value);
  }

  static Future<String> cloudUrl() async {
    final prefs = await SharedPreferences.getInstance();
    return prefs.getString(_kCloudUrl) ?? defaultCloudUrl;
  }

  static Future<void> setCloudUrl(String value) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_kCloudUrl, value);
  }

  /// Feature flag: render the rotatable 3D car model in the dashboard centre
  /// instead of the flat SVG silhouette. On by default; the SVG stays the
  /// safe fallback (e.g. if the WebView can't render).
  static Future<bool> car3dEnabled() async {
    final prefs = await SharedPreferences.getInstance();
    return prefs.getBool(_kCar3d) ?? true;
  }

  static Future<void> setCar3dEnabled(bool value) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setBool(_kCar3d, value);
  }

  /// User-chosen accent colour, stored as an ARGB int. Null means default.
  static Future<int?> accentColor() async {
    final prefs = await SharedPreferences.getInstance();
    return prefs.getInt(_kAccent);
  }

  static Future<void> setAccentColor(int argb) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setInt(_kAccent, argb);
  }

  /// Car paint tint as an ARGB int; null = factory finish.
  static Future<int?> carColor() async {
    final prefs = await SharedPreferences.getInstance();
    return prefs.getInt(_kCarColor);
  }

  static Future<void> setCarColor(int? argb) async {
    final prefs = await SharedPreferences.getInstance();
    if (argb == null) {
      await prefs.remove(_kCarColor);
    } else {
      await prefs.setInt(_kCarColor, argb);
    }
  }

  /// Wheel paint as an ARGB int; defaults to black.
  static Future<int> wheelColor() async {
    final prefs = await SharedPreferences.getInstance();
    return prefs.getInt(_kWheelColor) ?? 0xFF0A0A0A;
  }

  static Future<void> setWheelColor(int argb) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setInt(_kWheelColor, argb);
  }

  /// Whether the 3D car's lamps are lit. Off by default.
  static Future<bool> lightsOn() async {
    final prefs = await SharedPreferences.getInstance();
    return prefs.getBool(_kLightsOn) ?? false;
  }

  static Future<void> setLightsOn(bool value) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setBool(_kLightsOn, value);
  }

  /// Which vehicle's 3D model / silhouette to render. Stored as the catalog id
  /// (see vehicle_catalog.dart). VIN can't auto-select it — VIN isn't reliably
  /// OBD-readable — so the user picks in Settings.
  static Future<String> vehicleId() async {
    final prefs = await SharedPreferences.getInstance();
    return prefs.getString(_kVehicleId) ?? 'vitz';
  }

  static Future<void> setVehicleId(String id) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_kVehicleId, id);
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

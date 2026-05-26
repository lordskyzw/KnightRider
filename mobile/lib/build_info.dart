import 'package:package_info_plus/package_info_plus.dart';

/// Cached app build info so the dashboard / settings can show what APK is
/// running. Useful for telling a teammate over WhatsApp whether they need
/// to reinstall ("settings shows v0.2.0+2 — that's the right one").
class BuildInfo {
  static PackageInfo? _cached;

  static Future<PackageInfo> get() async {
    return _cached ??= await PackageInfo.fromPlatform();
  }

  static Future<String> displayVersion() async {
    final info = await get();
    return 'v${info.version}+${info.buildNumber}';
  }
}

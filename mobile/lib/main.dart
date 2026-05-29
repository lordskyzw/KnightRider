import 'package:flutter/material.dart';

import 'app_theme.dart';
import 'config.dart';
import 'dashboard_screen.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  // Restore the user's accent before first paint so there's no colour flash.
  final argb = await PiConfig.accentColor();
  if (argb != null) AppPalette.accent = Color(argb);
  runApp(const KnightRiderApp());
}

class KnightRiderApp extends StatelessWidget {
  const KnightRiderApp({super.key});

  @override
  Widget build(BuildContext context) {
    // Rebuild the MaterialApp (and its ThemeData) whenever the accent changes.
    return ValueListenableBuilder<int>(
      valueListenable: accentRevision,
      builder: (context, _, _) {
        return MaterialApp(
          title: 'Knight Rider',
          theme: buildAppTheme(),
          themeMode: ThemeMode.dark,
          debugShowCheckedModeBanner: false,
          home: const DashboardScreen(),
        );
      },
    );
  }
}

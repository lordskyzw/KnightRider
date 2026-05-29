import 'package:flutter/material.dart';

/// Tesla-inspired palette, shared across every screen so the whole app wears
/// one aesthetic. All colours are fixed dark "furniture" except [accent],
/// which the user can customise in Settings.
class AppPalette {
  static const bg       = Color(0xFF0A0A0B);
  static const surface  = Color(0xFF16171A);
  static const surface2 = Color(0xFF1F2024);
  static const textHi   = Color(0xFFF0F0F2);
  static const textMid  = Color(0xFF8E8E92);
  static const textLow  = Color(0xFF5A5A5E);
  static const divider  = Color(0xFF2A2B2F);
  static const live     = Color(0xFF34C759);
  static const warning  = Color(0xFFFF9F0A);
  static const cool     = Color(0xFF64D2FF);

  /// User-customisable accent. Mutable: loaded from prefs at startup and
  /// updated when the user picks a different colour. Widgets read it at build
  /// time, so a repaint picks up the new value.
  static Color accent = kAccentOptions.first.color;
}

/// Bumped whenever the accent changes, so the root [MaterialApp] rebuilds its
/// ThemeData (and Material-themed widgets recolour immediately).
final accentRevision = ValueNotifier<int>(0);

class AccentOption {
  final String name;
  final Color color;
  const AccentOption(this.name, this.color);
}

/// The swatches offered in Settings. First entry is the default (KITT red).
const List<AccentOption> kAccentOptions = [
  AccentOption('KITT Red', Color(0xFFC8102E)),
  AccentOption('Cyber Cyan', Color(0xFF00D9E0)),
  AccentOption('Signal Amber', Color(0xFFFF9F0A)),
  AccentOption('Track Green', Color(0xFF34C759)),
  AccentOption('Electric Violet', Color(0xFF8E5BFF)),
  AccentOption('Ion Blue', Color(0xFF0A84FF)),
];

/// A selectable car paint. [tint] of null keeps the model's factory finish;
/// any other colour is multiplied over the (neutral silver) body.
class CarColorOption {
  final String name;
  final Color? tint;
  const CarColorOption(this.name, this.tint);
}

/// Paint choices offered in Settings. First is the original silver.
const List<CarColorOption> kCarColorOptions = [
  CarColorOption('Factory', null),
  CarColorOption('Race Red', Color(0xFFD0021B)),
  CarColorOption('Midnight', Color(0xFF1B2A4A)),
  CarColorOption('Racing Green', Color(0xFF1F6B3B)),
  CarColorOption('Sunset', Color(0xFFFF6A00)),
  CarColorOption('Mamba', Color(0xFFF5C518)),
  CarColorOption('Pearl', Color(0xFFEDEDED)),
  CarColorOption('Graphite', Color(0xFF3A3D42)),
];

/// Builds the global dark theme from the current [AppPalette.accent].
ThemeData buildAppTheme() {
  final scheme = ColorScheme.fromSeed(
    seedColor: AppPalette.accent,
    brightness: Brightness.dark,
  ).copyWith(
    primary: AppPalette.accent,
    surface: AppPalette.bg,
  );
  return ThemeData(
    useMaterial3: true,
    colorScheme: scheme,
    scaffoldBackgroundColor: AppPalette.bg,
    canvasColor: AppPalette.bg,
    dividerColor: AppPalette.divider,
    splashColor: AppPalette.accent.withValues(alpha: 0.12),
    highlightColor: AppPalette.accent.withValues(alpha: 0.06),
    appBarTheme: const AppBarTheme(
      backgroundColor: AppPalette.bg,
      foregroundColor: AppPalette.textHi,
      elevation: 0,
      centerTitle: false,
    ),
    textSelectionTheme: TextSelectionThemeData(
      cursorColor: AppPalette.accent,
      selectionColor: AppPalette.accent.withValues(alpha: 0.3),
      selectionHandleColor: AppPalette.accent,
    ),
    switchTheme: SwitchThemeData(
      thumbColor: WidgetStateProperty.resolveWith(
        (s) => s.contains(WidgetState.selected)
            ? AppPalette.accent
            : AppPalette.textMid,
      ),
      trackColor: WidgetStateProperty.resolveWith(
        (s) => s.contains(WidgetState.selected)
            ? AppPalette.accent.withValues(alpha: 0.4)
            : AppPalette.surface2,
      ),
      trackOutlineColor: WidgetStateProperty.all(AppPalette.divider),
    ),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: AppPalette.surface,
      hintStyle: const TextStyle(color: AppPalette.textLow),
      enabledBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(10),
        borderSide: const BorderSide(color: AppPalette.divider),
      ),
      focusedBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(10),
        borderSide: BorderSide(color: AppPalette.accent, width: 1.5),
      ),
    ),
    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(
        backgroundColor: AppPalette.accent,
        foregroundColor: Colors.white,
      ),
    ),
  );
}

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:knight_rider_mobile/car_model.dart';
import 'package:knight_rider_mobile/main.dart';

void main() {
  testWidgets('app boots without throwing', (tester) async {
    // `flutter test` has no WebViewPlatform; stub the 3D car so booting the
    // (3D-by-default) dashboard doesn't try to spin up a real WebView.
    debugDisableCarWebView = true;
    addTearDown(() => debugDisableCarWebView = false);

    // Use a portrait-phone-shaped viewport so the dashboard layout fits.
    tester.view.physicalSize = const Size(420, 900);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    await tester.pumpWidget(const KnightRiderApp());
    // Dashboard starts disconnected and with no samples; verify the status
    // pill is rendered in its OFFLINE state.
    expect(find.text('OFFLINE'), findsOneWidget);
  });
}

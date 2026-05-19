import 'package:flutter_test/flutter_test.dart';

import 'package:knight_rider_mobile/main.dart';

void main() {
  testWidgets('app boots without throwing', (tester) async {
    await tester.pumpWidget(const KnightRiderApp());
    // The dashboard starts in disconnected state with no samples; verify the
    // status chip is rendered.
    expect(find.text('OFFLINE'), findsOneWidget);
  });
}

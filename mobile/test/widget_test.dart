import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:visionqc_mobile/services/edge_client.dart';

void main() {
  test('unpaired client refuses to inspect', () async {
    final client = EdgeClient('http://127.0.0.1:8765');
    expect(
      () => client.inspect(Uint8List.fromList([1, 2, 3])),
      throwsA(isA<EdgeException>()),
    );
  });
}

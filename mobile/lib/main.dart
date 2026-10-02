import 'package:flutter/material.dart';

import 'screens/relay_pair_screen.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  runApp(const VisionQCMobileApp());
}

class VisionQCMobileApp extends StatelessWidget {
  const VisionQCMobileApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'VisionQC',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(
          seedColor: const Color(0xFF2563EB),
          brightness: Brightness.light,
        ),
        useMaterial3: true,
      ),
      home: const RelayPairScreen(),
    );
  }
}

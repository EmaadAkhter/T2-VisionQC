import 'package:flutter/material.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

import 'config.local.dart';
import 'screens/login_screen.dart';
import 'screens/relay_pair_screen.dart';

/// Build with `--dart-define=VISIONQC_RELAY_ONLY=true` for a camera-only APK
/// that skips Supabase sign-in and opens relay pairing directly.
const bool relayOnly = bool.fromEnvironment('VISIONQC_RELAY_ONLY');

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  if (!relayOnly) {
    await Supabase.initialize(
      url: supabaseUrl,
      publishableKey: supabaseAnonKey,
    );
  }
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
      home: relayOnly ? const RelayPairScreen() : const LoginScreen(),
    );
  }
}

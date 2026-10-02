import 'package:flutter/material.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

import 'pair_screen.dart';

/// Sign in with the same organization account used on the edge desktop app.
class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key});

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  final _email = TextEditingController(text: 'operator@visionqc.local');
  final _password = TextEditingController(text: 'visionqc123');
  final _name = TextEditingController();
  bool _busy = false;
  bool _signedIn = false;
  String? _error;
  List<Map<String, dynamic>> _orgs = [];

  Future<void> _signIn() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await Supabase.instance.client.auth.signInWithPassword(
        email: _email.text.trim(),
        password: _password.text,
      );
      // Admins grant access from the web console; claim any pending
      // invitation for this account, then use the assigned organization.
      try {
        await Supabase.instance.client.rpc('claim_invitations');
      } catch (_) {
        // Older server without the RPC: continue with existing memberships.
      }
      final rows = await Supabase.instance.client
          .from('my_orgs')
          .select()
          .order('name');
      final orgs = List<Map<String, dynamic>>.from(rows);
      if (orgs.length == 1) {
        if (!mounted) return;
        _continueToPairing(orgs.first);
        return;
      }
      setState(() {
        _orgs = orgs;
        _signedIn = true;
      });
    } catch (error) {
      setState(() => _error = '$error');
    } finally {
      setState(() => _busy = false);
    }
  }

  Future<void> _signUp() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await Supabase.instance.client.auth.signUp(
        email: _email.text.trim(),
        password: _password.text,
        data: {'full_name': _name.text.trim()},
      );
      await _signIn();
    } catch (error) {
      setState(() => _error = '$error');
      setState(() => _busy = false);
    }
  }

  void _continueToPairing(Map<String, dynamic> org) {
    Navigator.of(context).push(
      MaterialPageRoute(
        builder: (_) => PairScreen(
          orgId: org['id'] as String,
          orgName: org['name'] as String,
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(24),
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 420),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  const Text(
                    'VisionQC',
                    style: TextStyle(fontSize: 30, fontWeight: FontWeight.bold),
                  ),
                  const SizedBox(height: 4),
                  Text(
                    'Phone camera companion',
                    style: TextStyle(color: Colors.grey.shade600),
                  ),
                  const SizedBox(height: 24),
                  TextField(
                    controller: _email,
                    keyboardType: TextInputType.emailAddress,
                    decoration: const InputDecoration(
                      labelText: 'Email',
                      border: OutlineInputBorder(),
                    ),
                  ),
                  const SizedBox(height: 12),
                  TextField(
                    controller: _password,
                    obscureText: true,
                    decoration: const InputDecoration(
                      labelText: 'Password',
                      border: OutlineInputBorder(),
                    ),
                  ),
                  const SizedBox(height: 12),
                  TextField(
                    controller: _name,
                    decoration: const InputDecoration(
                      labelText: 'Full name (sign-up only)',
                      border: OutlineInputBorder(),
                    ),
                  ),
                  if (_error != null) ...[
                    const SizedBox(height: 12),
                    Text(_error!, style: const TextStyle(color: Colors.red)),
                  ],
                  const SizedBox(height: 16),
                  FilledButton(
                    onPressed: _busy ? null : _signIn,
                    child: _busy
                        ? const SizedBox(
                            height: 18,
                            width: 18,
                            child: CircularProgressIndicator(strokeWidth: 2),
                          )
                        : const Text('Sign in'),
                  ),
                  const SizedBox(height: 8),
                  OutlinedButton(
                    onPressed: _busy ? null : _signUp,
                    child: const Text('Create account'),
                  ),
                  if (_signedIn && _orgs.isEmpty) ...[
                    const SizedBox(height: 16),
                    const Text(
                      'No access yet. Ask your administrator to invite you '
                      'from the VisionQC web console, then sign in again.',
                      style: TextStyle(color: Colors.black54),
                    ),
                  ],
                  if (_orgs.isNotEmpty) ...[
                    const SizedBox(height: 24),
                    const Text('Choose organization',
                        style: TextStyle(fontWeight: FontWeight.w600)),
                    const SizedBox(height: 8),
                    for (final org in _orgs)
                      Card(
                        child: ListTile(
                          title: Text(org['name'] as String),
                          subtitle: Text('${org['role']}'),
                          trailing: const Icon(Icons.chevron_right),
                          onTap: () => _continueToPairing(org),
                        ),
                      ),
                  ],
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}

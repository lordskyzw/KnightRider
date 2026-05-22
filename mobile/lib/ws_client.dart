import 'dart:async';
import 'dart:convert';

import 'package:web_socket_channel/web_socket_channel.dart';

import 'sample.dart';

enum WsState { connecting, connected, disconnected }

/// WebSocket client with auto-reconnect. Exposes a [stream] of [Sample]s, a
/// [stateStream] of connection-status changes, and [lastError] for the most
/// recent failure reason (cleared on successful CONNECTED transition).
class WsClient {
  final String Function() hostProvider;
  final Duration reconnectDelay;

  WebSocketChannel? _channel;
  StreamSubscription? _sub;
  Timer? _reconnectTimer;
  bool _disposed = false;

  final _samples = StreamController<Sample>.broadcast();
  final _state = StreamController<WsState>.broadcast();
  WsState _currentState = WsState.disconnected;
  String? _lastError;
  String? _lastTriedUri;

  WsClient({
    required this.hostProvider,
    this.reconnectDelay = const Duration(seconds: 2),
  });

  Stream<Sample> get stream => _samples.stream;
  Stream<WsState> get stateStream => _state.stream;
  WsState get state => _currentState;

  /// Most recent connection-failure reason. Cleared on a successful sample
  /// from the live stream (proves the channel is genuinely up).
  String? get lastError => _lastError;

  /// URI from the most recent connect attempt. Handy for UI debugging.
  String? get lastTriedUri => _lastTriedUri;

  void start() {
    _disposed = false;
    _connect();
  }

  void _setState(WsState s) {
    if (_currentState == s) return;
    _currentState = s;
    _state.add(s);
  }

  void _connect() {
    if (_disposed) return;
    _setState(WsState.connecting);
    final host = _normalizeHost(hostProvider());
    final uri = Uri.parse('ws://$host/ws/live');
    _lastTriedUri = uri.toString();
    try {
      _channel = WebSocketChannel.connect(uri);
    } catch (e) {
      _lastError = 'connect threw: ${e.toString()}';
      _scheduleReconnect();
      return;
    }
    // Optimistic — actual handshake happens async; if it fails, onError fires.
    _setState(WsState.connected);

    _sub = _channel!.stream.listen(
      (data) {
        // First successful frame proves the channel is genuinely up.
        _lastError = null;
        try {
          final json = jsonDecode(data as String) as Map<String, dynamic>;
          _samples.add(Sample.fromJson(json));
        } catch (_) {
          // Drop malformed frames silently — the canonical store has them.
        }
      },
      onError: (e) {
        _lastError = 'stream error: ${e.toString()}';
        _scheduleReconnect();
      },
      onDone: () {
        // onDone with no prior error often means the server closed the
        // connection — record it so OFFLINE has a reason on screen.
        _lastError ??= 'channel closed';
        _scheduleReconnect();
      },
      cancelOnError: true,
    );
  }

  void _scheduleReconnect() {
    _sub?.cancel();
    _sub = null;
    _channel?.sink.close();
    _channel = null;
    _setState(WsState.disconnected);
    if (_disposed) return;
    _reconnectTimer?.cancel();
    _reconnectTimer = Timer(reconnectDelay, _connect);
  }

  /// Tolerates common user mistakes in the Settings field: leading
  /// http://, https://, ws://, wss:// schemes, trailing slashes, and
  /// whitespace.
  static String _normalizeHost(String raw) {
    var h = raw.trim();
    for (final scheme in const ['https://', 'http://', 'wss://', 'ws://']) {
      if (h.toLowerCase().startsWith(scheme)) {
        h = h.substring(scheme.length);
        break;
      }
    }
    while (h.endsWith('/')) {
      h = h.substring(0, h.length - 1);
    }
    return h;
  }

  Future<void> dispose() async {
    _disposed = true;
    _reconnectTimer?.cancel();
    await _sub?.cancel();
    await _channel?.sink.close();
    await _samples.close();
    await _state.close();
  }
}

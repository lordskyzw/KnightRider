import 'dart:async';
import 'dart:convert';

import 'package:web_socket_channel/web_socket_channel.dart';

import 'sample.dart';

enum WsState { connecting, connected, disconnected }

/// WebSocket client with auto-reconnect. Exposes a [stream] of [Sample]s and
/// a [stateStream] of connection-status changes.
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

  WsClient({
    required this.hostProvider,
    this.reconnectDelay = const Duration(seconds: 2),
  });

  Stream<Sample> get stream => _samples.stream;
  Stream<WsState> get stateStream => _state.stream;
  WsState get state => _currentState;

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
    final host = hostProvider();
    final uri = Uri.parse('ws://$host/ws/live');
    try {
      _channel = WebSocketChannel.connect(uri);
    } catch (e) {
      _scheduleReconnect();
      return;
    }
    _setState(WsState.connected);

    _sub = _channel!.stream.listen(
      (data) {
        try {
          final json = jsonDecode(data as String) as Map<String, dynamic>;
          _samples.add(Sample.fromJson(json));
        } catch (_) {
          // Drop malformed frames silently — the canonical store has them.
        }
      },
      onError: (_) => _scheduleReconnect(),
      onDone: _scheduleReconnect,
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

  Future<void> dispose() async {
    _disposed = true;
    _reconnectTimer?.cancel();
    await _sub?.cancel();
    await _channel?.sink.close();
    await _samples.close();
    await _state.close();
  }
}

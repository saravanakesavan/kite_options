/**
 * AlertWebSocket — manages the WebSocket connection to the backend alert stream.
 *
 * Usage (inside a React component or context):
 *   const ws = new AlertWebSocket(token, onMessage);
 *   ws.connect();
 *   // later:
 *   ws.disconnect();
 *
 * Features:
 *  - Auto-reconnects with exponential back-off (max 30 s) when the connection drops.
 *  - Silently ignores PING frames (server heartbeat).
 *  - Calls onMessage(parsedPayload) for every real event.
 */

const WS_BASE = process.env.REACT_APP_WS_URL || 'ws://localhost:8000';
const INITIAL_RETRY_MS = 1_000;
const MAX_RETRY_MS = 30_000;

export class AlertWebSocket {
  constructor(token, onMessage) {
    this._token      = token;
    this._onMessage  = onMessage;
    this._ws         = null;
    this._retryDelay = INITIAL_RETRY_MS;
    this._stopped    = false;
    this._retryTimer = null;
  }

  connect() {
    if (this._stopped) return;
    const url = `${WS_BASE}/ws?token=${encodeURIComponent(this._token)}`;
    this._ws = new WebSocket(url);

    this._ws.onopen = () => {
      console.log('[AlertWS] connected');
      this._retryDelay = INITIAL_RETRY_MS; // reset back-off on success
    };

    this._ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.type === 'PING') return; // ignore heartbeats
        this._onMessage(data);
      } catch (e) {
        console.warn('[AlertWS] bad message:', event.data);
      }
    };

    this._ws.onerror = (err) => {
      console.warn('[AlertWS] error:', err);
    };

    this._ws.onclose = (event) => {
      console.log(`[AlertWS] closed (code=${event.code})`);
      if (!this._stopped && event.code !== 1008) {
        // 1008 = policy violation (bad token) — don't retry
        this._scheduleReconnect();
      }
    };
  }

  _scheduleReconnect() {
    this._retryTimer = setTimeout(() => {
      console.log(`[AlertWS] reconnecting in ${this._retryDelay}ms…`);
      this.connect();
      this._retryDelay = Math.min(this._retryDelay * 2, MAX_RETRY_MS);
    }, this._retryDelay);
  }

  disconnect() {
    this._stopped = true;
    if (this._retryTimer) clearTimeout(this._retryTimer);
    if (this._ws) {
      this._ws.onclose = null; // prevent reconnect loop
      this._ws.close();
      this._ws = null;
    }
    console.log('[AlertWS] disconnected');
  }
}

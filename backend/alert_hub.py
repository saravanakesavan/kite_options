"""
Alert Hub — thin pub/sub layer between the position monitor and the WebSocket endpoint.

position_monitor.py calls `broadcast(user_id, payload)` when an alert fires.
main.py registers WebSocket connections via `AlertHub.connect/disconnect`.

Design:
  - One `AlertHub` singleton shared across the process.
  - Each connected WebSocket is stored per user_id.
  - broadcast() is async-safe: it schedules the coroutine on the running event loop
    so it can be called from sync code inside position_monitor.
"""

import asyncio
import json
import logging
from typing import Dict, List
from fastapi import WebSocket

logger = logging.getLogger(__name__)


class AlertHub:
    """Manages WebSocket connections and fan-out broadcasting."""

    def __init__(self):
        # user_id → list of active WebSocket connections (multiple tabs allowed)
        self._connections: Dict[int, List[WebSocket]] = {}

    async def connect(self, user_id: int, ws: WebSocket):
        await ws.accept()
        self._connections.setdefault(user_id, []).append(ws)
        logger.info(f"[AlertHub] WS connected for user {user_id} "
                    f"(total={len(self._connections[user_id])})")

    def disconnect(self, user_id: int, ws: WebSocket):
        conns = self._connections.get(user_id, [])
        if ws in conns:
            conns.remove(ws)
        if not conns:
            self._connections.pop(user_id, None)
        logger.info(f"[AlertHub] WS disconnected for user {user_id}")

    async def _send_to_user(self, user_id: int, payload: dict):
        """Send payload to all WebSocket connections for a user."""
        conns = list(self._connections.get(user_id, []))
        dead  = []
        for ws in conns:
            try:
                await ws.send_text(json.dumps(payload))
            except Exception as e:
                logger.warning(f"[AlertHub] Send failed for user {user_id}: {e}")
                dead.append(ws)
        for ws in dead:
            self.disconnect(user_id, ws)

    def broadcast(self, user_id: int, payload: dict):
        """
        Thread-safe broadcast.  Called from sync code in position_monitor.
        Schedules _send_to_user on the running event loop if one exists.
        """
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(self._send_to_user(user_id, payload))
            else:
                loop.run_until_complete(self._send_to_user(user_id, payload))
        except RuntimeError:
            logger.warning("[AlertHub] No event loop — alert not pushed to WS")


# Process-wide singleton
hub = AlertHub()

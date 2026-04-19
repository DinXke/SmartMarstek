"""
telegram.py – SmartMarstek Telegram notification bridge.

Sends event notifications to the CommunicationAgent service (HTTP on port 3001).
Tracks pending approvals in memory with a 30-minute TTL.
"""

import json
import logging
import threading
import time
import uuid
from typing import Callable, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

log = logging.getLogger("telegram")

COMM_SERVICE_URL = "http://localhost:3001/api/notify"
APPROVAL_TTL_S   = 1800  # 30 minutes

_approvals_lock = threading.Lock()
# approval_id → {"created_at": float, "event_type": str, "payload": dict,
#                 "status": "pending"|"approved"|"rejected",
#                 "on_approve": Callable|None, "on_reject": Callable|None}
_pending_approvals: dict[str, dict] = {}


def _prune_expired() -> None:
    now = time.time()
    expired = [k for k, v in _pending_approvals.items()
               if now - v["created_at"] > APPROVAL_TTL_S]
    for k in expired:
        del _pending_approvals[k]


def notify_event(
    event_type: str,
    payload: dict,
    requires_approval: bool = False,
    on_approve: Optional[Callable] = None,
    on_reject: Optional[Callable] = None,
    settings: Optional[dict] = None,
) -> Optional[str]:
    """
    Send an event notification to the communication service.

    Returns the approval_id when requires_approval=True, else None.
    Silently skips when telegram is disabled or the event is toggled off.
    """
    # Import here to avoid circular imports at module level
    try:
        from strategy import load_strategy_settings
        s = settings or load_strategy_settings()
    except Exception:
        s = settings or {}

    if not s.get("telegram_enabled", False):
        return None

    events_cfg: dict = s.get("telegram_events", {})
    if event_type in events_cfg and not events_cfg[event_type]:
        return None

    approval_id: Optional[str] = None
    if requires_approval:
        approval_id = str(uuid.uuid4())
        with _approvals_lock:
            _prune_expired()
            _pending_approvals[approval_id] = {
                "created_at": time.time(),
                "event_type": event_type,
                "payload":    payload,
                "status":     "pending",
                "on_approve": on_approve,
                "on_reject":  on_reject,
            }

    body = {
        "event_type":        event_type,
        "payload":           payload,
        "requires_approval": requires_approval,
        "chat_id":           s.get("telegram_chat_id", ""),
    }
    if approval_id:
        body["approval_id"] = approval_id

    try:
        data = json.dumps(body).encode()
        req  = Request(COMM_SERVICE_URL, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Content-Length", str(len(data)))
        with urlopen(req, timeout=5) as resp:
            log.info("telegram.notify_event: %s sent (status=%d approval=%s)",
                     event_type, resp.status, approval_id)
    except URLError as exc:
        log.warning("telegram.notify_event: %s failed: %s", event_type, exc)
    except Exception as exc:
        log.warning("telegram.notify_event: %s unexpected error: %s", event_type, exc)

    return approval_id


def resolve_approval(approval_id: str, action: str) -> dict:
    """
    Resolve a pending approval.

    action must be "approve" or "reject".
    Returns {"ok": True, "status": action} or {"ok": False, "error": "..."}.
    """
    if action not in ("approve", "reject"):
        return {"ok": False, "error": "action must be 'approve' or 'reject'"}

    with _approvals_lock:
        _prune_expired()
        entry = _pending_approvals.get(approval_id)
        if entry is None:
            return {"ok": False, "error": "approval not found or expired"}
        if entry["status"] != "pending":
            return {"ok": False, "error": f"already resolved: {entry['status']}"}
        entry["status"] = action
        callback = entry.get("on_approve") if action == "approve" else entry.get("on_reject")

    if callback:
        try:
            callback()
        except Exception as exc:
            log.warning("telegram.resolve_approval: callback error: %s", exc)

    log.info("telegram.resolve_approval: %s → %s", approval_id, action)
    return {"ok": True, "status": action}


def get_pending_approvals() -> list[dict]:
    """Return all pending (non-expired, non-resolved) approvals for inspection."""
    with _approvals_lock:
        _prune_expired()
        return [
            {"approval_id": k, "event_type": v["event_type"],
             "payload": v["payload"], "created_at": v["created_at"]}
            for k, v in _pending_approvals.items()
            if v["status"] == "pending"
        ]

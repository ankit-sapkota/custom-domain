"""
Pending-domain queue backed by a JSON file.

Domains are added here when the user requests them via POST /domains.
A background polling loop periodically checks whether each pending domain's
A record points to the server.  Once verified the domain is promoted into
the live Caddy configuration and removed from the queue.

Entries older than ``MAX_PENDING_SECONDS`` (default 24 h) are marked as
``failed`` and excluded from further polling.  A verify call can reset
a failed domain back to ``pending``.
"""

import json
import logging
import os
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_PENDING_FILE = "domains/pending.json"
MAX_PENDING_SECONDS = int(os.environ.get("MAX_PENDING_SECONDS", 86400))  # 24 h


class DomainQueue:
    """Thread-safe, JSON-file-persisted queue of pending domains."""

    def __init__(self, filepath: Optional[str] = None):
        self.filepath = filepath or os.environ.get(
            "PENDING_DOMAINS_FILE", DEFAULT_PENDING_FILE
        )
        self._lock = threading.Lock()
        self._pending: dict[str, dict] = {}
        self._load()

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def _load(self):
        """Load the pending map from disk (if the file exists)."""
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, "r") as fh:
                    data = json.load(fh)
                if isinstance(data, dict):
                    self._pending = data
                else:
                    self._pending = {}
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning(f"Could not load pending queue from {self.filepath}: {exc}")
                self._pending = {}
        else:
            self._pending = {}

    def _save(self):
        """Persist the current pending map to disk."""
        directory = os.path.dirname(self.filepath)
        if directory and not os.path.exists(directory):
            os.makedirs(directory, exist_ok=True)
        try:
            with open(self.filepath, "w") as fh:
                json.dump(self._pending, fh, indent=2)
        except OSError as exc:
            logger.error(f"Could not save pending queue to {self.filepath}: {exc}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add(self, domain: str, upstream: str):
        """Add a domain to the pending queue (idempotent)."""
        with self._lock:
            if domain not in self._pending:
                self._pending[domain] = {
                    "upstream": upstream,
                    "added_at": time.time(),
                    "status": "pending",
                }
                self._save()
                logger.info(f"Pending queue: added '{domain}' (upstream={upstream})")
            else:
                logger.info(f"Pending queue: '{domain}' already in queue — skipped")

    def remove(self, domain: str):
        """Remove a domain from the pending queue."""
        with self._lock:
            if domain in self._pending:
                del self._pending[domain]
                self._save()
                logger.info(f"Pending queue: removed '{domain}'")

    def get_all(self) -> dict[str, dict]:
        """Return a *copy* of the current pending map."""
        with self._lock:
            return dict(self._pending)

    def is_pending(self, domain: str) -> bool:
        with self._lock:
            entry = self._pending.get(domain)
            return entry is not None and entry.get("status") == "pending"

    def is_failed(self, domain: str) -> bool:
        with self._lock:
            entry = self._pending.get(domain)
            return entry is not None and entry.get("status") == "failed"

    def get_status(self, domain: str) -> str | None:
        """Return the status of *domain* or None if not in the queue."""
        with self._lock:
            entry = self._pending.get(domain)
            return entry.get("status") if entry else None

    def mark_failed(self, domain: str):
        """Set the status of *domain* to ``failed``."""
        with self._lock:
            if domain in self._pending:
                self._pending[domain]["status"] = "failed"
                self._save()
                logger.info(f"Pending queue: marked '{domain}' as failed")

    def mark_pending(self, domain: str):
        """Reset a domain back to ``pending`` (e.g. after a verify call on a
        failed domain).  Also refreshes ``added_at`` so it gets a fresh TTL."""
        with self._lock:
            if domain in self._pending:
                self._pending[domain]["status"] = "pending"
                self._pending[domain]["added_at"] = time.time()
                self._save()
                logger.info(f"Pending queue: reset '{domain}' to pending")

    def get_pending_only(self) -> dict[str, dict]:
        """Return only entries with status == 'pending'."""
        with self._lock:
            return {
                d: dict(info)
                for d, info in self._pending.items()
                if info.get("status") == "pending"
            }

    def cleanup_expired(self) -> list[str]:
        """Mark entries older than MAX_PENDING_SECONDS as ``failed``.

        Returns the list of domains that were newly marked failed."""
        now = time.time()
        failed: list[str] = []
        with self._lock:
            for domain, info in list(self._pending.items()):
                if info.get("status") != "pending":
                    continue
                age = now - info.get("added_at", 0)
                if age > MAX_PENDING_SECONDS:
                    info["status"] = "failed"
                    failed.append(domain)
            if failed:
                self._save()
                logger.info(f"Pending queue: marked as failed {failed}")
        return failed


# Module-level singleton
pending_queue = DomainQueue()

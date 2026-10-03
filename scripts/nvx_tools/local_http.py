"""Bounded local HTTP workers shared by capability-authenticated services."""

from __future__ import annotations

import http.server
import threading
from typing import Any


class BoundedServer(http.server.ThreadingHTTPServer):
    daemon_threads = False
    block_on_close = True

    def __init__(self, address: tuple[str, int], handler: Any):
        self._slots = threading.BoundedSemaphore(32)
        super().__init__(address, handler)

    def process_request(self, request: Any, client_address: Any) -> None:
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()

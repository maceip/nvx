"""Private host proxy worker; binding values arrive over a pipe, never a file/argv."""

from __future__ import annotations

import json
import ssl
import sys
import time
from pathlib import Path
from typing import Any, BinaryIO, cast

from .common import ScriptError
from .proxy import Binding, CredentialProxy
from .sandbox_lifecycle import process_running


def read_configuration(source: BinaryIO) -> dict[str, Any]:
    # Four 8 KiB UTF-8 bindings can expand when JSON escapes their characters.
    raw = source.readline((256 << 10) + 1)
    if len(raw) > 256 << 10:
        raise ScriptError("host credential configuration exceeded its size limit")
    return cast(dict[str, Any], json.loads(raw))


def main() -> None:
    document = read_configuration(sys.stdin.buffer)
    state = Path(document["state"])
    bindings = tuple(Binding(**item) for item in document["bindings"])
    context = ssl.create_default_context()
    if document.get("ca"):
        context.load_verify_locations(cafile=document["ca"])
    proxy = CredentialProxy(
        frozenset((item[0], item[1]) for item in document["allowed"]),
        bindings,
        document["capability"],
        state / "proxy.jsonl",
        state.name,
        context=context,
    )
    proxy.start()
    print(json.dumps({"port": proxy.port}), flush=True)
    observed = False
    deadline = time.monotonic() + 180
    try:
        while True:
            running = (state / "runtime.json").is_file()
            if running:
                runtime = json.loads((state / "runtime.json").read_bytes())
                running = process_running(int(runtime["pid"]))
            observed = observed or running
            if observed and not running:
                break
            if not observed and time.monotonic() >= deadline:
                break
            time.sleep(0.1)
    finally:
        proxy.stop()
        (state / "proxy-runtime.json").unlink(missing_ok=True)


if __name__ == "__main__":
    main()

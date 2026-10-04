"""Twenty real repaired clones, retained runtime proof, and a failing control."""

from __future__ import annotations

import json
import platform
import threading
import time
from pathlib import Path
from typing import Any

from . import registry, warm
from .common import ScriptError
from .control_session import ControlSession
from .image_run import workload_argv
from .local_http import BoundedServer
from .policy import resolve
from .receipt_tests import Collector


def validate_clone(
    result: dict[str, Any],
    generation: str,
    source_generation: str,
    before: float,
    after: float,
    repair: bool,
) -> None:
    if not result["egress"] or result["output"] != 42 or not result["initialized"]:
        raise ScriptError(
            "warm clone lost initialized runtime, output, or allowed egress"
        )
    if generation == source_generation:
        raise ScriptError("warm clone did not receive a fresh host generation ID")
    if repair:
        if (
            result["mid"] != generation
            or result["hostname"] != "nvx-" + generation
            or not before - 1.1 <= result["clock"] <= after + 1.1
            or result["uid"] != 65534
            or result["channel"] != "denied"
        ):
            details = {
                "mid": result["mid"],
                "generation": generation,
                "hostname": result["hostname"],
                "clock": result["clock"],
                "host_before": before,
                "host_after": after,
                "uid": result["uid"],
                "channel": result["channel"],
            }
            raise ScriptError(
                "warm clone repair or private runtime channel failed: "
                + json.dumps(details, sort_keys=True)
            )
    elif result["mid"] == generation:
        raise ScriptError("disabled repair control unexpectedly passed")


def validate_batch(
    records: list[dict[str, Any]], controls: list[dict[str, Any]], count: int = 20
) -> None:
    if len(records) != count or len(controls) != 2:
        raise ScriptError("warm clone evidence has an incomplete batch")
    for key in ("id", "generation", "mid", "hostname", "entropy"):
        if len({item[key] for item in records}) != count:
            raise ScriptError("repaired clones repeated " + key)
    if len({item["origin"] for item in records}) != 1:
        raise ScriptError(
            "Python was restarted instead of retaining its initialized state"
        )
    if controls[0]["mid"] != controls[1]["mid"]:
        raise ScriptError(
            "disabled repair control did not reproduce stale machine identity"
        )


def run(backend: str, output: Path, timeout: float) -> None:
    output.mkdir(parents=True, exist_ok=True)
    server = BoundedServer(("127.0.0.1", 0), Collector)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_port
    probe = f"""
import builtins,json,os,socket,time,sys
connection=socket.create_connection(('192.168.127.1',{port}),3)
connection.sendall(b'GET / HTTP/1.0\\r\\nHost: nvx-test\\r\\n\\r\\n')
reply=b''
while True:
    chunk=connection.recv(4096)
    if not chunk:break
    reply+=chunk
connection.close()
try:
    fd=os.open('/proc/1/fd/198',os.O_WRONLY);os.close(fd);channel='visible'
except PermissionError:channel='denied'
print(json.dumps(dict(origin=builtins.__nvx_warm_start__,initialized='http.client' in sys.modules,
    mid=open('/etc/machine-id').read().strip(),hostname=os.uname().nodename,
    clock=time.time(),entropy=os.urandom(32).hex(),egress=b'nvx-collector' in reply,
    output=6*7,uid=os.getuid(),channel=channel)))
"""
    records: list[dict[str, Any]] = []
    controls: list[dict[str, Any]] = []
    progress: list[dict[str, Any]] = []
    try:
        for repair, count, label in ((True, 20, "repaired"), (False, 2, "disabled")):
            template = output / label
            warm.capture(
                "python:3.12-slim",
                backend,
                template,
                resolve(
                    {
                        "profile": "ci" if repair else "risky",
                        "allow": [f"192.168.127.1:tcp:{port}"] if repair else [],
                    }
                ),
                timeout=timeout,
                repair=repair,
                runtime="python",
                host_loopback=(
                    "allow"
                    if platform.machine().lower() in ("aarch64", "arm64")
                    else None
                ),
            )
            metadata = warm.admit(template)
            for index in range(count):
                state = warm.clone(template, timeout=timeout)
                try:
                    runtime = json.loads((state / "runtime.json").read_bytes())
                    with ControlSession.connect(
                        Path(runtime["control_endpoint"]),
                        (state / "control.capability").read_bytes(),
                        timeout,
                    ) as session:
                        generation = session.generation(timeout)
                    image = json.loads((state / "image.json").read_bytes())["manifest"]
                    argv = workload_argv(image, ("python", "-c", probe))
                    (state / "argv.json").write_text(json.dumps(argv))
                    before = time.time()
                    executed = registry.exec_guest(state, argv, timeout)
                    after = time.time()
                    if executed.returncode or executed.stderr:
                        raise ScriptError("warm clone workload failed")
                    result: dict[str, Any] = json.loads(executed.stdout)
                    result.update(id=state.name, generation=generation, clone=index)
                    progress.append(
                        {
                            "repair": repair,
                            "source_generation": metadata["source_generation"],
                            "host_before": before,
                            "host_after": after,
                            "result": result,
                        }
                    )
                    (output / "progress.json").write_text(
                        json.dumps(progress, indent=2) + "\n"
                    )
                    validate_clone(
                        result,
                        generation,
                        metadata["source_generation"],
                        before,
                        after,
                        repair,
                    )
                    if repair:
                        records.append(result)
                    else:
                        controls.append(result)
                finally:
                    registry.stop_instance(state, timeout)
            print(f"warm {label}: {count} clones exercised", flush=True)
        validate_batch(records, controls)
        (output / "clones.json").write_text(
            json.dumps({"repaired": records, "disabled": controls}, indent=2) + "\n"
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    print("NVX-WARM-CLONE-OK")

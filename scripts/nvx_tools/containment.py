"""Public, scoped containment probes. Every passing cell requires a failing control."""

from __future__ import annotations

import argparse
import contextlib
import http.server
import io
import json
import platform
import secrets
import subprocess
import sys
import tempfile
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

from . import sandbox_lifecycle
from .build_constants import BuildConstants
from .common import ScriptError
from .image import ImageCache
from .policy_tests import DEVICES, RESOURCES, SECCOMP, execute, validate
from .sandbox import SandboxLaunch, SandboxLayer
from .snapshot import verify_snapshot


@dataclass(frozen=True)
class Probe:
    family: str
    scope: str
    control: str


PROBES = (
    Probe(
        "identity",
        "non-root, empty capabilities, NNP and seccomp syscall denial",
        "risky",
    ),
    Probe(
        "filesystem", "system writes denied; scratch writable; mount denied", "risky"
    ),
    Probe(
        "process/namespace",
        "agent invisible; creation of another network namespace denied",
        "risky",
    ),
    Probe(
        "devices",
        "CAP_MKNOD retained in ci; raw memory/block nodes denied; proc mask",
        "risky",
    ),
    Probe(
        "resources",
        "bounded fork simulant reaches pids limit without stopping host",
        "risky",
    ),
    Probe(
        "network",
        "collector connection denied; actual host observes control request",
        "risky",
    ),
    Probe(
        "snapshot integrity",
        "same-length RAM/state tamper rejected; foreign arch refused",
        "legacy structural-only format",
    ),
    Probe(
        "tenant isolation",
        "private scratch hides previous instance writes",
        "risky with deliberately shared test scratch",
    ),
)
FILESYSTEM = r"""
import ctypes,json,os
c=ctypes.CDLL(None,use_errno=True)
result={}
open('/tmp/nvx-scratch','w').write('scratch')
result['scratch']=open('/tmp/nvx-scratch').read()
plain=b'nvx-ransomware-simulant-owned-fixture'
open('/tmp/nvx-loot','wb').write(plain)
encrypted=bytes(byte^0x5a for byte in plain)
open('/tmp/nvx-loot','wb').write(encrypted)
result['loot_encrypted']=open('/tmp/nvx-loot','rb').read()==encrypted and encrypted!=plain
result['system_writes']=[]
for path in ('/etc/nvx-system-write','/usr/nvx-system-write'):
    try:open(path,'w').write('changed');result['system_writes'].append('writable')
    except OSError as e:result['system_writes'].append(e.errno)
result['mount']=c.mount(b'tmpfs',b'/tmp',b'tmpfs',0,None)
result['mount_errno']=ctypes.get_errno()
print(json.dumps(result))
"""
NAMESPACE = r"""
import ctypes,json,os
c=ctypes.CDLL(None,use_errno=True)
r=c.unshare(0x40000000)
print(json.dumps(dict(unshare=r,errno=ctypes.get_errno(),agent_visible=os.path.exists('/run/nvx'),control_visible=os.path.exists('/dev/hvc1'))))
"""


def assert_pair(family: str, protected: bool, control: bool) -> None:
    if not protected or control:
        raise ScriptError(
            f"{family}: expected CONTAINED and UNCONTAINED; got {protected}, {control}; a missing/failing negative control cannot pass"
        )


def guest_pair(
    backend: str, family: str, output: Path, timeout: float
) -> dict[str, Any]:
    script = {
        "identity": SECCOMP,
        "filesystem": FILESYSTEM,
        "process/namespace": NAMESPACE,
        "devices": DEVICES,
        "resources": RESOURCES,
    }[family]
    label = family.replace("/", "-")
    p = execute(
        backend,
        script,
        output,
        label + "-protected",
        timeout,
        profile="ci" if family == "devices" else None,
        extra=("--cap-add", "MKNOD") if family == "devices" else (),
    )
    c = execute(backend, script, output, label + "-control", timeout, profile="risky")
    if family in ("identity", "devices", "resources"):
        scenario = {
            "identity": "seccomp-profile",
            "devices": "device-policy",
            "resources": "resource-caps",
        }[family]
        validate(scenario, p, c)
        contained, control_contained = True, False
    elif family == "filesystem":
        contained = (
            p["scratch"] == "scratch"
            and p["loot_encrypted"]
            and all(value in (1, 13, 30) for value in p["system_writes"])
            and len(p["system_writes"]) == 2
            and p["mount"] == -1
            and p["mount_errno"] == 1
        )
        control_contained = not (
            c["scratch"] == "scratch"
            and c["loot_encrypted"]
            and c["system_writes"] == ["writable", "writable"]
            and c["mount"] == 0
        )
    else:
        contained = (
            p["unshare"] == -1
            and p["errno"] == 1
            and not p["agent_visible"]
            and not p["control_visible"]
        )
        control_contained = not (c["unshare"] == 0)
    assert_pair(family, contained, control_contained)
    return {"protected": p, "control": c}


class _Collector(http.server.BaseHTTPRequestHandler):
    requests: int = 0

    def do_GET(self) -> None:
        type(self).requests += 1
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"nvx-containment-collector")

    def log_message(self, format: str, *args: object) -> None:
        pass


def network_pair(backend: str, output: Path, timeout: float) -> dict[str, Any]:
    _Collector.requests = 0
    server = http.server.HTTPServer(("127.0.0.1", 0), _Collector)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    script = f"""
import json,socket,time
s=socket.socket();s.settimeout(3);start=time.monotonic()
try:
    s.connect(('192.168.127.1',{server.server_port}));s.sendall(b'GET / HTTP/1.0\\r\\nHost: nvx\\r\\n\\r\\n');body=b''
    while True:
        chunk=s.recv(4096)
        if not chunk:break
        body+=chunk
    verdict='connected' if b'nvx-containment-collector' in body else 'bad-response'
except OSError:verdict='denied'
finally:s.close()
print(json.dumps(dict(verdict=verdict,latency_ms=(time.monotonic()-start)*1000)))
"""
    try:
        p = execute(
            backend,
            script,
            output,
            "network-protected",
            timeout,
            profile=None,
        )
        p_requests = _Collector.requests
        c = execute(
            backend,
            script,
            output,
            "network-control",
            timeout,
            profile="risky",
            extra=("--host-loopback", "allow"),
        )
        assert_pair(
            "network",
            p["verdict"] == "denied" and p_requests == 0,
            c["verdict"] != "connected" or int(_Collector.requests) != 1,
        )
        return {"protected": p, "control": c, "collector_requests": _Collector.requests}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _varint(value: int) -> bytes:
    output = bytearray()
    while value > 127:
        output.append((value & 127) | 128)
        value >>= 7
    output.append(value)
    return bytes(output)


def _field(number: int, value: int | bytes) -> bytes:
    if isinstance(value, int):
        return _varint(number << 3) + _varint(value)
    return _varint(number << 3 | 2) + _varint(len(value)) + value


def _decode_fields(raw: bytes) -> list[tuple[int, int | bytes]]:
    def varint(offset: int) -> tuple[int, int]:
        value = shift = 0
        while offset < len(raw) and shift < 70:
            byte = raw[offset]
            offset += 1
            value |= (byte & 127) << shift
            if byte < 128:
                return value, offset
            shift += 7
        raise ScriptError("invalid snapshot protobuf")

    fields: list[tuple[int, int | bytes]] = []
    offset = 0
    while offset < len(raw):
        key, offset = varint(offset)
        wire = key & 7
        if wire == 0:
            value, offset = varint(offset)
        elif wire == 2:
            length, offset = varint(offset)
            value = raw[offset : offset + length]
            offset += length
        else:
            raise ScriptError("unexpected snapshot protobuf wire type")
        fields.append((key >> 3, value))
    return fields


def snapshot_pair(backend: str, output: Path, timeout: float) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="nvx-integrity-", dir=output) as directory:
        root = Path(directory)
        snapshot = root / "snapshot"
        command = [
            sys.executable,
            str(BuildConstants.REPO_ROOT / "scripts/nvx.py"),
            "run",
            "--hypervisor",
            backend,
            "--memory-mib",
            "512",
            "--virtio-net",
            "consomme:192.168.127.0/24,egress=deny",
            "--memory-backing-file",
            str(root / "ram"),
            "--save-snapshot",
            str(snapshot),
            "--save-on",
            "NVX-GUEST-BOOT-OK",
            "--save-timeout",
            str(timeout),
            "--cmdline",
            "quiet loglevel=0",
        ]
        if platform.machine().lower() in ("aarch64", "arm64"):
            with (output / "snapshot-capture.log").open("wb") as log:
                subprocess.run(
                    command,
                    check=True,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    timeout=timeout * 6,
                )
        else:
            from .microvm_tests import capture_integrity_snapshot

            capture_integrity_snapshot(
                backend, snapshot, output / "snapshot-capture.log", timeout
            )
        report = verify_snapshot(snapshot)
        if report.version < 6:
            raise ScriptError("snapshot writer did not publish integrity format v6")
        protected: dict[str, Any] = {}
        for filename in ("memory.bin", "state.bin"):
            path = snapshot / filename
            with path.open("r+b") as file:
                file.seek(-1, 2)
                byte = file.read(1)
                file.seek(-1, 2)
                file.write(bytes([byte[0] ^ 1]))
            try:
                verify_snapshot(snapshot)
                protected[filename] = "admitted"
            except ScriptError:
                protected[filename] = "rejected"
            with path.open("r+b") as file:
                file.seek(-1, 2)
                file.write(byte)
        try:
            verify_snapshot(
                snapshot,
                expected_arch="x86_64"
                if report.architecture == "aarch64"
                else "aarch64",
            )
            protected["foreign_arch"] = "admitted"
        except ScriptError:
            protected["foreign_arch"] = "rejected"
        manifest = snapshot / "manifest.bin"
        fields = _decode_fields(manifest.read_bytes())
        with (snapshot / "memory.bin").open("r+b") as file:
            file.seek(-1, 2)
            byte = file.read(1)
            file.seek(-1, 2)
            file.write(bytes([byte[0] ^ 1]))
        # The same corrupted RAM is accepted by the retained legacy structural
        # admission contract. This explicit downgrade is a test control only.
        legacy = [
            (n, 5 if n == 1 else b"OPENVMM_SNAPSHOT_V5\0" if n == 12 else v)
            for n, v in fields
            if n not in (9, 10)
        ]
        manifest.write_bytes(b"".join(_field(n, v) for n, v in legacy))
        control = verify_snapshot(snapshot)
        assert_pair(
            "snapshot integrity",
            all(v == "rejected" for v in protected.values()),
            control.version != 5,
        )
        return {
            "protected": protected,
            "control": {"tampered_memory": "admitted", "version": control.version},
            "scope": "CLI and core v6 payload admission; retained v5 lacks payload hashes",
        }


def tenant_pair(backend: str, output: Path, timeout: float) -> dict[str, Any]:
    cache = ImageCache()
    digest, manifest = cache.resolve("python:3.12-slim")
    layers = tuple(
        SandboxLayer(row["role"], cache.verify_blob(row["digest"]), row["uuid"])
        for row in manifest["layers"]
    )
    nonce = secrets.token_hex(16)
    owners = [secrets.token_hex(16) for _ in range(2)]
    scratches = [cache.acquire(owner, digest) for owner in owners]

    def probe(scratch: Path, profile: str, write: bool, label: str) -> bytes:
        state = output / label
        launch = SandboxLaunch(
            layers,
            scratch,
            profile=profile,
            seccomp="unconfined" if profile == "risky" else "nvx-default",
            workload_identity=(0, 0) if profile == "risky" else (65534, 65534),
            caps=("ALL",) if profile == "risky" else (),
            device_filter=profile != "risky",
            masked_paths=profile != "risky",
            no_new_privs=profile != "risky",
        )
        sandbox_lifecycle.provision(
            state,
            launch,
            hypervisor=backend,
            memory_mib=512,
            net=None,
            network_profile=None,
            network_egress="deny",
            network_ingress="deny",
            network_egress_allow=(),
            network_egress_deny=(),
            host_loopback=None,
            network_proxy=None,
            host_loopback_forward=(),
            cmdline="quiet loglevel=0",
        )
        sandbox_lifecycle.start(state, timeout)
        try:
            script = (
                f"open('/tmp/nvx-tenant','w').write('{nonce}');print('written')"
                if write
                else "import os;print(open('/tmp/nvx-tenant').read() if os.path.exists('/tmp/nvx-tenant') else 'absent')"
            )
            result = sandbox_lifecycle.exec_workload(
                state,
                ("/usr/local/bin/python", "-c", script),
                timeout_ms=10000,
                response_timeout=timeout,
            )
            if result.returncode:
                raise ScriptError("tenant probe failed")
            return result.stdout.strip()
        finally:
            sandbox_lifecycle.stop(state, timeout)

    output.mkdir(parents=True, exist_ok=True)
    try:
        probe(scratches[0], "default", True, "tenant-first")
        p = probe(scratches[1], "default", False, "tenant-private").decode()
        c = probe(scratches[0], "risky", False, "tenant-shared-control").decode()
        assert_pair("tenant isolation", p == "absent", c != nonce)
        return {
            "protected": {"previous_write": p},
            "control": {"previous_write": c},
            "scratch_distinct": scratches[0] != scratches[1],
        }
    finally:
        for owner in owners:
            cache.release(owner)


def render(document: dict[str, Any]) -> str:
    validate_document(document)
    lines = [
        "| Probe family | Backend | Protected | Negative control | Scope |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in document["results"]:
        lines.append(
            f"| {row['family']} | {document['backend']} | {row['verdict']} | {row['control_verdict']} | {row['scope']} |"
        )
    return "\n".join(lines) + "\n"


def validate_document(document: dict[str, Any]) -> None:
    if document.get("containment_version") != 1 or document.get("backend") not in (
        "kvm",
        "mshv",
        "whp",
        "hvf",
    ):
        raise ScriptError("unsupported containment document")
    rows = cast(list[dict[str, Any]], document.get("results", []))
    if {row.get("family") for row in rows} != {probe.family for probe in PROBES} or len(
        rows
    ) != len(PROBES):
        raise ScriptError("containment document must record every probe exactly once")
    for row in rows:
        if (
            row.get("verdict") != "CONTAINED"
            or row.get("control_verdict") != "UNCONTAINED"
            or not row.get("evidence")
            or not row.get("scope")
        ):
            raise ScriptError(
                "containment result missing observed failing negative control or evidence"
            )


def run(backend: str, output: Path, timeout: float) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    for probe in PROBES:
        if probe.family == "network":
            evidence = network_pair(backend, output, timeout)
        elif probe.family == "snapshot integrity":
            evidence = snapshot_pair(backend, output / "snapshot", timeout)
        elif probe.family == "tenant isolation":
            evidence = tenant_pair(backend, output / "tenant", timeout)
        else:
            evidence = guest_pair(backend, probe.family, output, timeout)
        results.append(
            {
                **asdict(probe),
                "verdict": "CONTAINED",
                "control_verdict": "UNCONTAINED",
                "evidence": evidence,
            }
        )
        (output / "progress.json").write_text(
            json.dumps(results, sort_keys=True, indent=2) + "\n"
        )
    document: dict[str, Any] = {
        "containment_version": 1,
        "backend": backend,
        "results": results,
    }
    validate_document(document)
    (output / "containment.json").write_text(
        json.dumps(document, sort_keys=True, indent=2) + "\n"
    )
    return document


def run_showcase(backend: str, output: Path, timeout: float) -> None:
    """The four talk simulants, with no policy overrides on protected runs."""
    output.mkdir(parents=True, exist_ok=True)
    results: dict[str, Any] = {}
    for name, family in (
        ("ransomware", "filesystem"),
        ("identity", "identity"),
        ("fork bomb", "resources"),
        ("exfiltration", "network"),
    ):
        evidence = (
            network_pair(backend, output, timeout)
            if family == "network"
            else guest_pair(backend, family, output, timeout)
        )
        results[name] = {
            "verdict": "CONTAINED",
            "control_verdict": "UNCONTAINED",
            "evidence": evidence,
        }
    (output / "showcase-simulants.json").write_text(
        json.dumps({"backend": backend, "results": results}, sort_keys=True, indent=2)
        + "\n"
    )
    print("NVX-SHOWCASE-SIMULANTS-OK")


def command(args: argparse.Namespace) -> None:
    if args.containment_operation == "render":
        document = cast(dict[str, Any], json.loads(args.input.read_bytes()))
    else:
        with contextlib.redirect_stdout(io.StringIO()):
            document = run(args.backend, args.output_dir, args.timeout)
    if args.format == "md":
        print(render(document), end="")
    else:
        print(json.dumps(document, sort_keys=True, indent=2))


def configure_parser(parser: argparse.ArgumentParser) -> None:
    commands = parser.add_subparsers(dest="containment_operation", required=True)
    for name in ("run", "render"):
        child = commands.add_parser(name)
        child.add_argument("--format", choices=("md", "json"), default="json")
        if name == "run":
            child.add_argument(
                "--backend",
                choices=("kvm", "mshv", "whp", "hvf"),
                default="hvf"
                if sys.platform == "darwin"
                else "whp"
                if sys.platform == "win32"
                else "kvm",
            )
            child.add_argument(
                "--output-dir",
                type=Path,
                default=Path("build/test-results/containment"),
            )
            child.add_argument("--timeout", type=float, default=60)
        else:
            child.add_argument("input", type=Path)
        child.set_defaults(handler=command)

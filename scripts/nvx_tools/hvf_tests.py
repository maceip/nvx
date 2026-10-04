"""Marker-driven ARM sandbox parity gate; all fixtures are built from this tree."""

from __future__ import annotations

import argparse
import base64
import http.server
import json
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from . import sandbox_lifecycle
from .build_constants import BuildConstants
from .common import (
    ScriptError,
    artifact_path,
    openvmm_binary_path,
    require_tool,
    sha256_file,
)
from .openvmm_process import OpenvmmProcess
from .sandbox import SandboxLaunch, SandboxLayer

HVF_SCENARIOS = (
    "guest-boot",
    "sandbox-blocks",
    "structured-outcome",
    "managed-lifecycle",
    "workload-identity",
    "hvf-parity",
    "image-run",
    "receipt-flow-log",
    "containment-battery",
    "seccomp-profile",
    "egress-fast-fail",
    "resource-caps",
    "device-policy",
    "showcase-simulants",
    "workspace-lifecycle",
    "secret-isolation",
    "warm-clone",
    "mcp-portable",
)
FIXTURE_UUID = "11111111-1111-1111-1111-111111111111"


class _CollectorHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"nvx-test-collector\n")

    def log_message(self, format: str, *args: object) -> None:
        pass


class _TLSCollector:
    def __init__(self, directory: Path):
        self.cert = directory / "cert.pem"
        key = directory / "key.pem"
        subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-keyout",
                str(key),
                "-out",
                str(self.cert),
                "-days",
                "1",
                "-subj",
                "/CN=nvx-hvf",
                "-addext",
                "subjectAltName=IP:192.168.127.1",
            ],
            check=True,
            capture_output=True,
        )
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self.cert, key)
        self.server = http.server.HTTPServer(("127.0.0.1", 0), _CollectorHandler)
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


def _stage_tls(
    process: OpenvmmProcess, collector: _TLSCollector, timeout: float
) -> None:
    cert = base64.b64encode(collector.cert.read_bytes()).decode()
    node = (
        "const https=require('https'),cp=require('child_process');const finish=ok=>{console.log('NVX-HVF-TLS-'+(ok?'OK':'FAIL'));cp.execFileSync('/sbin/nvx-exit',['0']);};"
        + f"https.get({{host:'192.168.127.1',port:{collector.server.server_port},ca:Buffer.from('{cert}','base64')}},r=>{{r.resume();r.on('end',()=>finish(r.statusCode===200));}}).on('error',e=>{{console.log('NVX-TLS-ERROR:'+e.code+':'+e.message);finish(false);}});"
    )
    encoded = base64.b64encode(node.encode()).decode()
    process.send_line("cat >/tmp/nvx-tls.b64 <<'NVX_TLS_END'")
    for offset in range(0, len(encoded), 76):
        process.send_line(encoded[offset : offset + 76])
    process.send_line("NVX_TLS_END")
    process.send_line(
        "base64 -d /tmp/nvx-tls.b64 >/tmp/nvx-tls.js; node /tmp/nvx-tls.js"
    )


def _restore_clock(
    output: Path,
    memory: int,
    timeout: float,
    collector: _TLSCollector,
    backend: str = "hvf",
) -> None:
    spec = f"consomme:192.168.127.0/24,gwloopback,egress=deny,egress-allow=192.168.127.1:tcp:{collector.server.server_port}"
    for watch in (True, False):
        name = "sample" if watch else "off"
        with tempfile.TemporaryDirectory(
            prefix="clock-restore-", dir=output
        ) as temporary:
            work = Path(temporary)
            snapshot = work / "snapshot"
            # Force a stale saved clock, independent of host load or pause duration.
            cmdline = "quiet loglevel=0 virtnet_ip=192.168.127.2 virtnet_mask=255.255.255.0 virtnet_gw=192.168.127.1"
            if not watch:
                cmdline += " nvx_clock_watch=off"
            capture = [
                sys.executable,
                str(BuildConstants.REPO_ROOT / "scripts/nvx.py"),
                "run",
                "--hypervisor",
                backend,
                "--memory-mib",
                str(memory),
                "--memory-backing-file",
                str(work / "ram"),
                "--virtio-net",
                spec,
                "--save-snapshot",
                str(snapshot),
                "--save-timeout",
                str(timeout),
                "--save-on",
                "NVX-CLOCK-WATCH-READY" if watch else "NVX-GUEST-BOOT-OK",
                "--save-exec",
                "stty -echo; export PS1= PS2=; date -u -s @946684800 >/dev/null; printf 'NVX-HVF-%s\\n' STALE-CAPTURE-READY",
                "--save-ready",
                "NVX-HVF-STALE-CAPTURE-READY",
                "--cmdline",
                cmdline,
            ]
            with (output / f"clock-save-{name}.log").open("wb") as log:
                subprocess.run(
                    capture,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    check=True,
                    timeout=timeout * 4,
                )
            command = [
                str(openvmm_binary_path()),
                "--single-process",
                "--hypervisor",
                backend,
                "--com1",
                "console",
                "--restore-snapshot",
                str(snapshot.resolve()),
                "--virtio-net",
                spec + ",snapshot",
            ]
            with OpenvmmProcess(
                command, output / f"clock-restore-{name}.log"
            ) as process:
                if watch:
                    process.wait_for(b"NVX-CLOCK-RESTORE-DETECTED", timeout)
                    process.wait_for(b"NVX-CLOCK-SYNC-OK:", timeout)
                process.send_line("printf 'NVX-HVF-%s\\n' RESTORE-SHELL-READY")
                process.wait_for_line(b"NVX-HVF-RESTORE-SHELL-READY", timeout)
                expected = int(time.time())
                process.send_line(
                    f"now=$(date +%s); if [ $now -ge {expected - 1} ] && [ $now -le {expected + 1} ]; then echo NVX-HVF-RESTORE-CLOCK-OK; else echo NVX-HVF-RESTORE-CLOCK-FAIL; fi"
                )
                _stage_tls(process, collector, timeout)
                result = process.wait(timeout)
            lines = result.output.replace(b"\r", b"").splitlines()
            expected_clock = (
                b"NVX-HVF-RESTORE-CLOCK-OK" if watch else b"NVX-HVF-RESTORE-CLOCK-FAIL"
            )
            expected_tls = b"NVX-HVF-TLS-OK" if watch else b"NVX-HVF-TLS-FAIL"
            if (
                result.returncode
                or expected_clock not in lines
                or expected_tls not in lines
            ):
                raise ScriptError(
                    f"HVF restore clock {name} did not produce the expected clock/TLS verdict"
                )
            if not watch and not any(b"CERT_NOT_YET_VALID" in line for line in lines):
                raise ScriptError(
                    "restore negative control did not fail certificate validity"
                )
    print("NVX-HVF-RESTORE-CLOCK-OK")


def _fixture(output_dir: Path) -> tuple[Path, Path]:
    from .build import initramfs_provenance_inputs

    archive = artifact_path("initramfs.cpio.gz")
    try:
        provenance = json.loads(artifact_path("initramfs.provenance.json").read_bytes())
        if (
            provenance["initramfs_sha256"] != sha256_file(archive)
            or provenance["inputs"] != initramfs_provenance_inputs()
        ):
            raise ScriptError("scenario fixture requires a current verified initramfs")
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ScriptError(
            "scenario fixture requires a current verified initramfs"
        ) from error
    docker = require_tool(
        "docker", "scenario fixture conversion requires a Linux Docker engine"
    )
    layer = output_dir / "distro.erofs"
    scratch = output_dir / "scratch-template.ext4"
    with (output_dir / "fixture-build.log").open("wb") as log:
        subprocess.run(
            [
                docker,
                "build",
                "--target",
                "fixture",
                "-t",
                "nvx-hvf-test-fixture",
                "-f",
                str(BuildConstants.REPO_ROOT / "docker/Dockerfile"),
                str(BuildConstants.REPO_ROOT),
            ],
            check=True,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        subprocess.run(
            [
                docker,
                "run",
                "--rm",
                "--network",
                "none",
                "-v",
                f"{output_dir.resolve()}:/out",
                "-v",
                f"{archive.resolve()}:/in/initramfs.cpio.gz:ro",
                "nvx-hvf-test-fixture",
                "sh",
                "-ec",
                "gzip -dc /in/initramfs.cpio.gz > /tmp/fixture.cpio; "
                "mkdir /tmp/fixture-root; "
                "cd /tmp/fixture-root; cpio -idm --no-absolute-filenames < /tmp/fixture.cpio; "
                "mkfs.erofs -x-1 -T0 -U "
                + FIXTURE_UUID
                + " /out/distro.erofs /tmp/fixture-root; "
                "truncate -s 128M /out/scratch-template.ext4; mke2fs -q -t ext4 -F /out/scratch-template.ext4",
            ],
            check=True,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    (output_dir / "fixture-provenance.json").write_text(
        json.dumps(
            {
                "initramfs_sha256": provenance["initramfs_sha256"],
                "distro_erofs_sha256": sha256_file(layer),
                "scratch_template_sha256": sha256_file(scratch),
            },
            indent=2,
        )
        + "\n"
    )
    return layer, scratch


def _provision(
    state: Path, launch: SandboxLaunch, memory: int, backend: str = "hvf"
) -> None:
    sandbox_lifecycle.provision(
        state,
        launch,
        hypervisor=backend,
        memory_mib=memory,
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


def run(args: argparse.Namespace) -> int:
    scenarios = tuple(dict.fromkeys(args.scenario or HVF_SCENARIOS))
    unknown = set(scenarios) - set(HVF_SCENARIOS)
    if unknown:
        raise ScriptError(
            "HVF parity gate does not cover: " + ", ".join(sorted(unknown))
        )
    output: Path = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    memory = args.memory_mib or 1024
    if "guest-boot" in scenarios or "hvf-parity" in scenarios:
        with tempfile.TemporaryDirectory(prefix="nvx-hvf-tls-") as temporary:
            collector = _TLSCollector(Path(temporary))
            try:
                for clock in (
                    ("sample", "off") if "hvf-parity" in scenarios else ("sample",)
                ):
                    port = collector.server.server_port
                    command = [
                        str(openvmm_binary_path()),
                        "--single-process",
                        "--hypervisor",
                        args.backend,
                        "--com1",
                        "console",
                        "--memory",
                        f"{memory}M",
                        "--kernel",
                        str(artifact_path("Image")),
                        "--initrd",
                        str(artifact_path("initramfs.cpio.gz")),
                        "--virtio-net",
                        f"consomme:192.168.127.0/24,gwloopback,egress=deny,egress-allow=192.168.127.1:tcp:{port}",
                        "--cmdline",
                        f"quiet loglevel=0 nvx_host_clock={clock} virtnet_ip=192.168.127.2 virtnet_mask=255.255.255.0 virtnet_gw=192.168.127.1",
                    ]
                    with OpenvmmProcess(
                        command,
                        output / f"clock-{clock}.log",
                    ) as process:
                        process.wait_for(b"NVX-GUEST-BOOT-OK: alpine", args.timeout)
                        # PID 1 prints its boot marker before starting the shell.
                        # Wait for shell execution, and disable line editing/echo
                        # before staging a script across the bounded UART input.
                        process.send_line(
                            "stty -echo; export PS1='' PS2=''; echo NVX-HVF-SHELL-READY"
                        )
                        process.wait_for_line(b"NVX-HVF-SHELL-READY", args.timeout)
                        expected = int(time.time())
                        process.send_line(
                            f"now=$(date -u +%s); echo NVX-HVF-CLOCK:$now; if [ $now -ge {expected - 1} ] && [ $now -le {expected + 1} ]; then echo NVX-HVF-CLOCK-FRESH-OK; else echo NVX-HVF-CLOCK-FRESH-FAIL; fi"
                        )
                        _stage_tls(process, collector, args.timeout)
                        result = process.wait(args.timeout)
                    if result.returncode != 0:
                        raise ScriptError("HVF guest-initiated exit failed")
                    lines = result.output.replace(b"\r", b"").splitlines()
                    fresh = b"NVX-HVF-CLOCK-FRESH-OK" in lines
                    tls = b"NVX-HVF-TLS-OK" in lines
                    if clock == "off" and (
                        b"NVX-HVF-CLOCK-FRESH-FAIL" not in lines
                        or b"NVX-HVF-TLS-FAIL" not in lines
                        or not any(b"CERT_NOT_YET_VALID" in line for line in lines)
                    ):
                        raise ScriptError(
                            "clock negative control did not reject the current certificate"
                        )
                    if fresh != (clock == "sample") or tls != (clock == "sample"):
                        raise ScriptError(
                            f"HVF clock {clock} produced the wrong freshness/TLS verdict"
                        )
                if "hvf-parity" in scenarios:
                    _restore_clock(
                        output, memory, args.timeout, collector, args.backend
                    )
            finally:
                collector.close()
        print(
            "NVX-HVF-PARITY-OK"
            if "hvf-parity" in scenarios
            else "NVX-GUEST-BOOT-CHECK-OK"
        )
    from .policy_tests import SCENARIOS as policy_scenarios
    from .policy_tests import run as run_policy_tests

    selected_policy = tuple(name for name in scenarios if name in policy_scenarios)
    if selected_policy:
        run_policy_tests(args.backend, selected_policy, output / "policy", args.timeout)
    if "containment-battery" in scenarios:
        from .containment import run as run_containment

        run_containment(args.backend, output / "containment", args.timeout)
        print("NVX-CONTAINMENT-BATTERY-OK")
    if "showcase-simulants" in scenarios:
        from .containment import run_showcase

        run_showcase(args.backend, output / "showcase", args.timeout)
    if "receipt-flow-log" in scenarios:
        from .receipt_tests import run as run_receipt_tests

        run_receipt_tests(args.backend, output / "receipts", args.timeout)
    if "image-run" in scenarios:
        from .image_tests import run as run_images

        run_images(args.backend, output / "images", args.timeout)
    if "workspace-lifecycle" in scenarios:
        from .workspace_tests import run as run_workspace

        run_workspace(args.backend, output / "workspace", args.timeout)
    if "secret-isolation" in scenarios:
        from .secret_tests import run as run_secrets

        run_secrets(args.backend, output / "secrets", args.timeout)
    if "warm-clone" in scenarios:
        from .warm_tests import run as run_warm

        run_warm(args.backend, output / "warm", args.timeout)
    if "mcp-portable" in scenarios:
        from .mcp_tests import run as run_mcp

        run_mcp(args.backend, output / "mcp", args.timeout)
    sandbox_scenarios = set(scenarios) - {
        "guest-boot",
        "hvf-parity",
        "image-run",
        "receipt-flow-log",
        "containment-battery",
        "showcase-simulants",
        "workspace-lifecycle",
        "secret-isolation",
        "warm-clone",
        "mcp-portable",
        *policy_scenarios,
    }
    if sandbox_scenarios:
        layer, template = _fixture(output)
        for scenario in sorted(sandbox_scenarios):
            scratch = output / f"{scenario}.ext4"
            shutil.copyfile(template, scratch)
            with tempfile.TemporaryDirectory(prefix="nvx-hvf-") as temporary:
                state = Path(temporary) / "state"
                launch = SandboxLaunch(
                    (SandboxLayer("custom", layer, FIXTURE_UUID),), scratch
                )
                _provision(state, launch, memory, args.backend)
                started = False
                try:
                    sandbox_lifecycle.start(state, args.timeout)
                    started = True
                    if scenario == "workload-identity":
                        argv = ("/sbin/nvx-identity-probe",)
                    elif scenario == "sandbox-blocks":
                        argv = (
                            "/bin/sh",
                            "-c",
                            "set -eu; test ! -e /dev/vda; test ! -e /dev/mem; test ! -e /dev/port; test ! -e /run/nvx; test ! -e /sys/fs/cgroup/agent; echo writable >/tmp/nvx-overlay; test $(cat /tmp/nvx-overlay) = writable; echo NVX-SANDBOX-BLOCKS-OK",
                        )
                    else:
                        argv = (
                            "/bin/sh",
                            "-c",
                            "printf stdout-value; printf stderr-value >&2; exit 37",
                        )
                    result = sandbox_lifecycle.exec_workload(
                        state, argv, timeout_ms=10_000, response_timeout=args.timeout
                    )
                    if scenario in ("managed-lifecycle", "structured-outcome"):
                        if (result.stdout, result.stderr, result.returncode) != (
                            b"stdout-value",
                            b"stderr-value",
                            37,
                        ):
                            raise ScriptError(
                                "HVF exec did not preserve separate streams and status 37"
                            )
                    elif result.returncode != 0:
                        raise ScriptError(f"HVF {scenario} probe failed")
                    elif (
                        scenario == "workload-identity"
                        and b"NVX-WORKLOAD-IDENTITY-OK uid=65534 gid=65534"
                        not in result.stdout
                    ):
                        raise ScriptError("workload identity marker missing")
                    elif (
                        scenario == "sandbox-blocks"
                        and b"NVX-SANDBOX-BLOCKS-OK" not in result.stdout
                    ):
                        raise ScriptError("sandbox blocks marker missing")
                    outcome = sandbox_lifecycle.stop(state, args.timeout)
                    started = False
                    if outcome["outcome"] != {
                        "operation": "managed",
                        "category": "success",
                        "status_code": 0,
                    }:
                        raise ScriptError("HVF structured teardown outcome is invalid")
                    (output / f"{scenario}.json").write_text(
                        json.dumps(outcome, indent=2) + "\n"
                    )
                    print(f"NVX-{scenario.upper()}-OK")
                finally:
                    if started:
                        sandbox_lifecycle.stop(state, args.timeout)
                    if (state / "openvmm.log").exists():
                        shutil.copyfile(
                            state / "openvmm.log", output / f"{scenario}.log"
                        )
                    if state.exists():
                        sandbox_lifecycle.deprovision(state)
    return 0

# NVX cookbook

Run commands from the checkout root; use the same architecture for the kernel,
initramfs, OpenVMM, image, and snapshot. Diagnostics are the first recipe:

```sh
python3 scripts/nvx.py setup                 # local macOS entitlement
python3 scripts/nvx.py doctor --backend hvf
```

For a published signed runtime, install without a source checkout:

```sh
curl -fsSL https://raw.githubusercontent.com/maceip/nvx/dev/scripts/install-nvx.py | python3 -
```

Python 3.10+ and GitHub CLI must be installed. The installer uses public downloads and
offline signature bundles, so GitHub login is unnecessary. It refuses unsupported hosts,
existing destinations and development archives. Add its printed `bin` directory to `PATH`;
then use `nvx doctor` and `nvx run --image python:3.12-slim -- python -c 'print(1)'`.
Publication availability is tracked in the [acceptance ledger](implementation-status.md).

To run a prebuilt EROFS layer on Apple Silicon, supply its filesystem UUID and
an unused, formatted ext4 scratch image. The workload starts as uid/gid 65534:

```sh
python3 scripts/nvx.py sandbox --hypervisor hvf \
  --layer custom,/absolute/root.erofs,11111111-1111-1111-1111-111111111111 \
  --scratch /absolute/private.ext4 --entrypoint /usr/bin/id
```

For repeated executions, provision once, start, execute, stop, then deprovision.
Each execution has separate stdout/stderr and a numeric exit status:

```sh
python3 scripts/nvx.py sandbox provision --hypervisor hvf --state-dir ./build/example \
  --layer custom,/absolute/root.erofs,11111111-1111-1111-1111-111111111111 \
  --scratch /absolute/private.ext4
python3 scripts/nvx.py sandbox start --state-dir ./build/example
python3 scripts/nvx.py sandbox exec --state-dir ./build/example --entrypoint /usr/bin/id
python3 scripts/nvx.py explain ./build/example
python3 scripts/nvx.py sandbox stop --state-dir ./build/example
python3 scripts/nvx.py sandbox deprovision --state-dir ./build/example
```

To reproduce the ARM parity gate including its clock negative control:

```sh
python3 scripts/nvx.py test-microvm --backend hvf \
  --output-dir build/test-results/microvm-hvf
```

The logs and structured outcomes in that directory are the evidence to attach
to a manual pre-release check. See [CI](ci.md) for runner configuration.

## Run Python from an OCI image

```sh
python3 scripts/nvx.py image pull python:3.12-slim
python3 scripts/nvx.py run --image python:3.12-slim -- python -c 'print(1)'
```

The default policy denies egress and caps the workload. No layer UUIDs or scratch
formatting commands are needed. The printed `NVX-ID` locates retained evidence under
`$NVX_IMAGE_CACHE/instances/ID` (default `~/.nvx/cache/instances/ID`).

## Register a curated base

```sh
python3 scripts/nvx.py image pull alpine:3.20 --curated-base
python3 scripts/nvx.py image convert my-derived-image
python3 scripts/nvx.py image verify my-derived-image
```

Only an exact ordered prefix of OCI layer digests shares the base. Other images flatten
to one custom layer. Conversion handles OCI deletions before building the read-only
filesystem and rejects imported overlay-control attributes.

## Verify image conversion determinism

```sh
python3 scripts/nvx.py verify-guest-determinism \
  --image alpine:3.20 --image python:3.12-slim \
  --work-dir build/oci-determinism
```

Both images must already exist in Docker. The command records actual converter digests
and fails on any difference, including scratch-template bytes.

## Give a CI workload one network destination

```sh
python3 scripts/nvx.py run --image python:3.12-slim --profile ci \
  --network-egress-allow 203.0.113.9:tcp:443 -- python job.py
```

Replace the documentation address with the job's actual destination and arrange its
input separately. A deny rule takes precedence; `default` refuses contradictory allow
rules. `nvx policy show` prints the complete resolved profile before a run.

## Bound a suspicious workload

```sh
python3 scripts/nvx.py run --image python:3.12-slim \
  --pids-max 32 --memory-max 67108864 --exec-timeout-ms 10000 \
  -- python -c 'while True: pass'
```

Timeout is reported as status 124 and a resource-denial event. Process and memory limits
are applied to the workload cgroup; the outer agent remains available to stop the VM.

## Inspect and verify a run receipt

```sh
python3 scripts/nvx.py run --image python:3.12-slim --receipt build/run-receipt.json \
  -- python -c 'print(1)'
python3 scripts/nvx.py receipt verify build/run-receipt.json \
  --evidence-dir "$HOME/.nvx/cache/instances/REPLACE_WITH_NVX_ID"
```

The output path must be new. Verification checks both the canonical body digest and
its referenced host evidence. For signed receipts, also provide the trusted public key.

## Reproduce the containment matrix

```sh
python3 scripts/nvx.py containment run --backend hvf \
  --output-dir build/containment --format md > build/containment-matrix.md
python3 scripts/nvx.py containment render build/containment/containment.json --format md
```

Use `kvm`, `mshv` or `whp` on the corresponding host. A control that stays contained
fails the run. Scope is printed in each row; this is not a claim about kernel CVEs,
side channels, cross-hypervisor restore, or hostile administrators of the host.

## Exercise all four showcase simulants under default policy

```sh
python3 scripts/nvx.py test-microvm --backend hvf --scenario showcase-simulants --output-dir build/showcase-proof
```

The ransomware fixture encrypts its own scratch loot while two system writes fail.
Identity, bounded fork spawning, and an attempted exfiltration complete the gate. Every
case repeats with the risky control and requires an uncontained result. The protected
commands supply no policy overrides.


## Run a workspace job and collect its outputs

```sh
python3 scripts/nvx.py run --image alpine:3.20 --workspace ./src:/src:ro --out ./artifacts -- /bin/sh -c 'mkdir -p /out; cp /src/result.txt /out/result.txt'
```

Use `:rw` when the job must update the input directory. The output directory must not exist.

## Inspect a retained instance and export evidence

```sh
python3 scripts/nvx.py run --image alpine:3.20 --keep-alive -- /bin/echo ready
python3 scripts/nvx.py exec REPLACE_WITH_NVX_ID -- /bin/sh -c 'echo stdout;echo stderr >&2;exit 37'
python3 scripts/nvx.py cp ./input.bin REPLACE_WITH_NVX_ID:/tmp/input.bin
python3 scripts/nvx.py logs REPLACE_WITH_NVX_ID --json
python3 scripts/nvx.py stop REPLACE_WITH_NVX_ID
python3 scripts/nvx.py bundle REPLACE_WITH_NVX_ID --output build/evidence.tar.gz
python3 scripts/nvx.py bundle verify build/evidence.tar.gz
```

## Keep an API credential on the host

Set `SERVICE_KEY` in the host environment using your credential manager, then bind an exact
TLS destination. The guest uses the disposable `NVX_PROXY_URL` and `NVX_PROXY_CAPABILITY`
provided by NVX; the proxy injects the API credential after validating the destination.

```sh
python3 scripts/nvx.py run --image python:3.12-slim --secret SERVICE_KEY --egress-allow api.example.com:443 --proxy-log build/proxy.jsonl -- python /app/job.py
python3 scripts/nvx.py test-microvm --backend hvf --scenario secret-isolation --output-dir build/secret-proof
```

The example job must already be present in the image or supplied through a workspace.

## Prepare a Python pool and measure it

```sh
python3 scripts/nvx.py warm --image python:3.12-slim --backend hvf --output build/python-warm
python3 scripts/nvx.py pool start --template build/python-warm --size 2
python3 scripts/nvx.py run --pool REPLACE_WITH_POOL_ID -- python -c 'print(42)'
python3 scripts/nvx.py pool stop REPLACE_WITH_POOL_ID
python3 scripts/nvx.py benchmark --suite warm-pool --backend hvf --template build/python-warm --runs 20 --platform darwin-arm64-hvf --output build/warm-benchmark.json
python3 scripts/nvx.py performance collect-warm --platform darwin-arm64-hvf --commit LOCAL_REVISION --input build/warm-benchmark.json --output-dir build/warm-performance
python3 scripts/nvx.py performance gate --baseline-dir data/warm --target-dir build/warm-performance --minimum-history 1 --threshold 20 --absolute-tolerance-ms 1
python3 scripts/nvx.py performance gate --baseline-dir data/warm --target-dir build/warm-performance --minimum-history 10 --threshold 20 --absolute-tolerance-ms 1
```

Use the matching backend on Linux or Windows. The template is architecture/backend bound.
The first command is a bootstrap guard, not a trend: the baseline currently has one
point. The second reports Warmup until ten independent points exist. Timing is client
request to first workload stdout against an already-resumed ready clone; preparation,
admission hashing and refill are excluded. It is not the older x86 bare-guest boot metric.

## Connect the Python MCP client

```sh
python3 scripts/nvx.py mcp serve --backend hvf --transport http --state-dir build/mcp
```

In another process, set `PYTHONPATH=sdk/python` and use the URL and capability-file path printed
by the server:

```python
from pathlib import Path
from nvx_sdk import Client
client = Client('http://127.0.0.1:REPLACE_WITH_PORT/mcp', Path('build/mcp/http.capability'))
client.initialize()
result = client.run('alpine:3.20', ['/bin/echo', 'hello'], handle='example-1',
                    timeout=30, output=lambda stream, data: print(stream, data))
print(result['returncode'])
```

`timeout`, `output` and `request_id` belong to the Python request rather than guest tool
arguments. Use a known `request_id` with `client.cancel(id)` for cancellation from another
thread. TypeScript exposes `startRun` and `startExec`, each returning `{id, result, cancel}`;
`run`/`exec` accept tool arguments followed by `{output, timeoutMs}` request options.

## Verify a self-contained local ARM package

```sh
python3 scripts/nvx.py package --platform darwin-arm64 --development --binary-only --destination build/nvx-preview
python3 scripts/nvx.py archive-release --source build/nvx-preview --destination build/nvx-preview.tar.gz
python3 scripts/nvx.py install --archive build/nvx-preview.tar.gz --destination build/nvx-installed
build/nvx-installed/bin/nvx doctor --backend hvf
build/nvx-installed/bin/nvx run --image alpine:3.20 -- /bin/echo NVX-GUEST-BOOT-OK
```

A development preview is not a signed/notarized public release. The ARM release workflow
requires configured signing identities and enrolled runners before it can pass.

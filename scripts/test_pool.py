import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from nvx_tools.common import ScriptError
from nvx_tools.pool import Accounting, start, stop
from nvx_tools.quota import Limits, Quota


class PoolTests(unittest.TestCase):
    def test_stop_waits_for_refill_and_vm_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "pools" / ("a" * 32) / "runtime.json"
            runtime.parent.mkdir(parents=True)
            runtime.write_text("{}")
            completed = threading.Event()

            def finish() -> None:
                time.sleep(0.15)
                runtime.unlink()
                completed.set()

            worker = threading.Thread(target=finish)
            with (
                patch("nvx_tools.pool.ImageCache") as cache,
                patch("nvx_tools.pool.request", return_value={"stopping": True}),
            ):
                cache.return_value.root = root
                worker.start()
                try:
                    self.assertEqual(stop("a" * 32), {"stopping": True})
                    self.assertTrue(completed.is_set())
                finally:
                    worker.join(timeout=5)

    def test_stop_reports_incomplete_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "pools" / ("a" * 32) / "runtime.json"
            runtime.parent.mkdir(parents=True)
            runtime.write_text("{}")
            with (
                patch("nvx_tools.pool.ImageCache") as cache,
                patch("nvx_tools.pool.request", return_value={"stopping": True}),
            ):
                cache.return_value.root = root
                with self.assertRaisesRegex(ScriptError, "cleanup timed out"):
                    stop("a" * 32, timeout=0.03)
                self.assertTrue(runtime.exists())

    def test_duplicate_identity_and_double_retire_are_rejected(self) -> None:
        with self.assertRaisesRegex(ScriptError, "duplicate"):
            Accounting([Path("left/a"), Path("right/a")])
        pool = Accounting([Path("left/a")])
        with self.assertRaisesRegex(ScriptError, "previously used"):
            pool.add(Path("right/a"))
        state = pool.lease()
        with self.assertRaises(ScriptError):
            pool.add(Path("right/a"))
        pool.retire(state)
        with self.assertRaises(ScriptError):
            pool.retire(state)
        with self.assertRaises(ScriptError):
            pool.add(Path("right/a"))
        self.assertEqual(pool.counts(), {"ready": 0, "leased": 0, "retired": 1})

    def test_concurrent_exhaustion_leases_each_clone_once(self) -> None:
        pool = Accounting([Path(str(i)) for i in range(8)])
        barrier = threading.Barrier(16)
        leased: list[Path] = []
        denied: list[bool] = []
        lock = threading.Lock()

        def worker() -> None:
            barrier.wait()
            try:
                state = pool.lease()
                with lock:
                    leased.append(state)
            except ScriptError:
                with lock:
                    denied.append(True)

        threads = [threading.Thread(target=worker) for _ in range(16)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual((len(set(leased)), len(denied)), (8, 8))
        self.assertEqual(pool.counts(), {"ready": 0, "leased": 8, "retired": 0})
        for state in leased:
            pool.retire(state)
        self.assertEqual(pool.counts(), {"ready": 0, "leased": 0, "retired": 8})

    def test_concurrent_quota_denials_do_not_consume_budget(self) -> None:
        quota = Quota(Limits(concurrent=4, aggregate_cpus=4, aggregate_memory_mib=1024))
        barrier = threading.Barrier(16)
        accepted: list[str] = []
        lock = threading.Lock()

        def worker(index: int) -> None:
            barrier.wait()
            try:
                quota.reserve(str(index), 256)
                with lock:
                    accepted.append(str(index))
            except ScriptError:
                pass

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(16)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(len(accepted), 4)
        self.assertEqual(
            quota.counts(),
            {"sandboxes": 4, "cpus": 4, "memory_mib": 1024, "snapshot_bytes": 0},
        )
        for handle in accepted:
            quota.release(handle)
        self.assertEqual(quota.counts()["memory_mib"], 0)
        quota.reserve("replacement", 1024)
        self.assertEqual(quota.counts()["sandboxes"], 1)

    def test_invalid_sizes_spawn_nothing(self) -> None:
        with patch("nvx_tools.pool.subprocess.Popen") as spawn:
            for size in (0, 33):
                with self.assertRaisesRegex(ScriptError, "size"):
                    start(Path("unused"), size)
            spawn.assert_not_called()

    def test_lease_retire_refuses_reuse(self) -> None:
        first, second = Path("a"), Path("b")
        pool = Accounting([first])
        self.assertEqual(pool.lease(), first)
        self.assertEqual(pool.counts(), {"ready": 0, "leased": 1, "retired": 0})
        with self.assertRaises(ScriptError):
            pool.lease()
        pool.retire(first)
        with self.assertRaises(ScriptError):
            pool.add(first)
        pool.add(second)
        self.assertEqual(pool.lease(), second)

    def test_quota_denial_spawns_nothing(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            patch(
                "nvx_tools.pool.warm.admit",
                return_value={"config": {"memory_mib": 512}},
            ),
            patch("nvx_tools.pool.subprocess.Popen") as spawn,
        ):
            with self.assertRaisesRegex(ScriptError, "quota"):
                start(Path(directory), 8, memory_budget_mib=1024)
            spawn.assert_not_called()

    def test_memory_cpu_wall_snapshot_and_idempotent_release(self) -> None:
        quota = Quota(Limits(concurrent=2, aggregate_cpus=2, aggregate_memory_mib=512))
        quota.reserve("first", 256)
        for memory, cpus, wall in ((1024, 1, 1), (128, 2, 1), (128, 1, 60001)):
            with self.assertRaises(ScriptError):
                quota.reserve("denied", memory, cpus, wall)
        quota.reserve("second", 256)
        with self.assertRaises(ScriptError):
            quota.reserve("third", 1)
        quota.release("first")
        quota.release("first")
        self.assertEqual(quota.counts()["sandboxes"], 1)
        quota.reserve_snapshot("snapshot", 4 << 30)
        with self.assertRaises(ScriptError):
            quota.reserve_snapshot("denied", 1)
        quota.release_snapshot("snapshot")
        self.assertEqual(quota.counts()["snapshot_bytes"], 0)

"""Regression coverage for hosted and fleet-backed CI admission."""

from __future__ import annotations

import unittest

from nvx_tools.ci import (
    REQUIRED_CI_HOSTED_JOBS,
    REQUIRED_CI_RESULT_ENVIRONMENTS,
    required_ci_expected_results,
    required_ci_failures,
)


class RequiredCiTests(unittest.TestCase):
    def test_every_mode_requires_exact_results(self) -> None:
        for event in ("push", "pull_request"):
            for same_repository in (False, True):
                for run_tests in (False, True):
                    for run_workloads in (False, True):
                        for self_hosted in (False, True):
                            with self.subTest(
                                event=event,
                                same_repository=same_repository,
                                run_tests=run_tests,
                                run_workloads=run_workloads,
                                self_hosted=self_hosted,
                            ):
                                arguments = {
                                    "same_repository": same_repository,
                                    "run_tests": run_tests,
                                    "run_workloads": run_workloads,
                                    "self_hosted": self_hosted,
                                }
                                expected = required_ci_expected_results(
                                    event, **arguments
                                )
                                self.assertEqual(
                                    set(expected), set(REQUIRED_CI_RESULT_ENVIRONMENTS)
                                )
                                self.assertEqual(
                                    required_ci_failures(
                                        event, results=expected, **arguments
                                    ),
                                    [],
                                )
                                for job, result in expected.items():
                                    if result != "success":
                                        continue
                                    for bad_result in (
                                        "failure",
                                        "cancelled",
                                        "skipped",
                                        "",
                                    ):
                                        results = {**expected, job: bad_result}
                                        failures = required_ci_failures(
                                            event, results=results, **arguments
                                        )
                                        self.assertEqual(len(failures), 1)
                                        self.assertTrue(
                                            failures[0].startswith(job + ":")
                                        )

    def test_fork_requires_hosted_acceptance_instead_of_unavailable_fleet(self) -> None:
        expected = required_ci_expected_results(
            "push",
            same_repository=True,
            run_tests=True,
            run_workloads=True,
            self_hosted=False,
        )
        for job in REQUIRED_CI_HOSTED_JOBS:
            self.assertEqual(expected[job], "success")
        for job in (
            "build-openvmm-linux-gnu",
            "openvmm-unit-tests",
            "nvx-microvm-tests-mshv",
            "platform-whp",
            "performance-gate",
        ):
            self.assertEqual(expected[job], "skipped")
        self.assertEqual(expected["quality"], "success")
        self.assertEqual(expected["artifacts"], "success")

    def test_fleet_mode_preserves_backend_and_performance_requirements(self) -> None:
        expected = required_ci_expected_results(
            "pull_request",
            same_repository=True,
            run_tests=True,
            run_workloads=True,
        )
        for job, result in expected.items():
            self.assertEqual(
                result, "skipped" if job in REQUIRED_CI_HOSTED_JOBS else "success"
            )

    def test_documentation_and_external_prs_do_not_schedule_fleet_or_runtime(
        self,
    ) -> None:
        for same_repository, run_workloads in ((True, False), (False, True)):
            expected = required_ci_expected_results(
                "pull_request",
                same_repository=same_repository,
                run_tests=False,
                run_workloads=run_workloads,
                self_hosted=False,
            )
            for job in REQUIRED_CI_HOSTED_JOBS:
                self.assertEqual(expected[job], "skipped")
            self.assertEqual(expected["quality"], "success")


if __name__ == "__main__":
    unittest.main()

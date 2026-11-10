"""Offline local-harness guardrails; these mocks establish no live Gitea ACL claim.

Only synthetic state in newly allocated artifacts/ subdirectories is used. Socket
and child-process creation are blocked even if a transport mock is accidentally
omitted. No existing private fixture, Docker daemon, or service is accessed.
"""

import base64
import io
import json
import os
import socket
import stat
import subprocess
import tempfile
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from email.message import Message
from pathlib import Path
from unittest.mock import Mock, call, patch
from urllib.request import AbstractHTTPHandler
from urllib.response import addinfourl

import lab


def setUpModule():
    for module, attributes in (
        (socket, ("socket", "create_connection", "getaddrinfo")),
        (subprocess, ("run", "Popen")),
    ):
        for attribute in attributes:
            guard = patch.object(
                module,
                attribute,
                side_effect=AssertionError("offline test attempted external I/O"),
            )
            guard.start()
            unittest.addModuleCleanup(guard.stop)


def synthetic_fixture():
    """Identifiers and credentials below are test-only, never a release attestation."""
    run_id = "prodguild-t01-lab-0123456789"
    return {
        "run_id": run_id,
        "namespace": "prodguild-t01-lab0123456789",
        "origin": "http://127.0.0.1:49152",
        "release": "0.0.0",
        "image": "docker.gitea.com/gitea@sha256:" + "a" * 64,
        "image_tag": "docker.gitea.com/gitea:0.0.0",
        "container": run_id,
        "volume": run_id + "-data",
        "network": run_id + "-net",
        "bootstrap": {
            "username": "bootstrap-offline",
            "password": "offline-bootstrap-password-only",
        },
        "accounts": {
            role: {
                "id": index,
                "username": role.replace("_", "-") + "-0123456789",
                "password": "offline-" + role + "-password-only",
                "token": "offline-" + role + "-token-only-0123456789",
            }
            for index, role in enumerate(lab.ROLES, 1)
        },
        "repos": {"scratch": {"id": 101, "name": "scratch"}},
    }


def http_response(url, *, status=200, body=b"{}", headers=None):
    metadata = Message()
    for name, value in (headers or {}).items():
        metadata[name] = value
    result = addinfourl(io.BytesIO(body), metadata, url, status)
    result.msg = "synthetic offline response"
    return result


class OfflineLabTest(unittest.TestCase):
    def setUp(self):
        artifacts = lab.ROOT / "artifacts"
        artifacts.mkdir(mode=0o700, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="prodguild-t01-offline-test-", dir=artifacts)
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.lab = lab.Lab(self.directory / "state")
        self.lab.cfg = synthetic_fixture()


class StateSafetyTests(OfflineLabTest):
    def test_outside_artifacts_and_sibling_prefix_are_rejected_before_reading_state(
        self,
    ):
        for directory in (
            lab.ROOT / self.directory.name,
            lab.ROOT / "artifacts-not-ignored" / self.directory.name,
            lab.ROOT / "artifacts" / ".." / self.directory.name,
        ):
            with (
                self.subTest(directory=directory),
                patch.object(Path, "read_text") as read,
                self.assertRaises(lab.LabError),
            ):
                lab.Lab(directory)
            read.assert_not_called()

    def test_symlink_escape_from_artifacts_is_rejected(self):
        link = self.directory / "escape"
        # The target is intentionally nonexistent; nothing outside our tempdir is created/read.
        link.symlink_to(lab.ROOT / self.directory.name, target_is_directory=True)
        with self.assertRaises(lab.LabError):
            lab.Lab(link / "state")

    def test_save_creates_private_directory_and_fixture_even_with_permissive_umask(
        self,
    ):
        previous = os.umask(0)
        try:
            self.lab.save()
        finally:
            os.umask(previous)
        self.assertEqual(stat.S_IMODE(self.lab.directory.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.lab.state_file.stat().st_mode), 0o600)
        self.assertEqual(lab.Lab(self.lab.directory).cfg, self.lab.cfg)

    def test_save_hardens_existing_permissions_and_does_not_leave_old_tail(self):
        self.lab.save()
        self.lab.directory.chmod(0o755)
        self.lab.state_file.chmod(0o644)
        self.lab.cfg.pop("bootstrap")
        self.lab.save()
        self.assertEqual(stat.S_IMODE(self.lab.directory.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.lab.state_file.stat().st_mode), 0o600)
        self.assertEqual(json.loads(self.lab.state_file.read_text()), self.lab.cfg)

    def test_fixture_with_group_or_other_access_is_refused(self):
        self.lab.save()
        for permissions in (0o640, 0o604, 0o644, 0o660):
            with self.subTest(permissions=oct(permissions)):
                self.lab.state_file.chmod(permissions)
                with self.assertRaises(lab.LabError):
                    lab.Lab(self.lab.directory)

    def test_fixture_symlink_is_not_loaded_as_owned_state(self):
        self.lab.save()
        target = self.directory / "other-fixture.json"
        self.lab.state_file.rename(target)
        self.lab.state_file.symlink_to(target)
        with self.assertRaises(lab.LabError):
            lab.Lab(self.lab.directory)

    def test_private_write_refuses_symlink_without_overwriting_target(self):
        target = self.directory / "untouched.json"
        target.write_bytes(b"test-owned sentinel")
        link = self.directory / "write-link.json"
        link.symlink_to(target)
        with self.assertRaises(lab.LabError):
            lab.private_write(link, {"token": "offline-only"})
        self.assertEqual(target.read_bytes(), b"test-owned sentinel")


class DockerRoutingSafetyTests(OfflineLabTest):
    def test_routing_overrides_are_rejected_before_context_lookup_or_mutation(self):
        overrides = {
            "DOCKER_HOST": "tcp://remote.invalid:2375",
            "DOCKER_CONTEXT": "offline-untrusted-context",
            "DOCKER_TLS_VERIFY": "1",
            "DOCKER_CERT_PATH": "/offline-untrusted-certificates",
        }
        for pinned in (False, True):
            for variable, value in overrides.items():
                with self.subTest(variable=variable, pinned=pinned):
                    self.lab.cfg.pop("docker_endpoint", None)
                    if pinned:
                        self.lab.cfg["docker_endpoint"] = "unix://" + str(self.directory / "mock-docker.sock")
                    with (
                        patch.dict(os.environ, {variable: value}, clear=True),
                        patch.object(lab, "process") as process,
                        patch.object(
                            Path,
                            "stat",
                            side_effect=AssertionError("unexpected socket lookup"),
                        ),
                        patch.object(self.lab, "save") as save,
                        self.assertRaises(lab.LabError),
                    ):
                        self.lab.docker("restart", self.lab.cfg["container"])
                    process.assert_not_called()
                    save.assert_not_called()

    def test_remote_context_is_refused_after_read_only_discovery(self):
        for endpoint in (
            "tcp://remote.invalid:2375",
            "tcp://127.0.0.1:2375",
            "ssh://operator@remote.invalid",
            "https://remote.invalid:2376",
            "unix://remote.invalid/docker.sock",
        ):
            with self.subTest(endpoint=endpoint):
                context = [{"Endpoints": {"docker": {"Host": endpoint}}}]
                reply = subprocess.CompletedProcess(["docker"], 0, json.dumps(context).encode(), b"")
                with (
                    patch.dict(os.environ, {}, clear=True),
                    patch.object(lab, "process", return_value=reply) as process,
                    patch.object(
                        Path,
                        "stat",
                        side_effect=AssertionError("remote socket lookup attempted"),
                    ),
                    patch.object(self.lab, "save") as save,
                    self.assertRaises(lab.LabError),
                ):
                    self.lab.docker("volume", "rm", self.lab.cfg["volume"])
                self.assertEqual(
                    [item.args[0] for item in process.call_args_list],
                    [["docker", "context", "inspect"]],
                )
                self.assertNotIn("docker_endpoint", self.lab.cfg)
                save.assert_not_called()

    def test_persisted_remote_endpoint_cannot_bypass_routing_validation(self):
        self.lab.cfg["docker_endpoint"] = "tcp://remote.invalid:2375"
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(lab, "process") as process,
            patch.object(
                Path,
                "stat",
                side_effect=AssertionError("remote socket lookup attempted"),
            ),
            patch.object(self.lab, "save") as save,
            self.assertRaises(lab.LabError),
        ):
            self.lab.docker("restart", self.lab.cfg["container"])
        process.assert_not_called()
        save.assert_not_called()

    def test_unix_endpoint_must_be_a_socket_before_mutation(self):
        endpoint = "unix://" + str(self.directory / "not-an-actual-socket")
        context = [{"Endpoints": {"docker": {"Host": endpoint}}}]
        reply = subprocess.CompletedProcess(["docker"], 0, json.dumps(context).encode(), b"")
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(lab, "process", return_value=reply) as process,
            patch.object(Path, "stat", return_value=Mock(st_mode=stat.S_IFREG | 0o600)),
            patch.object(self.lab, "save") as save,
            self.assertRaises(lab.LabError),
        ):
            self.lab.docker("restart", self.lab.cfg["container"])
        self.assertEqual(
            [item.args[0] for item in process.call_args_list],
            [["docker", "context", "inspect"]],
        )
        save.assert_not_called()

    def test_mock_local_socket_is_pinned_for_later_commands_with_clean_child_environment(
        self,
    ):
        endpoint = "unix://" + str(self.directory / "mock-docker.sock")
        context = [{"Endpoints": {"docker": {"Host": endpoint}}}]
        discovery = subprocess.CompletedProcess(["docker"], 0, json.dumps(context).encode(), b"")
        success = subprocess.CompletedProcess(["docker"], 0, b"", b"")
        environment = {
            "PATH": "/usr/bin:/bin",
            "HOME": str(self.directory),
            "DOCKER_CONFIG": "/offline-untrusted-config",
            "UNRELATED_SECRET": "offline-ambient-secret-only",
        }
        with (
            patch.dict(os.environ, environment, clear=True),
            patch.object(lab, "process", side_effect=[discovery, success, success]) as process,
            patch.object(Path, "stat", return_value=Mock(st_mode=stat.S_IFSOCK | 0o600)),
            patch.object(self.lab, "save"),
        ):
            self.lab.docker("restart", self.lab.cfg["container"], timeout=17)
            self.lab.docker("volume", "rm", self.lab.cfg["volume"], timeout=23)
        self.assertEqual(self.lab.cfg["docker_endpoint"], endpoint)
        commands = [item.args[0] for item in process.call_args_list]
        self.assertEqual(commands[0], ["docker", "context", "inspect"])
        self.assertEqual(
            commands[1],
            ["docker", "--host", endpoint, "restart", self.lab.cfg["container"]],
        )
        self.assertEqual(
            commands[2],
            ["docker", "--host", endpoint, "volume", "rm", self.lab.cfg["volume"]],
        )
        for invocation in process.call_args_list:
            self.assertEqual(
                invocation.kwargs["env"],
                {"PATH": environment["PATH"], "HOME": environment["HOME"]},
            )
        self.assertEqual([item.kwargs["timeout"] for item in process.call_args_list[1:]], [17, 23])


class OwnershipSafetyTests(OfflineLabTest):
    def docker_reply(self, kind, labels):
        value = {"Config": {"Labels": labels}} if kind == "container" else {"Labels": labels}
        return subprocess.CompletedProcess(["docker"], 0, json.dumps([value]).encode(), b"")

    def discovery_reply(self, args, present=None):
        for kind, prefix in (
            ("container", ("ps", "--all")),
            ("volume", ("volume", "ls")),
            ("network", ("network", "ls")),
        ):
            if args[:2] == prefix:
                names = "foreign-resource-must-not-be-touched\n"
                if present is None or kind in present:
                    names += self.lab.cfg[kind] + "\n"
                return subprocess.CompletedProcess(["docker", *args], 0, names.encode(), b"")
        return None

    def test_missing_or_wrong_labels_cannot_establish_ownership(self):
        for kind in ("container", "volume", "network"):
            for labels in (
                None,
                {},
                {lab.LABEL: "different-run"},
                {"unrelated-label": self.lab.cfg["run_id"]},
            ):
                with (
                    self.subTest(kind=kind, labels=labels),
                    patch.object(self.lab, "docker", return_value=self.docker_reply(kind, labels)) as docker,
                    self.assertRaises(lab.LabError),
                ):
                    self.lab.owned(kind, self.lab.cfg[kind])
                docker.assert_called_once_with(kind, "inspect", self.lab.cfg[kind])

    def test_cleanup_checks_all_labels_before_any_mutation(self):
        for unowned_kind in ("container", "volume", "network"):

            def inspect_only(*args, **kwargs):
                discovery = self.discovery_reply(args)
                if discovery is not None:
                    return discovery
                if len(args) >= 2 and args[1] == "inspect":
                    owner = "different-run" if args[0] == unowned_kind else self.lab.cfg["run_id"]
                    return self.docker_reply(args[0], {lab.LABEL: owner})
                return subprocess.CompletedProcess(["docker", *args], 0, b"", b"")

            with self.subTest(unowned_kind=unowned_kind):
                with (
                    patch.object(self.lab, "docker", side_effect=inspect_only) as docker,
                    self.assertRaises(lab.LabError),
                ):
                    self.lab.cleanup()
                self.assertTrue(docker.call_args_list)
                self.assertTrue(
                    all(item.args[0] == "ps" or item.args[1] in ("inspect", "ls") for item in docker.call_args_list),
                    docker.call_args_list,
                )
                self.assertNotIn("cleaned_up", self.lab.cfg)

    def test_owned_cleanup_mutates_only_exact_inspected_identifiers(self):
        inspected = set()

        def owned_docker(*args, **kwargs):
            discovery = self.discovery_reply(args)
            if discovery is not None:
                return discovery
            if len(args) >= 2 and args[1] == "inspect":
                kind, _, name = args
                self.assertEqual(name, self.lab.cfg[kind])
                inspected.add(name)
                return self.docker_reply(kind, {lab.LABEL: self.lab.cfg["run_id"]})
            self.assertIn(args[-1], inspected)
            return subprocess.CompletedProcess(["docker", *args], 0, b"", b"")

        with patch.object(self.lab, "docker", side_effect=owned_docker) as docker:
            self.lab.cleanup()
        mutations = [
            item for item in docker.call_args_list if item.args[0] != "ps" and item.args[1] not in ("inspect", "ls")
        ]
        self.assertEqual(
            mutations,
            [
                call("stop", "--time", "10", self.lab.cfg["container"]),
                call("container", "rm", self.lab.cfg["container"]),
                call("volume", "rm", self.lab.cfg["volume"]),
                call("network", "rm", self.lab.cfg["network"]),
            ],
        )
        self.assertTrue(lab.Lab(self.lab.directory).cfg["cleaned_up"])

    def test_unowned_container_cannot_be_restarted_or_have_git_maintenance_run(self):
        with (
            patch.object(
                self.lab,
                "docker",
                return_value=self.docker_reply("container", {lab.LABEL: "other"}),
            ) as docker,
            patch.object(self.lab, "http") as http,
            patch.object(self.lab, "git") as git,
            self.assertRaises(lab.LabError),
        ):
            self.lab.restart_maintenance()
        docker.assert_called_once_with("container", "inspect", self.lab.cfg["container"])
        http.assert_not_called()
        git.assert_not_called()

    def test_partial_cleanup_skips_absent_resources_and_repeat_is_idempotent(self):
        for survivors in (
            (),
            ("container",),
            ("volume",),
            ("network",),
            ("container", "volume"),
            ("container", "network"),
            ("volume", "network"),
        ):
            with self.subTest(survivors=survivors):
                present, inspected, removed = set(survivors), set(), []
                self.lab.cfg.pop("cleaned_up", None)

                def partial_docker(*args, **kwargs):
                    discovery = self.discovery_reply(args, present=present)
                    if discovery is not None:
                        return discovery
                    if args[1] == "inspect":
                        kind, _, name = args
                        self.assertIn(kind, present)
                        self.assertEqual(name, self.lab.cfg[kind])
                        inspected.add(kind)
                        return self.docker_reply(kind, {lab.LABEL: self.lab.cfg["run_id"]})
                    self.assertEqual(
                        inspected,
                        set(survivors),
                        "mutated before checking every survivor",
                    )
                    if args[0] == "stop":
                        self.assertIn("container", present)
                        self.assertEqual(args[-1], self.lab.cfg["container"])
                    else:
                        kind, operation, name = args
                        self.assertEqual(operation, "rm")
                        self.assertEqual(name, self.lab.cfg[kind])
                        present.remove(kind)
                        removed.append(kind)
                    return subprocess.CompletedProcess(["docker", *args], 0, b"", b"")

                with patch.object(self.lab, "docker", side_effect=partial_docker) as docker:
                    self.lab.cleanup()
                    first_call_count = docker.call_count
                    self.lab.cleanup()
                self.assertFalse(present)
                self.assertEqual(
                    removed,
                    [kind for kind in ("container", "volume", "network") if kind in survivors],
                )
                self.assertTrue(
                    all(
                        item.args[0] == "ps" or item.args[1] == "ls"
                        for item in docker.call_args_list[first_call_count:]
                    )
                )
                self.assertTrue(lab.Lab(self.lab.directory).cfg["cleaned_up"])

    def test_partial_absence_does_not_skip_ownership_checks_on_survivors(self):
        for missing, unowned in (
            ("container", "network"),
            ("volume", "network"),
            ("network", "volume"),
        ):
            with self.subTest(missing=missing, unowned=unowned):
                present = {"container", "volume", "network"} - {missing}

                def partial_docker(*args, **kwargs):
                    discovery = self.discovery_reply(args, present=present)
                    if discovery is not None:
                        return discovery
                    if args[1] == "inspect":
                        self.assertIn(args[0], present)
                        owner = "different-run" if args[0] == unowned else self.lab.cfg["run_id"]
                        return self.docker_reply(args[0], {lab.LABEL: owner})
                    self.fail("cleanup mutated a resource despite an unowned survivor")

                with (
                    patch.object(self.lab, "docker", side_effect=partial_docker) as docker,
                    self.assertRaises(lab.LabError),
                ):
                    self.lab.cleanup()
                docker.assert_any_call(unowned, "inspect", self.lab.cfg[unowned])
                self.assertNotIn("cleaned_up", self.lab.cfg)

    def test_discovery_failure_is_not_treated_as_an_absent_resource(self):
        def unavailable_docker(*args, **kwargs):
            if args[:2] == ("volume", "ls"):
                raise lab.LabError("offline_discovery_unavailable")
            discovery = self.discovery_reply(args)
            if discovery is not None:
                return discovery
            if args[1] == "inspect":
                return self.docker_reply(args[0], {lab.LABEL: self.lab.cfg["run_id"]})
            self.fail("cleanup mutated a resource despite incomplete discovery")

        with (
            patch.object(self.lab, "docker", side_effect=unavailable_docker),
            self.assertRaises(lab.LabError),
        ):
            self.lab.cleanup()
        self.assertNotIn("cleaned_up", self.lab.cfg)


class PreflightSafetyTests(OfflineLabTest):
    def inspections(self):
        container_id = "c" * 64
        image_id = "sha256:" + "b" * 64
        return {
            "container": {
                "Id": container_id,
                "Image": image_id,
                "Config": {"Labels": {lab.LABEL: self.lab.cfg["run_id"]}},
                "HostConfig": {"Privileged": False},
                "Mounts": [
                    {
                        "Type": "volume",
                        "Name": self.lab.cfg["volume"],
                        "Destination": "/data",
                    }
                ],
                "NetworkSettings": {
                    "Ports": {
                        "3000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "49152"}],
                        "22/tcp": None,
                    },
                    "Networks": {self.lab.cfg["network"]: {}},
                },
            },
            "volume": {"Labels": {lab.LABEL: self.lab.cfg["run_id"]}},
            "network": {
                "Labels": {lab.LABEL: self.lab.cfg["run_id"]},
                "Containers": {container_id: {}},
            },
            "image": {
                "Id": image_id,
                "RepoDigests": [self.lab.cfg["image"]],
                "Architecture": "offline-only",
            },
        }

    def inspect_docker(self, documents, *args, **kwargs):
        kind, operation, identifier = args
        self.assertEqual(operation, "inspect", "preflight attempted Docker mutation")
        self.assertEqual(identifier, self.lab.cfg[kind])
        return subprocess.CompletedProcess(["docker", *args], 0, json.dumps([documents[kind]]).encode(), b"")

    def test_runtime_boundary_failure_prevents_readiness_credentials_and_matrix_mutation(
        self,
    ):
        mutations = (
            (("container", "Config", "Labels", lab.LABEL), "another-run"),
            (("volume", "Labels", lab.LABEL), "another-run"),
            (("network", "Labels", lab.LABEL), "another-run"),
            (("network", "Containers"), {"c" * 64: {}, "foreign-container": {}}),
            (
                ("container", "NetworkSettings", "Ports", "3000/tcp"),
                [{"HostIp": "0.0.0.0", "HostPort": "49152"}],
            ),
            (
                ("container", "NetworkSettings", "Ports", "3000/tcp"),
                [{"HostIp": "127.0.0.1", "HostPort": "49153"}],
            ),
            (
                ("container", "NetworkSettings", "Ports", "22/tcp"),
                [{"HostIp": "127.0.0.1", "HostPort": "49154"}],
            ),
            (("container", "HostConfig", "Privileged"), True),
            (
                ("container", "Mounts"),
                [{"Type": "bind", "Destination": "/var/run/docker.sock"}],
            ),
            (("container", "NetworkSettings", "Networks"), {"foreign-network": {}}),
            (("container", "Image"), "sha256:" + "d" * 64),
            (
                ("image", "RepoDigests"),
                ["registry.invalid/unapproved@sha256:" + "e" * 64],
            ),
        )
        for location, value in mutations:
            with self.subTest(location=location, value=value):
                documents = self.inspections()
                target = documents
                for key in location[:-1]:
                    target = target[key]
                target[location[-1]] = value
                self.lab.rows = []
                with ExitStack() as stack:
                    stack.enter_context(redirect_stderr(io.StringIO()))
                    docker = stack.enter_context(
                        patch.object(
                            self.lab,
                            "docker",
                            side_effect=lambda *a, **k: self.inspect_docker(documents, *a, **k),
                        )
                    )
                    guards = [
                        stack.enter_context(
                            patch.object(
                                self.lab,
                                name,
                                side_effect=AssertionError("preflight did not stop " + name),
                            )
                        )
                        for name in (
                            "readiness",
                            "http",
                            "api",
                            "identity_control",
                            "permission_matrix",
                            "git",
                            "save",
                        )
                    ]
                    self.lab.run_matrix()
                self.assertTrue(docker.call_args_list)
                for guard in guards:
                    guard.assert_not_called()
                self.assertEqual(len(self.lab.rows), 1)
                self.assertEqual(self.lab.rows[0]["status"], "failed")
                self.assertEqual(self.lab.rows[0]["id"], "release.runtime_and_isolation_preflight")

    def test_release_preflight_failure_stops_before_identity_and_mutating_matrix(self):
        documents = self.inspections()
        with (
            patch.object(
                self.lab,
                "docker",
                side_effect=lambda *a, **k: self.inspect_docker(documents, *a, **k),
            ),
            patch.object(self.lab, "readiness") as readiness,
            patch.object(
                self.lab,
                "api",
                return_value=(200, {"version": "different-release"}, {}),
            ) as api,
            patch.object(
                self.lab,
                "identity_control",
                side_effect=AssertionError("identity after failed preflight"),
            ),
            patch.object(
                self.lab,
                "permission_matrix",
                side_effect=AssertionError("matrix after failed preflight"),
            ),
            patch.object(
                self.lab,
                "http",
                side_effect=AssertionError("unexpected credential-bearing request"),
            ),
            patch.object(self.lab, "git", side_effect=AssertionError("unexpected Git operation")),
            redirect_stderr(io.StringIO()),
        ):
            self.lab.run_matrix()
        readiness.assert_called_once_with()
        api.assert_called_once_with("GET", "/version")
        self.assertEqual(len(self.lab.rows), 1)
        self.assertEqual(self.lab.rows[0]["status"], "failed")
        self.assertEqual(self.lab.rows[0]["code"], "runtime_release_mismatch")

    def test_bootstrap_boundary_failure_prevents_readiness_and_credential_creation(
        self,
    ):
        lock = self.directory / "synthetic-release-lock.json"
        lock.write_text(
            json.dumps(
                {
                    "local_only": True,
                    "source_repository": "go-gitea/gitea",
                    "image": self.lab.cfg["image"],
                    "release": self.lab.cfg["release"],
                    "tag": self.lab.cfg["image_tag"],
                }
            )
        )
        self.lab.cfg = {}

        def bootstrap_docker(*args, **kwargs):
            if args[0] == "pull":
                self.assertEqual(args, ("pull", self.lab.cfg["image"]))
                return subprocess.CompletedProcess(["docker", *args], 0, b"", b"")
            if args[0] == "run" or args[:2] in (
                ("volume", "create"),
                ("network", "create"),
            ):
                return subprocess.CompletedProcess(["docker", *args], 0, b"", b"")
            self.assertEqual(
                args[1],
                "inspect",
                "credential-creating exec ran after failed boundary check",
            )
            documents = self.inspections()
            documents["container"]["HostConfig"]["Privileged"] = True
            return self.inspect_docker(documents, *args, **kwargs)

        listener = Mock()
        listener.__enter__ = Mock(return_value=listener)
        listener.__exit__ = Mock(return_value=False)
        listener.getsockname.return_value = ("127.0.0.1", 49152)
        with (
            patch.object(socket, "socket", return_value=listener),
            patch.object(lab.secrets, "token_hex", return_value="0123456789"),
            patch.object(
                lab.secrets,
                "token_urlsafe",
                side_effect=AssertionError("credential generation after failure"),
            ),
            patch.object(self.lab, "docker", side_effect=bootstrap_docker),
            patch.object(
                self.lab,
                "readiness",
                side_effect=AssertionError("readiness after failed boundary"),
            ),
            patch.object(
                self.lab,
                "http",
                side_effect=AssertionError("HTTP after failed boundary"),
            ),
            patch.object(
                self.lab,
                "seed",
                side_effect=AssertionError("seed after failed boundary"),
            ),
            self.assertRaisesRegex(lab.LabError, "unsafe_container_permissions"),
        ):
            self.lab.bootstrap(lock)
        self.assertEqual(self.lab.cfg["accounts"], {})
        self.assertNotIn("bootstrap", self.lab.cfg)
        self.assertNotIn("bootstrapped", self.lab.cfg)


class HttpSafetyTests(OfflineLabTest):
    unsafe_origins = (
        "http://example.invalid:3000",
        "http://localhost:3000",
        "http://127.0.0.1.example.invalid:3000",
        "http://2130706433:3000",
        "http://127.0.0.2:3000",
        "http://[::1]:3000",
        "https://127.0.0.1:3000",
        "ftp://127.0.0.1:3000",
        "http://user:password@127.0.0.1:3000",
        "http://@127.0.0.1:3000",
        "http://127.0.0.1:3000/",
        "http://127.0.0.1:3000/api",
        "http://127.0.0.1:3000?token=offline",
        "http://127.0.0.1:3000?",
        "http://127.0.0.1:3000#fragment",
        "http://127.0.0.1:3000#",
        "http://127.0.0.1",
        "http://127.0.0.1:0",
        "http://127.0.0.1:65536",
        "http://127.0.0.1:not-a-port",
        "http://127.0.0.1:3000\n",
        " http://127.0.0.1:3000",
    )

    def test_configured_origin_is_validated_before_authenticated_request(self):
        for origin in self.unsafe_origins:
            self.lab.cfg["origin"] = origin
            with self.subTest(origin=origin):
                with (
                    patch.object(
                        self.lab.opener,
                        "open",
                        side_effect=AssertionError("unsafe origin reached transport"),
                    ),
                    self.assertRaises(lab.LabError),
                ):
                    self.lab.http("GET", "/api/v1/user", role="backend")

    def test_override_origin_uses_same_validation_before_sending_credentials(self):
        for origin in self.unsafe_origins:
            with self.subTest(origin=origin):
                with (
                    patch.object(
                        self.lab.opener,
                        "open",
                        side_effect=AssertionError("unsafe override reached transport"),
                    ),
                    self.assertRaises(lab.LabError),
                ):
                    self.lab.http("GET", "/api/v1/user", role="backend", origin=origin)

    def test_unsafe_path_is_refused_before_transport(self):
        for path in (
            "",
            "http://example.invalid/api/v1/user",
            "//example.invalid/api/v1/user",
            "/api/v1/user\r\nAuthorization: injected",
            "/api/v1/user\t",
            "/api/v1/user\x00",
            "/api/v1/../user",
            "/api/v1/%2e%2E/user",
            "/api/v1/..\\user",
            "/api/v1/%5cuser",
            "/api/v1/user#fragment",
        ):
            with self.subTest(path=path):
                with (
                    patch.object(
                        self.lab.opener,
                        "open",
                        side_effect=AssertionError("unsafe path reached transport"),
                    ),
                    self.assertRaises(lab.LabError),
                ):
                    self.lab.http("GET", path, role="backend")

    def test_valid_local_api_request_keeps_token_in_header_and_has_timeout(self):
        path = "/api/v1/repos/offline/scratch/branches/prodguild%2Fsubmissions%2Fabc?limit=10"
        with patch.object(self.lab.opener, "open", return_value=http_response(self.lab.origin + path)) as opened:
            status, body, _ = self.lab.http("GET", path, role="backend")
        self.assertEqual((status, body), (200, b"{}"))
        request = opened.call_args.args[0]
        token = self.lab.cfg["accounts"]["backend"]["token"]
        self.assertEqual(request.full_url, self.lab.origin + path)
        self.assertEqual(request.get_header("Authorization"), "token " + token)
        self.assertNotIn(token, request.full_url)
        self.assertGreater(opened.call_args.kwargs["timeout"], 0)
        self.assertLessEqual(opened.call_args.kwargs["timeout"], 10)

    def assert_redirect_not_forwarded(self, status, destination, *, basic=False):
        sent = []

        def transport(connection_class, request, **kwargs):
            sent.append(request)
            if len(sent) == 1:
                return http_response(request.full_url, status=status, headers={"Location": destination})
            return http_response(request.full_url)

        account = self.lab.cfg["accounts"]["backend"]
        credentials = (
            {
                "basic": (account["username"], account["password"]),
                "response_role": "backend",
            }
            if basic
            else {"role": "backend"}
        )
        with patch.object(AbstractHTTPHandler, "do_open", side_effect=transport):
            observed, _, _ = self.lab.http(
                "POST" if basic else "GET",
                "/user/login" if basic else "/api/v1/user",
                expected=(status, 200),
                body=b"synthetic-login-body" if basic else None,
                **credentials,
            )
        self.assertEqual(len(sent), 1, "redirect caused a second request")
        self.assertEqual(observed, status)
        self.assertEqual(sent[0].host, "127.0.0.1:49152")
        expected_auth = (
            "Basic " + base64.b64encode((account["username"] + ":" + account["password"]).encode()).decode()
            if basic
            else "token " + account["token"]
        )
        self.assertEqual(sent[0].get_header("Authorization"), expected_auth)

    def test_token_redirect_never_forwards_auth_even_to_same_origin(self):
        for status in (301, 302, 303, 307, 308):
            for destination in (
                "http://example.invalid/capture",
                "http://127.0.0.1:49153/capture",
                self.lab.origin + "/redirected",
            ):
                with self.subTest(status=status, destination=destination):
                    self.assert_redirect_not_forwarded(status, destination)

    def test_basic_login_redirect_does_not_resend_credentials_or_body(self):
        for status in (302, 303, 307, 308):
            with self.subTest(status=status):
                self.assert_redirect_not_forwarded(status, "http://example.invalid/capture", basic=True)

    def test_oversized_body_is_refused_and_read_is_bounded(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b"x" * 17
        with (
            patch.object(lab, "BODY_LIMIT", 16),
            patch.object(self.lab.opener, "open", return_value=response),
            self.assertRaises(lab.LabError),
        ):
            self.lab.http("GET", "/api/v1/user", role="backend")
        response.read.assert_called_once_with(17)


class GitSafetyTests(OfflineLabTest):
    def test_clone_disables_ambient_credentials_config_hooks_protocols_and_redirects(
        self,
    ):
        hostile = {
            "HOME": "/offline-untrusted-home",
            "PATH": "/offline-untrusted-bin",
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "credential.helper",
            "GIT_CONFIG_VALUE_0": "!offline-malicious-helper",
            "GIT_CONFIG_PARAMETERS": "'credential.helper=offline-malicious-helper'",
            "GIT_CONFIG": "/offline-untrusted-config",
            "GIT_ASKPASS": "/offline-untrusted-askpass",
            "SSH_ASKPASS": "/offline-untrusted-ssh-askpass",
            "GIT_SSH_COMMAND": "offline-ssh-command",
            "GIT_EXEC_PATH": "/offline-untrusted-git-core",
            "GIT_TEMPLATE_DIR": "/offline-untrusted-template",
            "GIT_TRACE": "1",
            "GIT_TRACE_CURL": "1",
            "GIT_CURL_VERBOSE": "1",
            "GIT_SSL_NO_VERIFY": "1",
            "GIT_ALTERNATE_OBJECT_DIRECTORIES": "/offline-untrusted-objects",
            "HTTP_PROXY": "http://example.invalid:8080",
            "HTTPS_PROXY": "http://example.invalid:8080",
            "ALL_PROXY": "http://example.invalid:8080",
            "UNRELATED_SECRET": "offline-ambient-secret-only",
        }
        result = subprocess.CompletedProcess(["git"], 0, b"", b"")
        with (
            patch.dict(os.environ, hostile, clear=True),
            patch.object(lab, "process", return_value=result) as process,
        ):
            destination = self.lab.clone("scratch", "student_a", suffix="offline")
        command = process.call_args.args[0]
        options = process.call_args.kwargs
        environment = options["env"]
        self.assertEqual(command[0], "/usr/bin/git")
        self.assertIn("--bare", command)
        self.assertIn("--no-local", command)
        self.assertIn("--no-hardlinks", command)
        self.assertIn(self.lab.origin + "/" + self.lab.cfg["namespace"] + "/scratch.git", command)
        self.assertTrue(destination.is_relative_to(self.lab.directory))
        self.assertEqual(command[-1], str(destination))
        self.assertEqual(options["cwd"], self.lab.directory)
        self.assertGreater(options["timeout"], 0)
        self.assertLessEqual(options["timeout"], 30)
        config = dict(command[index + 1].split("=", 1) for index, arg in enumerate(command[:-1]) if arg == "-c")
        for key, value in {
            "credential.helper": "",
            "core.hooksPath": os.devnull,
            "init.templateDir": "",
            "protocol.file.allow": "never",
            "protocol.ext.allow": "never",
            "http.followRedirects": "false",
            "http.sslVerify": "true",
        }.items():
            self.assertEqual(config.get(key), value, key)
        for key in hostile:
            if key not in ("HOME", "PATH", "GIT_ASKPASS"):
                self.assertNotIn(key, environment)
        self.assertEqual(environment["HOME"], str(self.lab.directory))
        self.assertEqual(environment["XDG_CONFIG_HOME"], str(self.lab.directory))
        self.assertEqual(environment["PATH"], "/usr/bin:/bin")
        self.assertEqual(environment["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(environment["GIT_CONFIG_NOSYSTEM"], "1")
        self.assertEqual(environment["GIT_CONFIG_SYSTEM"], os.devnull)
        self.assertEqual(environment["GIT_CONFIG_GLOBAL"], os.devnull)
        self.assertEqual(environment["GIT_ATTR_NOSYSTEM"], "1")
        account = self.lab.cfg["accounts"]["student_a"]
        self.assertEqual(environment["PRODGUILD_LAB_TOKEN"], account["token"])
        for role, other in self.lab.cfg["accounts"].items():
            for field in ("token", "password"):
                self.assertNotIn(other[field], " ".join(command))
                if role != "student_a" or field != "token":
                    self.assertNotIn(other[field], environment.values())

    def test_askpass_is_private_executable_and_does_not_embed_credentials(self):
        with patch.object(lab, "process", return_value=subprocess.CompletedProcess(["git"], 0)) as process:
            self.lab.git(self.lab.directory, "backend", ["status"])
        helper = Path(process.call_args.kwargs["env"]["GIT_ASKPASS"])
        self.assertTrue(helper.is_relative_to(self.lab.directory))
        self.assertEqual(stat.S_IMODE(helper.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(helper.parent.stat().st_mode), 0o700)
        content = helper.read_text()
        for account in self.lab.cfg["accounts"].values():
            self.assertNotIn(account["token"], content)
            self.assertNotIn(account["password"], content)


class PolicyLabTest(OfflineLabTest):
    def setUp(self):
        super().setUp()
        self.lab.cfg["origin"] = "http://127.0.0.1:54030"
        self.lab.cfg["repos"] = {
            key: {
                "id": index,
                "name": key.replace("_", "-"),
                "marker": "PRODGUILD_T01_OFFLINE_" + key.upper(),
                "head": str(index) * 40,
                "base": "a" * 40,
                "working": "b" * 40,
                "head_ref": "prodguild/submissions/offline",
                "base_ref": "prodguild/bases/offline",
                **({"pull": 1} if key.startswith("solution_") else {}),
            }
            for index, key in enumerate(
                (
                    "starter",
                    "reference",
                    "draft_case",
                    "pending",
                    "solution_a",
                    "solution_b",
                ),
                1,
            )
        }

    def result(self, text, *, code=128, stdout=b""):
        return subprocess.CompletedProcess(["git"], code, stdout, text.encode() if isinstance(text, str) else text)

    def http_denial(self, url, status=404):
        return self.result(f"fatal: unable to access '{url}/': The requested URL returned error: {status}\n")

    def framed_leaks(self, marker):
        escaped = marker.replace("PRODGUILD", "\\u0050RODGUILD").encode()
        nested = base64.b64encode(base64.b64encode(marker.encode())).decode()
        header = b"diff --git a/probe.txt b/probe.txt\n--- a/probe.txt\n+++ b/probe.txt\n@@ -1 +1 @@\n"
        return (
            header + b"+" + json.dumps({"content": nested}).encode() + b"\n",
            header + b"+" + escaped + b"\n",
            header + b"-" + json.dumps({"content": marker}).encode().replace(b"PRODGUILD", b"\\u0050RODGUILD") + b"\n",
            header + b"+" + nested.encode() + b"\n",
        )


class GitCanarySafetyTests(PolicyLabTest):
    """Test the local scanner's canary policy, not Git/Gitea access controls."""

    def setUp(self):
        super().setUp()
        self.blobs_read = []

    def scan_synthetic_blobs(
        self,
        *,
        leaking_role=None,
        solution=None,
        peer_result=None,
        api_body=None,
        blob_body=None,
    ):
        clones = {}
        blob_sha = "f" * 40

        def clone(key, role, **kwargs):
            directory = self.directory / ("mock-git-" + key + "-" + role)
            clones[directory] = key
            return directory

        def git(directory, role, args, **kwargs):
            key = clones[directory]
            code, output, errors = 0, b"", b""
            if args[0] == "fetch":
                if args[-1] != "+refs/*:refs/*":
                    return peer_result(args[1]) if peer_result else self.http_denial(args[1])
            elif args == ["rev-list", "--objects", "--all"]:
                output = (blob_sha + " probe.txt\n").encode()
            elif args == ["cat-file", "-t", blob_sha]:
                output = b"blob\n"
            elif args == ["cat-file", "blob", blob_sha]:
                self.blobs_read.append((key, role))
                output = (self.lab.cfg["repos"][key]["marker"] + "\n").encode()
                if key == "starter" and role == leaking_role:
                    output += self.lab.cfg["repos"][solution]["marker"].encode()
                if blob_body:
                    output = blob_body(key, role, output)
            else:
                self.fail("unexpected synthetic Git operation")
            return subprocess.CompletedProcess(["git", *args], code, output, errors)

        pending = base64.b64encode(self.lab.cfg["repos"]["pending"]["marker"].encode()).decode()
        with (
            patch.object(self.lab, "clone", side_effect=clone),
            patch.object(self.lab, "git", side_effect=git),
            patch.object(self.lab, "file", return_value={"content": pending}),
            patch.object(
                self.lab,
                "http",
                side_effect=lambda method, path, **kw: (
                    200,
                    api_body(path, kw["role"]) if api_body else b"[]",
                    {},
                ),
            ),
        ):
            return self.lab.scan_private_git()

    def test_clean_starter_and_own_solution_markers_do_not_trigger_canary_failure(self):
        observation = self.scan_synthetic_blobs()
        self.assertEqual(observation["reachable_blobs_scanned"], 4)
        self.assertEqual(observation["student_api_bodies_scanned"], 20)
        self.assertEqual(
            observation["native_denial_classifications"],
            ["git-http-404-exact-repository"] * 2,
        )
        self.assertEqual(
            self.blobs_read,
            [
                ("starter", "student_a"),
                ("starter", "student_b"),
                ("solution_a", "student_a"),
                ("solution_b", "student_b"),
            ],
        )
        self.assertFalse(
            self.lab.rows,
            "synthetic scanner results must not be recorded as live evidence",
        )

    def test_shared_starter_rejects_both_solution_markers_for_each_student(self):
        for role in ("student_a", "student_b"):
            for solution in ("solution_a", "solution_b"):
                with self.subTest(role=role, solution=solution):
                    self.blobs_read = []
                    with self.assertRaisesRegex(lab.LabError, "private_content_in_reachable_git_history"):
                        self.scan_synthetic_blobs(leaking_role=role, solution=solution)
                    self.assertIn(("starter", role), self.blobs_read)

    def test_known_peer_sha_fetch_rejects_transport_auth_storage_and_wrong_target(self):
        factories = (
            lambda url: self.result(f"fatal: unable to access '{url}/': Connection refused"),
            lambda url: self.result(f"fatal: unable to access '{url}/': Recv failure: Connection reset by peer"),
            lambda url: self.result(f"fatal: Authentication failed for '{url}/'"),
            lambda url: self.result("fatal: object not found; No space left on device"),
            lambda url: self.http_denial(url.replace("solution-", "other-")),
            lambda url: self.http_denial(url.replace("http://", "http://user:password@")),
            lambda url: self.http_denial(url + "?status=403"),
            lambda url: self.result(self.http_denial(url).stderr + b"fatal: Authentication failed\n"),
            lambda url: self.result(self.http_denial(url).stderr, code=0),
        )
        for factory in factories:
            with (
                self.subTest(factory=factories.index(factory)),
                self.assertRaisesRegex(lab.LabError, "peer_known_sha_denial_not_authorization"),
            ):
                self.scan_synthetic_blobs(peer_result=factory)

    def test_student_api_absence_includes_peer_escaped_and_nested_base64_markers(self):
        for role, peer in (("student_a", "solution_b"), ("student_b", "solution_a")):
            marker = self.lab.cfg["repos"][peer]["marker"]
            for suffix in ("/reviews", "/comments", "/git/refs", ".diff"):
                for body in (
                    json.dumps({"body": marker}).encode().replace(b"PRODGUILD", b"\\u0050RODGUILD"),
                    json.dumps({"nested": [base64.b64encode(base64.b64encode(marker.encode())).decode()]}).encode(),
                    ("+" + marker + "\n").encode() if suffix == ".diff" else json.dumps([marker]).encode(),
                ):

                    def api_body(path, actor):
                        return body if actor == role and path.endswith(suffix) else b"[]"

                    with (
                        self.subTest(role=role, suffix=suffix, body=body),
                        self.assertRaisesRegex(lab.LabError, "private_marker_in_student_api"),
                    ):
                        self.scan_synthetic_blobs(api_body=api_body)

    def test_student_api_own_marker_is_allowed_but_starter_excludes_both_solutions(
        self,
    ):
        def own_body(path, role):
            if "/solution-" not in path:
                return b"{}"
            key = "solution_a" if role == "student_a" else "solution_b"
            return json.dumps({"body": self.lab.cfg["repos"][key]["marker"]}).encode()

        self.scan_synthetic_blobs(api_body=own_body)
        for role in ("student_a", "student_b"):
            for solution in ("solution_a", "solution_b"):

                def starter_body(path, actor):
                    marker = self.lab.cfg["repos"][solution]["marker"]
                    return json.dumps({"body": marker}).encode() if actor == role and "/starter" in path else b"{}"

                with (
                    self.subTest(role=role, solution=solution),
                    self.assertRaisesRegex(lab.LabError, "private_marker_in_student_api"),
                ):
                    self.scan_synthetic_blobs(api_body=starter_body)

    def test_student_api_diff_absence_scans_actual_diff_framed_encoded_payloads(self):
        for role, peer in (("student_a", "solution_b"), ("student_b", "solution_a")):
            for body in self.framed_leaks(self.lab.cfg["repos"][peer]["marker"]):
                with (
                    self.subTest(role=role, body=body),
                    self.assertRaisesRegex(lab.LabError, "private_marker_in_student_api"),
                ):
                    self.scan_synthetic_blobs(
                        api_body=lambda path, actor: (body if actor == role and path.endswith(".diff") else b"{}")
                    )

    def test_reachable_blob_text_scans_structured_lines_and_diff_payloads(self):
        marker = self.lab.cfg["repos"]["solution_b"]["marker"]
        nested = base64.b64encode(base64.b64encode(marker.encode())).decode()
        bodies = (
            *self.framed_leaks(marker),
            b"text header\n" + json.dumps({"content": nested}).encode(),
        )
        for body in bodies:
            with (
                self.subTest(body=body),
                self.assertRaisesRegex(lab.LabError, "private_content_in_reachable_git_history"),
            ):
                self.scan_synthetic_blobs(
                    blob_body=lambda key, role, clean: (body if key == "starter" and role == "student_a" else clean)
                )

    def test_student_api_absence_scans_declared_unpadded_and_wide_json(self):
        marker = self.lab.cfg["repos"]["solution_b"]["marker"]
        for payload in (
            marker.encode(),
            json.dumps({"content": marker}).encode("utf-16-be"),
        ):
            body = json.dumps(
                {
                    "encoding": "base64",
                    "content": base64.b64encode(payload).decode().rstrip("="),
                }
            ).encode()
            with (
                self.subTest(payload=payload),
                self.assertRaisesRegex(lab.LabError, "private_marker_in_student_api"),
            ):
                self.scan_synthetic_blobs(
                    api_body=lambda path, role: (body if role == "student_a" and "/solution-a/" in path else b"{}")
                )


class ApiCanarySafetyTests(PolicyLabTest):
    def denial(self, body, *, role="student_a", key="reference", text=False):
        def http(method, path, **kwargs):
            return (200, b"{}", {}) if kwargs["role"] == "backend" else (404, body, {})

        with (
            patch.object(self.lab, "identity_control") as identity,
            patch.object(self.lab, "http", side_effect=http) as requests,
        ):
            status = self.lab.api_denial(role, key, "/pulls/1.diff" if text else "", text=text)
        self.assertEqual(status, 404)
        self.assertEqual(identity.call_args_list, [call(role), call(role)])
        self.assertEqual(
            [item.kwargs["role"] for item in requests.call_args_list],
            ["backend", role, "backend"],
        )

    def test_api_404_denial_rejects_json_escaped_and_nested_base64_forbidden_markers(
        self,
    ):
        for key in ("reference", "draft_case", "pending", "solution_b"):
            marker = self.lab.cfg["repos"][key]["marker"].encode()
            bodies = [json.dumps({"message": marker.decode()}).encode().replace(b"PRODGUILD", b"\\u0050RODGUILD")]
            encoded = marker
            for _ in range(lab.SCAN_BASE64_DEPTH):
                encoded = base64.b64encode(encoded)
                bodies.append(
                    json.dumps(
                        {
                            "message": "Not Found",
                            "nested": [{"content": encoded.decode()}],
                        }
                    ).encode()
                )
            # Decode a base64 JSON envelope, then a second JSON-escaped payload.
            bodies.append(base64.b64encode(bodies[0]))
            bodies[-1] = json.dumps({"payload": bodies[-1].decode()}).encode()
            for body in bodies:
                with (
                    self.subTest(key=key, body=body),
                    self.assertRaisesRegex(lab.LabError, "private_marker_in_denial"),
                ):
                    self.denial(body)

        # Noncanonical unused padding bits must not hide a second encoded layer.
        marker = self.lab.cfg["repos"]["reference"]["marker"].encode()
        encoded = base64.b64encode(base64.b64encode(marker)).rstrip(b"=")
        alphabet = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
        if len(encoded) % 4 in (2, 3):
            encoded = encoded[:-1] + bytes([alphabet[alphabet.index(encoded[-1]) | 1]])
        with self.assertRaisesRegex(lab.LabError, "private_marker_in_denial"):
            self.denial(json.dumps({"message": "Not Found", "content": encoded.decode()}).encode())

    def test_all_actor_forbidden_markers_are_scanned_and_authorized_markers_are_excluded(
        self,
    ):
        forbidden = {
            "student_a": ("reference", "draft_case", "pending", "solution_b"),
            "student_b": ("reference", "draft_case", "pending", "solution_a"),
            "organizer": ("pending", "solution_a", "solution_b"),
            "reviewer": ("reference", "draft_case", "pending"),
            "expert": ("reference", "draft_case", "pending"),
        }
        for role, keys in forbidden.items():
            for key, repo in self.lab.cfg["repos"].items():
                body = json.dumps({"message": repo["marker"]}).encode()
                with self.subTest(role=role, marker=key):
                    if key in keys:
                        with self.assertRaisesRegex(lab.LabError, "private_marker_in_denial"):
                            self.denial(body, role=role, key=keys[0])
                    else:
                        self.denial(body, role=role, key=keys[0])

    def test_escaped_json_keys_and_nested_json_strings_are_scanned(self):
        marker = self.lab.cfg["repos"]["solution_b"]["marker"]
        for body in (
            json.dumps({marker: "Not Found"}).encode().replace(b"PRODGUILD", b"\\u0050RODGUILD"),
            json.dumps({"nested": json.dumps({"body": marker}).replace("PRODGUILD", "\\u0050RODGUILD")}).encode(),
            json.dumps({base64.b64encode(marker.encode()).decode(): "Not Found"}).encode(),
        ):
            with (
                self.subTest(body=body),
                self.assertRaisesRegex(lab.LabError, "private_marker_in_denial"),
            ):
                self.denial(body)

    def test_base64_json_byte_order_mark_cannot_bypass_nested_inspection(self):
        message = json.dumps(self.lab.cfg["repos"]["solution_b"]["marker"]).replace("PRODGUILD", "\\u0050RODGUILD")
        for encoding in ("utf-8-sig", "utf-16", "utf-32"):
            body = json.dumps({"content": base64.b64encode(message.encode(encoding)).decode()}).encode()
            with (
                self.subTest(encoding=encoding),
                self.assertRaisesRegex(lab.LabError, "private_marker_in_denial"),
            ):
                self.denial(body)

    def test_api_denial_requires_recognizable_json_error_envelope(self):
        for body in (b"null", b"[]", b"{}", b'{"message":404}'):
            with (
                self.subTest(body=body),
                self.assertRaisesRegex(lab.LabError, "denial_body_unrecognized"),
            ):
                self.denial(body)

    def test_matrix_diff_denial_body_is_scanned_and_bracketed_by_controls(self):
        requests = []

        def http(method, path, **kwargs):
            role = kwargs.get("role")
            requests.append((role, path))
            if role == "backend":
                return 200, b"{}", {}
            body = b'{"message":"Not Found"}'
            if role == "student_b" and path.endswith("/solution-a/pulls/1.diff"):
                body = b"diff --git a/probe.txt b/probe.txt\n+" + self.lab.cfg["repos"]["solution_a"]["marker"].encode()
            return 404, body, {}

        with (
            patch.object(self.lab, "identity_control"),
            patch.object(self.lab, "http", side_effect=http),
            patch.object(self.lab, "web_denial", return_value=404),
            patch.object(self.lab, "clone_denial", return_value={}),
            redirect_stderr(io.StringIO()),
        ):
            self.lab.permission_matrix()
        row = next(row for row in self.lab.rows if row["id"] == "acl.api.student_b.solution_a")
        self.assertEqual(
            row,
            {"id": row["id"], "status": "failed", "code": "private_marker_in_denial"},
        )
        target = lab.API + self.lab.repo_path("solution_b") + "/pulls/1.diff"
        index = requests.index(("student_a", target))
        self.assertEqual(
            requests[index - 1 : index + 2],
            [("backend", target), ("student_a", target), ("backend", target)],
        )

    def test_diff_denial_scans_json_and_base64_as_well_as_plaintext(self):
        marker = self.lab.cfg["repos"]["pending"]["marker"].encode()
        for body in (
            marker,
            base64.b64encode(base64.b64encode(marker)),
            json.dumps(marker.decode()).encode(),
        ):
            with (
                self.subTest(body=body),
                self.assertRaisesRegex(lab.LabError, "private_marker_in_denial"),
            ):
                self.denial(body, text=True)

    def test_diff_404_denial_scans_actual_diff_framed_json_base64_and_escaped_lines(
        self,
    ):
        for key in ("pending", "reference", "solution_b"):
            for body in self.framed_leaks(self.lab.cfg["repos"][key]["marker"]):
                with (
                    self.subTest(key=key, body=body),
                    self.assertRaisesRegex(lab.LabError, "private_marker_in_denial"),
                ):
                    self.denial(body, text=True)

    def test_api_404_declared_unpadded_base64_and_bomless_wide_json_are_scanned(self):
        marker = self.lab.cfg["repos"]["solution_b"]["marker"]
        encoded = base64.b64encode((marker + "xx").encode()).decode().rstrip("=")
        self.assertNotEqual(len(encoded) % 4, 0)
        bodies = [json.dumps({"message": "Not Found", "encoding": "base64", "content": encoded}).encode()]
        wrapped = "\n".join(encoded[index : index + 12] for index in range(0, len(encoded), 12))
        bodies.append(json.dumps({"message": "Not Found", "content": wrapped}).encode())
        for encoding in ("utf-16-be", "utf-16-le", "utf-32-be", "utf-32-le"):
            escaped = json.dumps({"content": marker}).replace("PRODGUILD", "\\u0050RODGUILD").encode(encoding)
            bodies.append(
                json.dumps(
                    {
                        "message": "Not Found",
                        "content": base64.b64encode(escaped).decode(),
                    }
                ).encode()
            )
        for body in bodies:
            with (
                self.subTest(body=body),
                self.assertRaisesRegex(lab.LabError, "private_marker_in_denial"),
            ):
                self.denial(body)

    def test_declared_unknown_or_malformed_encoding_and_diff_bounds_fail_closed(self):
        for payload, code in (
            ({"encoding": "base64", "content": "%%%"}, "marker_scan_invalid_base64"),
            ({"encoding": "base64", "content": "A"}, "marker_scan_invalid_base64"),
            ({"encoding": "base64", "content": "Zh"}, "marker_scan_ambiguous_base64"),
            ({"encoding": "base64", "content": []}, "marker_scan_invalid_base64"),
            (
                {"encoding": "base64url", "content": "safe"},
                "marker_scan_unsupported_encoding",
            ),
            (
                {"encoding": "gzip", "content": "safe"},
                "marker_scan_unsupported_encoding",
            ),
        ):
            body = b"diff --git a/a b/a\n+" + json.dumps(payload).encode()
            with (
                self.subTest(payload=payload),
                self.assertRaisesRegex(lab.LabError, code),
            ):
                self.denial(body, text=True)
        for body, code in (
            (b"diff --git a/a b/a\n+{\n+malformed", "marker_scan_invalid_json"),
            (b"diff --git a/a b/a\n+\\u00ZZ", "marker_scan_invalid_escape"),
            (b"diff --git a/a b/a\n+\\x50RODGUILD", "marker_scan_unsupported_escape"),
        ):
            with self.subTest(body=body), self.assertRaisesRegex(lab.LabError, code):
                self.denial(body, text=True)
        with (
            patch.object(lab, "SCAN_NODES", 8),
            self.assertRaisesRegex(lab.LabError, "marker_scan_structure_limit"),
        ):
            self.denial(b"diff --git a/a b/a\n" + b"+ordinary line\n" * 9, text=True)

    def test_framed_own_marker_is_allowed_and_clean_declared_base64_accepts_optional_padding(
        self,
    ):
        for body in self.framed_leaks(self.lab.cfg["repos"]["solution_a"]["marker"]):
            self.denial(body, text=True)
        for content in ("", base64.b64encode(b"ordinary text.").decode().rstrip("=")):
            self.denial(json.dumps({"message": "Not Found", "encoding": "base64", "content": content}).encode())
        self.denial(
            json.dumps(
                {
                    "message": "Not Found",
                    "branch": "prodguild/submissions/dabd4d93ce932157542c12640aa9ebf7",
                    "sha": "e3" + "a" * 38,
                }
            ).encode()
        )

    def test_api_denial_fails_closed_on_body_structure_and_decoding_bounds(self):
        encoded = b"ordinary text."
        for _ in range(lab.SCAN_BASE64_DEPTH + 1):
            encoded = base64.b64encode(encoded)
        cases = (
            (b" " * (lab.BODY_LIMIT + 1), "marker_scan_body_limit"),
            (
                b"[" * (lab.SCAN_DEPTH + 1) + b"0" + b"]" * (lab.SCAN_DEPTH + 1),
                "marker_scan_structure_limit",
            ),
            (json.dumps([0] * lab.SCAN_NODES).encode(), "marker_scan_structure_limit"),
            (
                json.dumps({"payload": encoded.decode()}).encode(),
                "marker_scan_encoding_limit",
            ),
            (
                b'{"message":"Not Found","message":"different"}',
                "marker_scan_ambiguous_json",
            ),
            (b'{"message":NaN}', "marker_scan_ambiguous_json"),
            (b'{"message":"\\ud800"}', "marker_scan_invalid_text"),
            (b'{"payload":"Zh=="}', "marker_scan_ambiguous_base64"),
            (b'{"payload":"{invalid JSON}"}', "marker_scan_invalid_json"),
            (
                b"[" * 2000 + b"0" + b"]" * 2000,
                "marker_scan_(invalid_json|structure_limit)",
            ),
            (b"Not JSON", "marker_scan_invalid_json"),
        )
        for body, code in cases:
            with self.subTest(code=code), self.assertRaisesRegex(lab.LabError, code):
                self.denial(body)
        with (
            patch.object(lab, "SCAN_BYTES", 64),
            self.assertRaisesRegex(lab.LabError, "marker_scan_byte_limit"),
        ):
            self.denial(json.dumps({"payload": "x" * 60}).encode())

    def test_deep_but_bounded_clean_base64_is_allowed(self):
        encoded = b"ordinary text."
        for _ in range(lab.SCAN_BASE64_DEPTH):
            encoded = base64.b64encode(encoded)
        self.denial(json.dumps({"message": "Not Found", "payload": encoded.decode()}).encode())

    def test_denial_failure_emits_only_fixed_code_not_encoded_body_or_credentials(self):
        secret = self.lab.cfg["accounts"]["student_a"]["token"]
        marker = self.lab.cfg["repos"]["solution_b"]["marker"]
        body = json.dumps({"message": marker + secret}).encode()
        with redirect_stderr(io.StringIO()) as errors:
            self.assertFalse(self.lab.check("offline.denial", lambda: self.denial(body)))
        evidence = json.dumps(self.lab.rows) + errors.getvalue()
        self.assertNotIn(marker, evidence)
        self.assertNotIn(secret, evidence)
        self.assertIn("private_marker_in_denial", evidence)

    def test_web_denial_uses_actor_policy_instead_of_only_target_marker(self):
        marker = self.lab.cfg["repos"]["solution_b"]["marker"].encode()
        with (
            patch.object(self.lab, "identity_control"),
            patch.object(self.lab, "web", side_effect=lambda role: role),
            patch.object(
                self.lab,
                "http",
                side_effect=[(200, b"", {}), (200, b"", {}), (404, marker, {})],
            ),
            self.assertRaisesRegex(lab.LabError, "web_private_marker_visible"),
        ):
            self.lab.web_denial("student_a", "reference")

    def discovery(self, leaking_surface):
        marker = self.lab.cfg["repos"]["solution_b"]["marker"]
        leak = json.dumps({"message": base64.b64encode(base64.b64encode(marker.encode())).decode()}).encode()

        def http(method, path, **kwargs):
            role = kwargs.get("role")
            if path.startswith(lab.API + "/user/repos") or path.startswith(lab.API + "/repos/search"):
                values = [{"id": self.lab.cfg["repos"]["solution_a" if role == "student_a" else "solution_b"]["id"]}]
                body = json.dumps({"data": values} if "/repos/search" in path else values).encode()
                if leaking_surface == "discovery" and role == "student_a":
                    body = leak
                return 200, body, {}
            if path == "/explore/repos?q=":
                return 200, b"safe synthetic page", {}
            if path.startswith(lab.API):
                return (
                    404,
                    leak if leaking_surface == "anonymous-api" else b'{"message":"Not Found"}',
                    {},
                )
            return 404, leak if leaking_surface == "anonymous-web" else b"Not Found", {}

        with (
            patch.object(self.lab, "identity_control"),
            patch.object(self.lab, "api_denial", return_value=404),
            patch.object(self.lab, "api", return_value=(200, {}, {})),
            patch.object(self.lab, "web", return_value=object()),
            patch.object(self.lab, "http", side_effect=http),
        ):
            return self.lab.discovery_privacy()

    def test_discovery_and_anonymous_bodies_are_inspected_including_denials(self):
        for surface, code in (
            ("discovery", "private_marker_in_discovery"),
            ("anonymous-api", "anonymous_private_content"),
            ("anonymous-web", "anonymous_private_content"),
        ):
            with (
                self.subTest(surface=surface),
                self.assertRaisesRegex(lab.LabError, code),
            ):
                self.discovery(surface)
        self.assertEqual(self.discovery(None)["anonymous_api_web_denials"], 10)


class NativeDenialCallSiteTests(PolicyLabTest):
    def clone_denial(self, make_result):
        def git(directory, role, args, **kwargs):
            self.assertEqual(args[:2], ["clone", "--bare"])
            return make_result(args[2], args[3])

        with (
            patch.object(self.lab, "identity_control") as identity,
            patch.object(self.lab, "clone", return_value=self.directory) as clone,
            patch.object(self.lab, "git", side_effect=git),
        ):
            details = self.lab.clone_denial("student_a", "reference")
        self.assertEqual(identity.call_count, 2)
        self.assertEqual(
            [item.args for item in clone.call_args_list],
            [
                ("solution_a", "student_a"),
                ("reference", "backend"),
                ("solution_a", "student_a"),
            ],
        )
        return details

    def test_clone_call_site_rejects_54030_transport_auth_storage_and_unrelated_not_found(
        self,
    ):
        factories = (
            lambda url, dest: self.result(f"fatal: unable to access '{url}/': Connection refused"),
            lambda url, dest: self.result(f"fatal: unable to access '{url}/': Recv failure: Connection reset by peer"),
            lambda url, dest: self.result(f"fatal: Authentication failed for '{url}/'"),
            lambda url, dest: self.result("remote: object not found; No space left on device"),
            lambda url, dest: self.result("remote: access denied: unable to write storage"),
            lambda url, dest: self.http_denial(url.replace("reference", "other")),
            lambda url, dest: self.http_denial(url.replace("http://", "http://user:password@")),
            lambda url, dest: self.http_denial(url + "?error=403"),
            lambda url, dest: self.result(self.http_denial(url).stderr + b"fatal: Authentication failed\n"),
            lambda url, dest: self.result(self.http_denial(url).stderr, code=0),
            lambda url, dest: self.result(self.http_denial(url).stderr, stdout=b"unexpected diagnostic"),
        )
        for index, factory in enumerate(factories):
            with (
                self.subTest(index=index),
                self.assertRaisesRegex(lab.LabError, "native_denial_not_authorization"),
            ):
                self.clone_denial(factory)

    def test_clone_call_site_accepts_only_exact_target_signatures_and_sanitizes_evidence(
        self,
    ):
        signatures = (
            (
                lambda url: self.http_denial(url, 403).stderr,
                "git-http-403-exact-repository",
            ),
            (lambda url: self.http_denial(url).stderr, "git-http-404-exact-repository"),
            (
                lambda url: (f"remote: Repository not found.\nfatal: repository '{url}/' not found\n".encode()),
                "git-repository-not-found-exact-repository",
            ),
        )
        for diagnostic, classification in signatures:
            with self.subTest(classification=classification):
                details = self.clone_denial(
                    lambda url, dest: self.result(
                        f"Cloning into bare repository '{dest}'...\n".encode() + diagnostic(url)
                    )
                )
                self.assertEqual(details["denial_classification"], classification)
                self.assertNotIn(self.lab.origin, json.dumps(details))
                self.assertNotIn(str(self.directory), json.dumps(details))

    def protection_result(self, url, refspec):
        source, branch = refspec.split(":refs/heads/")
        reason = (
            "Not allowed to push to protected branch " + branch
            if source
            else "branch " + branch + " is protected from deletion"
        )
        return self.result(
            f"remote: \nremote: Gitea: {reason}\nremote: \nTo {url}\n"
            f" ! [remote rejected] {source or '(delete)'} -> {branch} (pre-receive hook declined)\n"
            f"error: failed to push some refs to '{url}'\n",
            code=1,
        )

    def protected_pushes(self, replace=None, *, fail_at=0, response_leak=None):
        self.lab.cfg["repos"]["scratch"] = {"name": "scratch"}
        repo = self.lab.cfg["repos"]["solution_a"]
        count = 0

        def git(directory, role, args, **kwargs):
            nonlocal count
            if args[-1].endswith(":refs/heads/native-positive"):
                return self.result(b"", code=0)
            result = self.protection_result(args[-2], args[-1])
            if replace is not None and count == fail_at:
                result = replace(result, args[-2], args[-1])
            count += 1
            return result

        def api(method, path, **kwargs):
            if "/contents/" in path:
                return 403, {"message": "Cannot commit to protected branch"}, {}
            if method == "GET" and path.endswith("/branch_protections"):
                return (
                    200,
                    [
                        {
                            "rule_name": branch,
                            "enable_push": False,
                            "enable_force_push": False,
                            "block_admin_merge_override": True,
                            "enable_merge_whitelist": True,
                        }
                        for branch in (repo["head_ref"], repo["base_ref"])
                    ],
                    {},
                )
            if method == "DELETE":
                return 403, {"message": "branch protected"}, {}
            if path.endswith("/pulls"):
                return 201, {"number": 99}, {}
            if path.endswith("/pulls/1/merge"):
                return 405, {"message": "User not allowed to merge PR"}, {}
            if method == "PATCH" and "/branch_protections/" in path:
                return 403, {"message": "Forbidden"}, {}
            return 200, {"merged": False}, {}

        def http(method, path, **kwargs):
            status, body, headers = api(method, path.removeprefix(lab.API), **kwargs)
            if response_leak:
                body = response_leak(method, path, kwargs["role"], body)
            return status, json.dumps(body).encode(), headers

        with (
            patch.object(self.lab, "grant") as grant,
            patch.object(self.lab, "clone", return_value=self.directory),
            patch.object(self.lab, "native_child", return_value="c" * 40),
            patch.object(self.lab, "git", side_effect=git),
            patch.object(
                self.lab,
                "branch_sha",
                side_effect=lambda key, ref: (repo["head"] if ref == repo["head_ref"] else repo["base"]),
            ),
            patch.object(self.lab, "file", return_value={"sha": "f" * 40}),
            patch.object(self.lab, "http", side_effect=http),
            patch.object(self.lab, "branch"),
        ):
            try:
                return self.lab.refs_protected()
            finally:
                self.assertEqual(
                    grant.call_args_list,
                    [
                        call("solution_a", "student_a", "write"),
                        call("solution_a", "student_a", "read"),
                    ],
                )

    def test_protected_regular_force_and_delete_call_sites_reject_nonprotection_failures(
        self,
    ):
        replacements = (
            lambda result, url, ref: self.result("remote: protected storage failed: No space left on device", code=1),
            lambda result, url, ref: self.result(f"fatal: Authentication failed for '{url}/'"),
            lambda result, url, ref: self.result(f"fatal: unable to access '{url}/': Connection refused"),
            lambda result, url, ref: self.result(
                f"fatal: unable to access '{url}/': Recv failure: Connection reset by peer"
            ),
            lambda result, url, ref: self.http_denial(url, 403),
            lambda result, url, ref: self.result(result.stderr + b"remote: No space left on device\n", code=1),
            lambda result, url, ref: self.result(result.stderr.replace(b"prodguild/", b"other/"), code=1),
            lambda result, url, ref: self.result(
                result.stderr.replace(url.encode(), (url + "-wrong").encode()), code=1
            ),
            lambda result, url, ref: self.result(result.stderr.replace(b"Gitea: ", b"hook: "), code=1),
            lambda result, url, ref: self.result(result.stderr, code=0),
        )
        for fail_at in range(6):
            for index, replace in enumerate(replacements):
                with (
                    self.subTest(fail_at=fail_at, index=index),
                    self.assertRaisesRegex(lab.LabError, "protected_native_write_not_denied"),
                ):
                    self.protected_pushes(replace, fail_at=fail_at)

    def test_exact_protected_push_delete_signatures_emit_only_fixed_classifications(
        self,
    ):
        details = self.protected_pushes()
        self.assertEqual(details["native_denials"], 6)
        self.assertEqual(
            details["native_denial_classifications"],
            ["gitea-protected-push", "gitea-protected-push", "gitea-protected-delete"] * 2,
        )
        self.assertNotIn(self.lab.origin, json.dumps(details))
        self.assertNotIn("remote:", json.dumps(details))

    def frozen_pushes(self, make_result, *, response_leak=None):
        self.lab.cfg["repos"]["scratch"] = {"name": "scratch"}

        def git(directory, role, args, **kwargs):
            if "/scratch.git" in args[1]:
                return self.result(b"", code=0)
            return make_result(args[1])

        def http(method, path, **kwargs):
            body = {"has_pull_requests": False} if method == "GET" else {"message": "Cannot commit to protected branch"}
            if response_leak:
                body = response_leak(method, path, kwargs["role"], body)
            return (200 if method == "GET" else 403), json.dumps(body).encode(), {}

        with (
            patch.object(self.lab, "identity_control"),
            patch.object(self.lab, "clone", return_value=self.directory),
            patch.object(self.lab, "native_child", return_value="c" * 40),
            patch.object(self.lab, "branch_sha", return_value="a" * 40),
            patch.object(self.lab, "file", return_value={"sha": "f" * 40}),
            patch.object(self.lab, "http", side_effect=http),
            patch.object(self.lab, "git", side_effect=git),
        ):
            return self.lab.frozen_materials()

    def test_frozen_push_call_site_rejects_storage_auth_and_wrong_target_errors(self):
        for factory in (
            lambda url: self.result("remote: unable to write object: No space left on device"),
            lambda url: self.result("fatal: failed to write object: Permission denied"),
            lambda url: self.result(f"fatal: unable to access '{url}/': Connection refused"),
            lambda url: self.result(f"fatal: unable to access '{url}/': Recv failure: Connection reset by peer"),
            lambda url: self.result(f"fatal: Authentication failed for '{url}/'"),
            lambda url: self.result("remote: protected storage failed"),
            lambda url: self.http_denial(url.replace("starter", "other"), 403),
            lambda url: self.result(self.http_denial(url, 403).stderr + b"remote: No space left on device"),
        ):
            with self.assertRaisesRegex(lab.LabError, "frozen_native_denial_not_authorization"):
                self.frozen_pushes(factory)

    def test_frozen_push_exact_write_acl_response_emits_fixed_classification(self):
        details = self.frozen_pushes(
            lambda url: self.result(
                b"remote: User permission denied for writing.\n" + self.http_denial(url, 403).stderr
            )
        )
        self.assertEqual(
            details["native_denial_classifications"],
            ["git-http-403-exact-repository"] * 3,
        )

    def test_owner_observed_release_diagnostics_at_actual_clone_and_push_callsites(
        self,
    ):
        details = self.clone_denial(
            lambda url, dest: self.result(
                f"Cloning into bare repository '{dest}'...\nremote: Repository not found\n"
                f"fatal: repository '{url}/' not found\n"
            )
        )
        self.assertEqual(
            details["denial_classification"],
            "git-repository-not-found-exact-repository",
        )

        def observed_protection(url, refspec):
            source, branch = refspec.split(":refs/heads/")
            reason = (
                f"Not allowed to push to protected branch {branch}"
                if source
                else f"Branch {branch} is protected from deletion"
            )
            ref = f"{source} -> {branch}" if source else branch
            if source == "b" * 40:
                reason = f"Branch {branch} is protected from force push"
            return self.result(
                f"remote: \nremote: error:        \nremote: error: {reason}        \nremote: error:        \n"
                f"To {url}\n ! [remote rejected] {ref} (pre-receive hook declined)\n"
                f"error: failed to push some refs to '{url}'\n",
                code=1,
            )

        with patch.object(self, "protection_result", side_effect=observed_protection):
            self.assertEqual(self.protected_pushes()["native_denials"], 6)
        details = self.frozen_pushes(
            lambda url: self.result(
                "remote: \nremote: error:        \nremote: error: User permission denied for writing.        \n"
                f"remote: error:        \nTo {url}\n ! [remote rejected] {'c' * 40} -> main (pre-receive hook declined)\n"
                f"error: failed to push some refs to '{url}'\n",
                code=1,
            )
        )
        self.assertEqual(
            details["native_denial_classifications"],
            ["gitea-write-acl-exact-repository-ref"] * 3,
        )

    def test_protected_write_delete_rule_and_merge_denials_scan_actual_response_bytes(
        self,
    ):
        for method, suffix in (
            ("PUT", "/contents/probe.txt"),
            ("DELETE", "/branches/prodguild%2Fsubmissions%2Foffline"),
            ("PATCH", "/branch_protections/prodguild%2Fsubmissions%2Foffline"),
            ("POST", "/pulls/1/merge"),
        ):
            for key in ("pending", "reference", "solution_b"):
                marker = self.lab.cfg["repos"][key]["marker"]

                def leak(verb, path, role, body):
                    if verb == method and path.endswith(suffix) and role == "student_a":
                        return {
                            **body,
                            "content": base64.b64encode(base64.b64encode(marker.encode())).decode(),
                        }
                    return body

                with (
                    self.subTest(method=method, key=key),
                    self.assertRaisesRegex(lab.LabError, "private_marker_in_response"),
                ):
                    self.protected_pushes(response_leak=leak)

    def test_protected_mutation_response_allows_own_solution_marker(self):
        marker = self.lab.cfg["repos"]["solution_a"]["marker"]
        self.protected_pushes(
            response_leak=lambda method, path, role, body: (
                {**body, "content": marker} if isinstance(body, dict) else body
            )
        )

    def test_frozen_write_denial_uses_recipient_policy_including_starter_own_solution(
        self,
    ):
        for key, marker_key in (
            ("starter", "solution_a"),
            ("reference", "solution_b"),
            ("draft-case", "pending"),
        ):
            marker = self.lab.cfg["repos"][marker_key]["marker"]

            def leak(method, path, role, body):
                return {**body, "content": marker} if method == "PUT" and f"/{key}/contents/" in path else body

            with (
                self.subTest(key=key),
                self.assertRaisesRegex(lab.LabError, "private_marker_in_response"),
            ):
                self.frozen_pushes(lambda url: self.http_denial(url, 403), response_leak=leak)


class ResponseBoundarySafetyTests(PolicyLabTest):
    def test_http_scans_denial_bytes_before_status_acceptance_and_json_parsing(self):
        marker = self.lab.cfg["repos"]["pending"]["marker"]
        raw = (
            json.dumps({"message": "Cannot commit", "content": marker})
            .encode()
            .replace(b"PRODGUILD", b"\\u0050RODGUILD")
        )
        for status in (200, 403, 404, 405, 500):
            with (
                self.subTest(status=status),
                patch.object(
                    self.lab.opener,
                    "open",
                    return_value=http_response(self.lab.origin, status=status, body=raw),
                ),
                patch.object(self.lab, "inspect_response", wraps=self.lab.inspect_response) as inspect,
                self.assertRaisesRegex(lab.LabError, "private_marker_in_response"),
            ):
                self.lab.api(
                    "DELETE",
                    self.lab.repo_path("solution_a") + "/branches/main",
                    role="student_a",
                    expected=(status,),
                )
            inspect.assert_called_once()

    def test_backend_control_and_actor_denial_have_distinct_marker_policies(self):
        raw = json.dumps({"message": self.lab.cfg["repos"]["reference"]["marker"]}).encode()
        with patch.object(
            self.lab.opener,
            "open",
            side_effect=lambda *a, **k: http_response(self.lab.origin, body=raw),
        ):
            self.lab.api("GET", self.lab.repo_path("reference"), role="backend")
            self.lab.api("GET", self.lab.repo_path("reference"), role="organizer")
            with self.assertRaisesRegex(lab.LabError, "private_marker_in_response"):
                self.lab.api("GET", self.lab.repo_path("reference"), role="student_a")

    def test_cookie_web_post_denial_scans_before_returning_body_to_caller(self):
        marker = self.lab.cfg["repos"]["solution_b"]["marker"]
        for operation in ("/_edit/main/probe.txt", "/branches/delete"):
            with (
                self.subTest(operation=operation),
                patch.object(self.lab, "web", return_value=self.lab.opener),
                patch.object(
                    self.lab.opener,
                    "open",
                    return_value=http_response(
                        self.lab.origin,
                        status=403,
                        body=("protected " + marker).encode(),
                    ),
                ),
                self.assertRaisesRegex(lab.LabError, "private_marker_in_response"),
            ):
                self.lab.web_post(
                    "/" + self.lab.cfg["namespace"] + "/solution-a" + operation,
                    "student_a",
                    {},
                    expected=(403,),
                )

    def test_missing_cookie_recipient_and_mismatched_token_recipient_fail_before_transport(
        self,
    ):
        for credentials, code in (
            ({"cookie": self.lab.opener}, "explicit_response_actor_required"),
            ({"basic": ("offline", "offline")}, "explicit_response_actor_required"),
            (
                {"role": "student_a", "response_role": "backend"},
                "response_actor_mismatch",
            ),
        ):
            with (
                patch.object(self.lab.opener, "open") as opened,
                self.assertRaisesRegex(lab.LabError, code),
            ):
                self.lab.http("GET", "/user/login", **credentials)
            opened.assert_not_called()

    def test_unknown_content_encoding_is_not_treated_as_clean(self):
        with (
            patch.object(
                self.lab.opener,
                "open",
                return_value=http_response(
                    self.lab.origin,
                    body=b"opaque",
                    headers={"Content-Encoding": "gzip"},
                ),
            ),
            self.assertRaisesRegex(lab.LabError, "marker_scan_unsupported_content_encoding"),
        ):
            self.lab.http("GET", "/user/login", role="student_a")

    def test_annotation_patch_and_delete_denials_scan_actual_api_bodies(self):
        repo = self.lab.cfg["repos"]["solution_a"]
        repo.update(canonical_comment=9, canonical_body=repo["marker"])
        marker = self.lab.cfg["repos"]["pending"]["marker"]
        for denied_method in ("PATCH", "DELETE"):

            def http(method, path, **kwargs):
                role = kwargs["role"]
                body = {
                    "body": repo["canonical_body"],
                    "id": 10,
                    "user": {"id": self.lab.cfg["accounts"]["backend"]["id"]},
                }
                if role == "student_a" and method == denied_method:
                    body = {
                        "message": "Forbidden",
                        "encoding": "base64",
                        "content": base64.b64encode(marker.encode()).decode().rstrip("="),
                    }
                return (
                    (403 if method in ("PATCH", "DELETE") and role == "student_a" else 200),
                    json.dumps(body).encode(),
                    {},
                )

            with (
                self.subTest(method=denied_method),
                patch.object(self.lab, "http", side_effect=http),
                patch.object(self.lab, "branch_sha", return_value=repo["head"]),
                self.assertRaisesRegex(lab.LabError, "private_marker_in_response"),
            ):
                self.lab.annotation_authority()


class EvidenceSafetyTests(OfflineLabTest):
    def test_public_projection_omits_credentials_and_preserves_scope_limits(self):
        self.lab.rows = [
            {
                "id": "offline.synthetic",
                "status": "blocked",
                "code": "offline_test_only",
            }
        ]
        destination = self.directory / "public.json"
        self.lab.write_evidence(destination)
        text = destination.read_text()
        document = json.loads(text)
        self.assertEqual(document["rows"], self.lab.rows)
        for field in (
            "local_matrix_passed",
            "production_ready",
            "application_domain_runtime_tested",
            "downstream_released",
        ):
            self.assertIs(document[field], False)
        for account in (*self.lab.cfg["accounts"].values(), self.lab.cfg["bootstrap"]):
            self.assertNotIn(account["password"], text)
            if "token" in account:
                self.assertNotIn(account["token"], text)
        self.assertNotIn("accounts", document)
        self.assertNotIn("bootstrap", document)
        native = document["native_denial_policy"]
        self.assertEqual(native["version"], lab.NATIVE_DENIAL_POLICY)
        self.assertIn("exact credential-free repository", native["matching"])
        marker = document["marker_scan_policy"]
        self.assertEqual(marker["version"], lab.MARKER_POLICY)
        self.assertEqual(marker["starter_additionally_excludes"], ["solution_a", "solution_b"])
        self.assertEqual(
            marker["actor_forbidden_repositories"]["student_a"],
            ["reference", "draft_case", "solution_b", "pending"],
        )
        self.assertEqual(
            marker["bounds"],
            {
                "body_bytes": lab.BODY_LIMIT,
                "aggregate_scanned_bytes": lab.SCAN_BYTES,
                "structure_depth": lab.SCAN_DEPTH,
                "structure_nodes": lab.SCAN_NODES,
                "nested_base64_layers": lab.SCAN_BASE64_DEPTH,
            },
        )
        self.assertEqual(marker["exhausted_bounds_or_ambiguous_encoding"], "fail closed")

    def assert_secret_projection_refused(self, secret):
        destination = self.directory / "public.json"
        previous = b"previous sanitized test evidence\n"
        destination.write_bytes(previous)
        self.lab.rows = [
            {
                "id": "offline.synthetic",
                "status": "blocked",
                "details": [{"nested": secret}],
            }
        ]
        with (
            redirect_stdout(io.StringIO()) as output,
            redirect_stderr(io.StringIO()) as errors,
            self.assertRaises(lab.LabError) as caught,
        ):
            self.lab.write_evidence(destination)
        self.assertEqual(destination.read_bytes(), previous)
        self.assertNotIn(secret, output.getvalue() + errors.getvalue() + str(caught.exception))

    def test_account_passwords_and_tokens_cannot_enter_nested_evidence(self):
        for role, account in self.lab.cfg["accounts"].items():
            for field in ("password", "token"):
                with self.subTest(role=role, field=field):
                    self.assert_secret_projection_refused(account[field])

    def test_bootstrap_password_cannot_enter_evidence(self):
        self.assert_secret_projection_refused(self.lab.cfg["bootstrap"]["password"])

    def test_json_escaping_cannot_hide_secret_from_projection_guard(self):
        for secret in (
            'offline-quote-"-password',
            "offline-backslash-\\-password",
            "offline-newline-\n-password",
        ):
            with self.subTest(secret=repr(secret)):
                self.lab.cfg["accounts"]["backend"]["password"] = secret
                self.assert_secret_projection_refused(secret)


class ProcessSafetyTests(unittest.TestCase):
    def test_subprocess_has_bounded_default_and_explicit_timeout_and_captured_output(
        self,
    ):
        for overrides in ({}, {"timeout": 7}):
            command = ["/usr/bin/git", "status"]
            result = subprocess.CompletedProcess(command, 0, b"offline-output", b"offline-stderr")
            with patch.object(subprocess, "run", return_value=result) as run:
                self.assertIs(lab.process(command, data=b"offline-stdin", **overrides), result)
            options = run.call_args.kwargs
            self.assertGreater(options["timeout"], 0)
            self.assertLessEqual(options["timeout"], 90)
            if overrides:
                self.assertEqual(options["timeout"], 7)
            self.assertEqual(options["input"], b"offline-stdin")
            self.assertEqual(options["stdout"], subprocess.PIPE)
            self.assertEqual(options["stderr"], subprocess.PIPE)
            self.assertFalse(options.get("shell", False))

    def test_nonzero_exit_is_sanitized_without_printing_command_or_output(self):
        secret = "offline-sensitive-command-and-output"
        command = ["/usr/bin/git", secret]
        result = subprocess.CompletedProcess(command, 128, secret.encode(), secret.encode())
        with (
            patch.object(subprocess, "run", return_value=result),
            redirect_stdout(io.StringIO()) as output,
            redirect_stderr(io.StringIO()) as errors,
            self.assertRaises(lab.LabError) as caught,
        ):
            lab.process(command)
        self.assertNotIn(secret, str(caught.exception) + output.getvalue() + errors.getvalue())

    def test_explicitly_allowed_nonzero_preserves_result_for_caller_classification(
        self,
    ):
        result = subprocess.CompletedProcess(["git"], 128, b"", b"synthetic-denial-not-ACL-evidence")
        with patch.object(subprocess, "run", return_value=result):
            observed = lab.process(["/usr/bin/git", "clone"], allowed=(0, 128))
        self.assertIs(observed, result)

    def test_timeout_and_launch_failure_become_sanitized_harness_errors(self):
        secret = "offline-sensitive-command-and-output"
        command = ["/usr/bin/git", secret]
        failures = (
            subprocess.TimeoutExpired(command, 7, output=secret.encode(), stderr=secret.encode()),
            FileNotFoundError(secret),
            PermissionError(secret),
        )
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                with (
                    patch.object(subprocess, "run", side_effect=failure),
                    redirect_stdout(io.StringIO()) as output,
                    redirect_stderr(io.StringIO()) as errors,
                    self.assertRaises(lab.LabError) as caught,
                ):
                    lab.process(command, timeout=7)
                self.assertNotIn(
                    secret,
                    str(caught.exception) + output.getvalue() + errors.getvalue(),
                )


if __name__ == "__main__":
    unittest.main()

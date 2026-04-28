"""
SSH connection and remote command execution.
Used the bot to run SwiftBox checks on a user's server
without installing anything on that server.

Usage:
    with SSHExecutor.from_config(host_config) as ssh:
        stdout, stderr, exit_code = ssh.run("df -h /")
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING
import paramiko

if TYPE_CHECKING:
    from core.schemas import HostConfig

logger = logging.getLogger(__name__)


class SSHExecutor:
    def __init__(
        self,
        host: str,
        user: str,
        port: int = 22,
        key_path: str | None = None,
        key_content: str | None = None,   # raw private key string (for bot use)
        password: str | None = None,
        timeout: int = 15,
    ) -> None:
        self.host = host
        self.user = user
        self.port = port
        self.key_path = key_path
        self.key_content = key_content
        self.password = password
        self.timeout = timeout
        self._client: paramiko.SSHClient | None = None

    @classmethod
    def from_config(cls, host_config: HostConfig) -> SSHExecutor:
        """Build an SSHExecutor from a loaded HostConfig."""
        ssh = host_config.ssh_config
        if not ssh:
            raise ValueError(f"Host {host_config.name} has no ssh config block")
        return cls(
            host=ssh["host"],
            user=ssh.get("user", "root"),
            port=int(ssh.get("port", 22)),
            key_path=ssh.get("key_path"),
            key_content=ssh.get("key_content"),
            password=ssh.get("password"),
            timeout=int(ssh.get("timeout", 15)),
        )

    def connect(self) -> None:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        connect_kwargs: dict = {
            "hostname": self.host,
            "username": self.user,
            "port": self.port,
            "timeout": self.timeout,
            "look_for_keys": False,
            "allow_agent": False,
        }

        if self.key_content:
            import io
            pkey = paramiko.RSAKey.from_private_key(io.StringIO(self.key_content))
            connect_kwargs["pkey"] = pkey
        elif self.key_path:
            connect_kwargs["key_filename"] = str(Path(self.key_path).expanduser())
        elif self.password:
            connect_kwargs["password"] = self.password
        else:
            raise ValueError("SSH executor requires key_path, key_content, or password")

        client.connect(**connect_kwargs)
        self._client = client
        logger.info("SSH connected: %s@%s:%s", self.user, self.host, self.port)

    def run(self, command: str) -> tuple[str, str, int]:
        """
        Run a command on the remote host.
        Returns (stdout, stderr, exit_code).
        Never raises on non-zero exit — callers decide what to do.
        """
        if self._client is None:
            raise RuntimeError("SSHExecutor not connected. Use as context manager.")

        logger.debug("SSH run: %s", command)
        _, stdout, stderr = self._client.exec_command(command, timeout=self.timeout)
        exit_code = stdout.channel.recv_exit_status()
        return stdout.read().decode().strip(), stderr.read().decode().strip(), exit_code

    def close(self) -> None:
        if self._client:
            self._client.close()
            self._client = None
            logger.debug("SSH disconnected: %s", self.host)

    def __enter__(self) -> SSHExecutor:
        self.connect()
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def test_connection(self) -> tuple[bool, str]:
        """
        Try to connect and run a no-op command.
        Returns (success, message). Safe to call before a full scan.
        """
        try:
            self.connect()
            stdout, _, exit_code = self.run("echo ok")
            self.close()
            if exit_code == 0 and stdout.strip() == "ok":
                return True, f"Connected to {self.host} as {self.user}"
            return False, f"Connected but echo failed (exit {exit_code})"
        except paramiko.AuthenticationException:
            return False, "Authentication failed — check key or credentials"
        except (TimeoutError, OSError):
            return False, f"Connection to {self.host} timed out or refused"
        except paramiko.SSHException as e:
            return False, f"SSH error: {e}"
        except Exception as e:  # noqa: BLE001
            return False, f"Connection error: {e}"
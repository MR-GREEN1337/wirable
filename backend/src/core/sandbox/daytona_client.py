"""
Thin async Daytona wrapper for Wirable.

Each audit/fix job gets an isolated sandbox:
  async with DaytonaClient.sandbox() as sb:
      await sb.upload("/task.md", prompt.encode())
      await sb.exec("opencode run --task /task.md", timeout=900)
      data = await sb.read("/output.json")
"""
from __future__ import annotations

import asyncio
import os
import shlex
import uuid
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from loguru import logger

try:
    from daytona import (
        AsyncDaytona,
        DaytonaConfig,
        FileUpload,
        SessionExecuteRequest,
    )
    from daytona import CreateSandboxFromImageParams
    DAYTONA_AVAILABLE = True
except ImportError:
    DAYTONA_AVAILABLE = False
    logger.warning("daytona package not installed — sandbox ops will raise")

from ...core.config import settings
from ...core.llm import key_pool

# Image baked in harness/Dockerfile and pushed to a registry before the demo.
# Fallback: official python-dev snapshot in Daytona cloud.
HARNESS_IMAGE = "agentready-harness:latest"
FALLBACK_SNAPSHOT = "python"


def _resolve_image() -> str:
    """Sandbox image name, env-overridable.

    Mirrors Crossnode's CROSSNODE_SANDBOX_IMAGE pattern: prefer the
    AGENTREADY_SANDBOX_IMAGE env var, fall back to the baked HARNESS_IMAGE
    constant. (The snapshot fallback in sandbox() handles the case where the
    resolved image is unavailable in the Daytona registry.)
    """
    return os.getenv("AGENTREADY_SANDBOX_IMAGE") or HARNESS_IMAGE

_AUTOSTOP_MINUTES = 30  # sandbox auto-stops after this many idle minutes


class SandboxHandle:
    """Thin handle over a live Daytona sandbox."""

    def __init__(
        self,
        client: "AsyncDaytona",
        sandbox: object,
        session_id: str,
        env: dict[str, str] | None = None,
    ) -> None:
        self._client = client
        self._sb = sandbox
        self._session = session_id
        # Persisted per-session env. Each execute_session_command may land in a
        # fresh shell, so we prepend `export K='V'; ...` to every command rather
        # than relying on a one-shot export sticking.
        self._env: dict[str, str] = dict(env or {})

    def _env_prefix(self) -> str:
        """Shell-safe `export K='V'; ` prefix injecting the session env."""
        if not self._env:
            return ""
        parts = [
            f"export {k}={shlex.quote(str(v))}"
            for k, v in self._env.items()
            if v != ""
        ]
        return ("; ".join(parts) + "; ") if parts else ""

    async def upload(self, remote_path: str, content: bytes) -> None:
        await self._sb.fs.upload_files([
            FileUpload(source=content, destination=remote_path)
        ])

    async def exec(self, command: str, timeout: int = 600) -> str:
        """Run a command in the persistent session. Returns combined stdout+stderr.

        Daytona's real ``get_session_command_logs_async`` signature for
        daytona==0.179.0 is ``(session_id, command_id, on_stdout, on_stderr) -> None``;
        it delivers output via callbacks rather than returning a string. We poll
        ``get_session_command`` for the exit code (bounded by ``timeout``), then
        collect the buffered logs through callbacks.
        """
        full_command = self._env_prefix() + command
        req = SessionExecuteRequest(command=full_command, run_async=True)
        resp = await self._sb.process.execute_session_command(self._session, req)
        cmd_id: str = resp.cmd_id

        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            try:
                info = await self._sb.process.get_session_command(self._session, cmd_id)
            except Exception as e:  # transient — keep polling until deadline
                logger.debug("get_session_command poll failed: %s", e)
                info = None
            if info is not None and getattr(info, "exit_code", None) is not None:
                break
            if asyncio.get_running_loop().time() > deadline:
                logger.warning("sandbox exec timed out after %ds: %s", timeout, command[:80])
                break
            await asyncio.sleep(2)

        chunks: list[str] = []

        async def _collect(chunk: str) -> None:
            chunks.append(chunk)

        try:
            await self._sb.process.get_session_command_logs_async(
                self._session, cmd_id, _collect, _collect
            )
        except Exception as e:
            logger.debug("sandbox log collection failed for %s: %s", command[:60], e)
        return "".join(chunks)

    async def read(self, remote_path: str, max_bytes: int = 400_000) -> bytes | None:
        """Download a file from the sandbox. Returns None if not found."""
        try:
            result = await self.exec(f"cat {remote_path} 2>/dev/null", timeout=15)
            if result:
                return result[:max_bytes].encode()
            return None
        except Exception as e:
            logger.debug("sandbox read failed for %s: %s", remote_path, e)
            return None

    # ------------------------------------------------------------------
    # Non-blocking exec + binary helpers — used to stream screenshots WHILE
    # a long-running command (the audit) is still in flight. The blocking
    # exec() above remains the path for everything that just needs a result.
    # ------------------------------------------------------------------

    async def exec_bg(self, command: str) -> str:
        """Start a command async and return its cmd_id immediately.

        Unlike exec(), this does NOT wait for completion — the caller polls
        is_command_done(cmd_id). The same session env-export prefix is applied.
        """
        full_command = self._env_prefix() + command
        req = SessionExecuteRequest(command=full_command, run_async=True)
        resp = await self._sb.process.execute_session_command(self._session, req)
        return resp.cmd_id

    async def is_command_done(self, cmd_id: str) -> bool:
        """True once the background command's exit_code is set (non-None)."""
        try:
            info = await self._sb.process.get_session_command(self._session, cmd_id)
        except Exception as e:
            logger.debug("is_command_done poll failed for %s: %s", cmd_id, e)
            return False
        return info is not None and getattr(info, "exit_code", None) is not None

    async def list_files(self, glob: str) -> list[str]:
        """Return sorted file paths matching a sandbox glob (empty on none)."""
        try:
            out = await self.exec(f"ls -1 {glob} 2>/dev/null", timeout=15)
        except Exception as e:
            logger.debug("list_files failed for %s: %s", glob, e)
            return []
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        return sorted(lines)

    async def read_b64(self, path: str) -> str | None:
        """Base64-encode a sandbox file and return the string (None if empty).

        Uses `base64 -w0` inside the sandbox so binary content (e.g. JPEGs)
        survives transport — a plain `cat` would corrupt non-UTF8 bytes.
        """
        try:
            out = await self.exec(f"base64 -w0 {path} 2>/dev/null", timeout=20)
        except Exception as e:
            logger.debug("read_b64 failed for %s: %s", path, e)
            return None
        b64 = "".join(out.split())  # strip any stray whitespace/newlines
        return b64 or None


class DaytonaClient:
    """Factory for SandboxHandle instances."""

    @staticmethod
    def _make_client() -> "AsyncDaytona":
        if not DAYTONA_AVAILABLE:
            raise RuntimeError("daytona package not installed. Run: pip install daytona==0.179.0")
        # Match Crossnode: let the SDK use its default API endpoint. Passing the
        # dashboard URL (app.daytona.io) as server_url makes the SDK hit CloudFront
        # and get a 403 "request method not allowed". Only override if a real,
        # non-default API base is explicitly configured.
        _url = (settings.DAYTONA_SERVER_URL or "").strip()
        if _url and "app.daytona.io" not in _url:
            config = DaytonaConfig(api_key=settings.DAYTONA_API_KEY, server_url=_url)
        else:
            config = DaytonaConfig(api_key=settings.DAYTONA_API_KEY)
        return AsyncDaytona(config)

    @staticmethod
    @asynccontextmanager
    async def sandbox(
        env: dict[str, str] | None = None,
    ) -> AsyncGenerator[SandboxHandle, None]:
        """
        Context manager — creates a sandbox, yields a handle, destroys on exit.

        Args:
            env: Environment variables exported into every command run via the
                handle's ``exec()`` (prepended as a shell-quoted ``export``
                prefix, since each session command may land in a fresh shell).
                When None, a pooled Claude key is auto-injected so the OpenCode
                agent inside the sandbox runs on Anthropic:
                    {"ANTHROPIC_API_KEY": key_pool.next_key(),
                     "ANTHROPIC_MODEL": settings.ANTHROPIC_MODEL}
                — but only if the pool has a key. With no keys, no env is
                injected (the sandbox still boots; the agent just lacks a key).

        Usage:
            async with DaytonaClient.sandbox() as sb:
                await sb.upload("/task.md", b"...")
                await sb.exec("opencode run --task /task.md")
                data = await sb.read("/output.json")
        """
        # Resolve the env to inject. Default = auto-pooled Claude key + model.
        resolved_env: dict[str, str] = dict(env) if env is not None else {}
        if env is None:
            pooled = key_pool.next_key()
            if pooled:
                resolved_env = {
                    "ANTHROPIC_API_KEY": pooled,
                    "ANTHROPIC_MODEL": settings.ANTHROPIC_MODEL,
                }
        # Best-effort: also surface the model via OpenCode's own provider knobs.
        if resolved_env.get("ANTHROPIC_API_KEY"):
            resolved_env.setdefault(
                "ANTHROPIC_MODEL", settings.ANTHROPIC_MODEL
            )
            resolved_env.setdefault(
                "OPENCODE_MODEL", f"anthropic/{resolved_env['ANTHROPIC_MODEL']}"
            )

        client = DaytonaClient._make_client()
        sandbox = None
        session_id = f"ar-{uuid.uuid4().hex[:8]}"

        try:
            try:
                params = CreateSandboxFromImageParams(
                    image=_resolve_image(),
                    auto_stop_interval=_AUTOSTOP_MINUTES,
                )
                sandbox = await client.create(params)
                logger.debug("Daytona sandbox created from harness image: %s", sandbox.id)
            except Exception as image_err:
                logger.warning("Harness image unavailable (%s), falling back to %s", image_err, FALLBACK_SNAPSHOT)
                from daytona import CreateSandboxFromSnapshotParams
                params = CreateSandboxFromSnapshotParams(
                    snapshot=FALLBACK_SNAPSHOT,
                    auto_stop_interval=_AUTOSTOP_MINUTES,
                )
                sandbox = await client.create(params)

            # Create a persistent shell session
            await sandbox.process.create_session(session_id)

            # Pre-install OpenCode if not baked into the image, and best-effort
            # write a minimal OpenCode config pointing at the Anthropic provider
            # + chosen model so `opencode run` uses Claude. The exported
            # ANTHROPIC_API_KEY/ANTHROPIC_MODEL (prepended to every exec) is the
            # primary mechanism; the config file is a defensive fallback.
            setup_cmd = (
                "which opencode || npm install -g opencode-ai@latest -q 2>/dev/null || true"
            )
            model = resolved_env.get("ANTHROPIC_MODEL", settings.ANTHROPIC_MODEL)
            if resolved_env.get("ANTHROPIC_API_KEY"):
                opencode_cfg = (
                    '{"model":"anthropic/' + model + '",'
                    '"provider":{"anthropic":{"models":{"' + model + '":{}}}}}'
                )
                setup_cmd += (
                    "; mkdir -p ~/.config/opencode 2>/dev/null"
                    "; printf '%s' " + shlex.quote(opencode_cfg)
                    + " > ~/.config/opencode/config.json 2>/dev/null || true"
                )
            try:
                await sandbox.process.execute_session_command(
                    session_id,
                    SessionExecuteRequest(command=setup_cmd, run_async=False),
                )
            except Exception:
                pass

            yield SandboxHandle(client, sandbox, session_id, env=resolved_env)

        finally:
            if sandbox:
                try:
                    # AsyncDaytona has no `remove`; the real teardown is `delete`.
                    await client.delete(sandbox)
                    logger.debug("Daytona sandbox deleted: %s", sandbox.id)
                except Exception as e:
                    logger.warning("Failed to delete sandbox: %s", e)

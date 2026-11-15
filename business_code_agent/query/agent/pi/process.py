"""Strict JSONL transport for Pi RPC mode.

Pi reserves stdout for protocol records and writes diagnostics to stderr. A
reader thread keeps stdout drained even while the caller waits for a command
response, and records are split only on LF as required by the protocol.
"""

from __future__ import annotations

import json
import queue
import subprocess
import threading
from collections import deque
from pathlib import Path
from typing import Any, Mapping, Sequence


_EOF = object()


class PiProcessError(RuntimeError):
    """The Pi child process could not be started, read or written."""


class PiProcess:
    def __init__(self, command: Sequence[str], *, cwd: Path,
                 environment: Mapping[str, str] | None = None,
                 stderr_lines: int = 80):
        self.command = [str(item) for item in command]
        self.cwd = Path(cwd)
        self.environment = dict(environment or {})
        self._records: queue.Queue[Any] = queue.Queue()
        self._stderr: deque[str] = deque(maxlen=stderr_lines)
        self._process: subprocess.Popen | None = None
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None

    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process else None

    def start(self) -> None:
        if self._process is not None:
            return
        try:
            self._process = subprocess.Popen(
                self.command,
                cwd=str(self.cwd),
                env=self.environment or None,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
        except OSError as exc:
            raise PiProcessError(f"无法启动 Pi 进程：{exc}") from exc
        self._stdout_thread = threading.Thread(
            target=self._read_stdout, name="pi-rpc-stdout", daemon=True)
        self._stderr_thread = threading.Thread(
            target=self._read_stderr, name="pi-rpc-stderr", daemon=True)
        self._stdout_thread.start()
        self._stderr_thread.start()

    def _read_stdout(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            self._records.put(_EOF)
            return
        try:
            while True:
                line = process.stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip("\r\n")
                if not text.strip():
                    continue
                try:
                    record = json.loads(text)
                except json.JSONDecodeError:
                    self._stderr.append("Pi stdout 含非 JSON 记录：" + text[:200])
                    continue
                if isinstance(record, dict):
                    self._records.put(record)
        except Exception as exc:  # pragma: no cover - defensive transport path
            self._stderr.append(f"读取 Pi stdout 失败：{exc}")
        finally:
            self._records.put(_EOF)

    def _read_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        try:
            while True:
                line = process.stderr.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip("\r\n")
                if text:
                    self._stderr.append(text)
        except Exception as exc:  # pragma: no cover - defensive transport path
            self._stderr.append(f"读取 Pi stderr 失败：{exc}")

    def send(self, record: Mapping[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise PiProcessError("Pi 进程尚未启动")
        payload = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        try:
            process.stdin.write(payload.encode("utf-8"))
            process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise PiProcessError(f"写入 Pi 命令失败：{exc}；{self.diagnostics()}") from exc

    def receive(self, timeout: float) -> dict[str, Any] | None:
        try:
            item = self._records.get(timeout=max(0.0, timeout))
        except queue.Empty:
            return None
        if item is _EOF:
            # Keep the terminal marker available so later reads fail promptly.
            self._records.put(_EOF)
            raise PiProcessError(
                f"Pi 进程已退出（exit={self.exit_code}）：{self.diagnostics()}")
        return item

    def close(self, *, wait: float = 5.0) -> None:
        process = self._process
        if process is None:
            return
        try:
            if process.stdin is not None and not process.stdin.closed:
                process.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        try:
            process.wait(timeout=wait)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        for thread in (self._stdout_thread, self._stderr_thread):
            if thread is not None and thread.is_alive():
                thread.join(timeout=1)

    @property
    def exit_code(self) -> int | None:
        return self._process.poll() if self._process else None

    def diagnostics(self, limit: int = 12) -> str:
        lines = list(self._stderr)[-limit:]
        return " / ".join(lines) if lines else "无 stderr 输出"

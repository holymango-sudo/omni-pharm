"""Offline tests for the resumable downloader (no network)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from omnipharm.data import download as dl

PAYLOAD = bytes(range(256)) * 4096  # 1 MiB


def _server(fail_first: int = 0, honor_range: bool = True) -> tuple[httpx.Client, dict[str, int]]:
    state = {"calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["calls"] += 1
        if state["calls"] <= fail_first:
            return httpx.Response(503)
        rng = request.headers.get("Range")
        if rng and honor_range:
            start = int(rng.removeprefix("bytes=").rstrip("-"))
            if start >= len(PAYLOAD):
                return httpx.Response(416)
            body = PAYLOAD[start:]
            return httpx.Response(206, content=body, headers={"Content-Length": str(len(body))})
        return httpx.Response(200, content=PAYLOAD, headers={"Content-Length": str(len(PAYLOAD))})

    return httpx.Client(transport=httpx.MockTransport(handler)), state


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dl.time, "sleep", lambda _s: None)


def test_full_download(tmp_path: Path) -> None:
    client, _ = _server()
    out = dl.download_file("http://x/f.bin", tmp_path / "f.bin", client)
    assert out.read_bytes() == PAYLOAD
    assert not (tmp_path / "f.bin.part").exists()


def test_resume_from_partial(tmp_path: Path) -> None:
    (tmp_path / "f.bin.part").write_bytes(PAYLOAD[:300_000])
    client, _ = _server()
    out = dl.download_file("http://x/f.bin", tmp_path / "f.bin", client)
    assert out.read_bytes() == PAYLOAD


def test_server_ignoring_range_restarts_cleanly(tmp_path: Path) -> None:
    (tmp_path / "f.bin.part").write_bytes(b"garbage" * 1000)
    client, _ = _server(honor_range=False)
    out = dl.download_file("http://x/f.bin", tmp_path / "f.bin", client)
    assert out.read_bytes() == PAYLOAD


def test_retries_transient_errors(tmp_path: Path) -> None:
    client, state = _server(fail_first=3)
    out = dl.download_file("http://x/f.bin", tmp_path / "f.bin", client)
    assert out.read_bytes() == PAYLOAD
    assert state["calls"] == 4


def test_gives_up_and_leaves_no_final_file(tmp_path: Path) -> None:
    client, _ = _server(fail_first=999)
    with pytest.raises(RuntimeError):
        dl.download_file("http://x/f.bin", tmp_path / "f.bin", client)
    assert not (tmp_path / "f.bin").exists()  # never a truncated file under the final name


def test_sha256(tmp_path: Path) -> None:
    import hashlib

    p = tmp_path / "a"
    p.write_bytes(PAYLOAD)
    assert dl.sha256_file(p) == hashlib.sha256(PAYLOAD).hexdigest()

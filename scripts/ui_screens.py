"""Capture the webapp's UI states as PNGs, over the Chrome DevTools Protocol.

Session F2 deliverable tooling. Drives a real Chrome (headless, CDP) against a
locally running app, at desktop width and at 390px mobile, and writes one PNG per
state into an output directory.

It captures three kinds of state:

  * static pages          -- /, /privacy, /validation
  * scripted UI states    -- the ?preview= state cards app.js already renders for
                             operator review, plus the More-options disclosure and
                             the saved-machines UI (seeded through localStorage)
  * the LIVE demo flow    -- clicks "Run the example analysis" on the real form
                             and screenshots the job as it actually progresses

The demo flow needs the server running WITHOUT an ANTHROPIC_API_KEY, which is how
this script starts it: the drafting client cannot be built, the job degrades to
the deterministic report (the documented contract), and no API call is made or
paid for. That degraded READY card is the real one, not a mock.

Usage:
    python scripts/ui_screens.py [--out outputs/ui_v2_screens] [--port 8011]

NOTE: this is a headless capture for review artifacts. It does NOT satisfy the
standing rule that每 release gets a human session in a real browser -- see
CLAUDE.md. Screenshots are evidence of layout, not of a browser session.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import websockets

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
DESKTOP = (1280, 900)
MOBILE = (390, 844)
INVITE_CODE = "demo1"

# ?preview= states app.js renders for review (see the preview() block there).
PREVIEW_STATES = [
    "queued", "analyzing", "drafting", "confirm", "confirm-multi", "confirm-cached",
    "ready", "ready-healthy",
    "ready-multiaxis", "speed-mismatch", "partial-gate", "degraded", "stopped", "error",
    # hotfix-net: the network/limit cards, which an analyst on a plant Wi-Fi
    # will see more often than any of the analysis states.
    "lost-contact", "server-busy", "offline", "oversize", "invite",
]

SEED_MACHINES = json.dumps([
    {"machine_alias": "Boiler feed pump 2A", "rpm": "3560", "iso_group": "2",
     "iso_support": "rigid", "bearing_model": "6206", "velocity_unit": "mm_s",
     "detection_type": "rms", "mode": "spectrum", "direction": ""},
    {"machine_alias": "Cooling tower fan 4", "rpm": "890", "iso_group": "1",
     "iso_support": "flexible", "bearing_model": "6309", "velocity_unit": "mm_s",
     "detection_type": "rms", "mode": "spectrum", "direction": "radial_v"},
])


class Chrome:
    """The thinnest CDP client that does the job: one page target, one socket."""

    def __init__(self, port: int) -> None:
        self.port = port
        self.proc: subprocess.Popen | None = None
        self.ws: websockets.ClientConnection | None = None
        self._id = 0

    def start(self, profile_dir: Path) -> None:
        self.proc = subprocess.Popen(
            [CHROME, "--headless=new", f"--remote-debugging-port={self.port}",
             f"--user-data-dir={profile_dir}", "--no-first-run", "--no-default-browser-check",
             "--hide-scrollbars", "--force-device-scale-factor=2", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        _wait_http(f"http://127.0.0.1:{self.port}/json/version", 20)

    async def connect(self) -> None:
        targets = json.loads(_http(f"http://127.0.0.1:{self.port}/json/list"))
        page = next(t for t in targets if t["type"] == "page")
        self.ws = await websockets.connect(page["webSocketDebuggerUrl"], max_size=200 * 1024 * 1024)
        await self.send("Page.enable")
        await self.send("Runtime.enable")

    async def send(self, method: str, **params):
        self._id += 1
        msg_id = self._id
        await self.ws.send(json.dumps({"id": msg_id, "method": method, "params": params}))
        while True:
            raw = json.loads(await self.ws.recv())
            if raw.get("id") == msg_id:
                if "error" in raw:
                    raise RuntimeError(f"{method}: {raw['error']}")
                return raw.get("result", {})

    async def viewport(self, width: int, height: int) -> None:
        await self.send("Emulation.setDeviceMetricsOverride", width=width, height=height,
                        deviceScaleFactor=2, mobile=width <= 480)

    async def goto(self, url: str) -> None:
        await self.send("Page.navigate", url=url)
        await asyncio.sleep(1.2)  # load + webfonts; the pages are static and tiny

    async def js(self, expression: str):
        result = await self.send("Runtime.evaluate", expression=expression, awaitPromise=True,
                                 returnByValue=True)
        return result.get("result", {}).get("value")

    async def tab(self, times: int = 1) -> None:
        """A REAL Tab keypress, not element.focus(): :focus-visible only matches
        keyboard focus, so this is the only way to screenshot the focus ring."""
        for _ in range(times):
            for event in ("rawKeyDown", "keyUp"):
                await self.send("Input.dispatchKeyEvent", type=event, key="Tab", code="Tab",
                                windowsVirtualKeyCode=9, nativeVirtualKeyCode=9)
            await asyncio.sleep(0.05)

    async def shot(self, path: Path) -> None:
        data = await self.send("Page.captureScreenshot", format="png", captureBeyondViewport=True)
        path.write_bytes(base64.b64decode(data["data"]))
        print(f"  → {path.name}")

    def stop(self) -> None:
        if self.proc is not None:
            self.proc.send_signal(signal.SIGTERM)
            self.proc.wait(timeout=10)


def _http(url: str) -> str:
    with urllib.request.urlopen(url, timeout=5) as response:
        return response.read().decode()


def _wait_http(url: str, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            _http(url)
            return
        except Exception:  # noqa: BLE001 -- polling a socket that is still coming up
            time.sleep(0.2)
    raise RuntimeError(f"timed out waiting for {url}")


def start_server(port: int, log_path: Path) -> subprocess.Popen:
    """uvicorn with NO ANTHROPIC_API_KEY: the demo job degrades to the
    deterministic report instead of calling (and billing) the API."""
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    env["INVITE_CODES"] = f"{INVITE_CODE}:screenshots"
    env["CONTACT_EMAIL"] = "reports@example.com"
    env["IP_REQUESTS_PER_MINUTE"] = "10000"
    env["IP_JOB_POSTS_PER_HOUR"] = "1000"
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "vib_agent.webapp.app:app", "--port", str(port),
         "--host", "127.0.0.1", "--no-access-log"],
        env=env, stdout=log_path.open("w"), stderr=subprocess.STDOUT,
    )
    _wait_http(f"http://127.0.0.1:{port}/healthz", 30)
    return proc


async def capture(base: str, out: Path, chrome: Chrome) -> None:
    for label, (width, _height) in (("desktop", DESKTOP), ("mobile", MOBILE)):
        print(f"[{label}]")
        await chrome.viewport(*(DESKTOP if label == "desktop" else MOBILE))

        # ── static pages ──────────────────────────────────────────────
        await chrome.goto(f"{base}/?code={INVITE_CODE}")
        await chrome.shot(out / f"form_{label}.png")

        await chrome.js("document.getElementById('more-options').open = true;")
        await asyncio.sleep(0.3)
        await chrome.shot(out / f"form_more_options_{label}.png")

        await chrome.goto(f"{base}/privacy")
        await chrome.shot(out / f"privacy_{label}.png")
        await chrome.goto(f"{base}/validation")
        await chrome.shot(out / f"validation_{label}.png")

        # ── saved machines (browser-only memory) ──────────────────────
        await chrome.goto(f"{base}/")
        await chrome.js(f"localStorage.setItem('vib.machines.v1', {SEED_MACHINES!r});")
        await chrome.goto(f"{base}/?code={INVITE_CODE}")
        await chrome.js("document.getElementById('saved').value = '0';"
                        "document.getElementById('saved').dispatchEvent(new Event('change'));")
        await asyncio.sleep(0.3)
        await chrome.shot(out / f"saved_machines_{label}.png")
        await chrome.js("localStorage.removeItem('vib.machines.v1');")

        # ── keyboard focus ring (accessibility evidence) ──────────────
        await chrome.goto(f"{base}/")
        await chrome.tab(4)  # into the hero's two buttons and onward
        await chrome.shot(out / f"focus_hero_{label}.png")

        # ── preview state cards ───────────────────────────────────────
        for state in PREVIEW_STATES:
            await chrome.goto(f"{base}/?preview={state}")
            await chrome.shot(out / f"state_{state.replace('-', '_')}_{label}.png")

        # ── the LIVE demo flow ────────────────────────────────────────
        await chrome.goto(f"{base}/?code={INVITE_CODE}")
        await chrome.js("document.getElementById('run-example').click();")
        await asyncio.sleep(1.0)
        await chrome.shot(out / f"demo_running_{label}.png")
        for _ in range(60):
            state = await chrome.js(
                "(document.querySelector('#state .state-tag')||{}).textContent||''")
            if state and "READY" in state:
                break
            await asyncio.sleep(1.0)
        await asyncio.sleep(0.5)
        await chrome.shot(out / f"demo_ready_{label}.png")
        print(f"  demo finished as: {state!r}")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="outputs/ui_v2_screens")
    parser.add_argument("--port", type=int, default=8011)
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    scratch = Path(os.environ.get("TMPDIR", "/tmp")) / "vib_ui_screens"
    shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir(parents=True)

    server = start_server(args.port, scratch / "server.log")
    chrome = Chrome(port=args.port + 100)
    try:
        chrome.start(scratch / "chrome-profile")
        await chrome.connect()
        await capture(f"http://127.0.0.1:{args.port}", out, chrome)
    finally:
        chrome.stop()
        server.send_signal(signal.SIGTERM)
        server.wait(timeout=10)
    print(f"\nwrote {len(list(out.glob('*.png')))} screenshots to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

"""Install the reviewed optional browser driver; never download a browser."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import urllib.request

BASE_DIR = Path(__file__).resolve().parents[2]
PIN = "6c00e8cf1bf2fb6718740e0c20d3578fb4c47286"
PLAYWRIGHT = "1.63.0"
HASHES = {
    "muse-driver.mjs": "ae83d2ebfa89409b13d02f23bd786a30445d55e177c71447fcffc33ef68fdd9e",
    "LICENSE": "ae4ffa9dd45509ef421ddb29757b982479aa6da8a864d598ffb708d39ec2256d",
}


def safe_driver(source: str) -> str:
    """Remove upstream CDP fallback and stealth launch flags, keeping normal Chrome."""
    start = source.index("    const launchPersistent = () =>")
    end = source.index("    this.ctx.on('close'", start)
    source = source[:start] + """    // Local patch: own a dedicated Chrome only; never attach to another browser.
    this.ctx = await chromium.launchPersistentContext(PROFILE_DIR, {
      channel: CHANNEL,
      headless: HEADLESS,
      viewport: { width: 1366, height: 900 },
      acceptDownloads: false,
      args: ['--no-first-run', '--no-default-browser-check'],
    })
""" + source[end:]
    if any(marker in source for marker in ("connectOverCDP", "AutomationControlled", "ignoreDefaultArgs")):
        raise RuntimeError("Unexpected upstream browser code; installation stopped.")
    return "// Upstream MIT driver at " + PIN + "; local launch safety patch below.\n" + source


def install(base_dir=BASE_DIR):
    node, npm = shutil.which("node"), shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if not node or not npm:
        raise RuntimeError("Node.js 20+ and npm are required for the optional Muse bridge.")
    version = subprocess.check_output([node, "--version"], text=True).strip()
    if int(version.lstrip("v").split(".")[0]) < 20:
        raise RuntimeError("The reviewed Playwright version requires Node.js 20 or newer.")
    runtime = Path(base_dir) / "workspace/tools/muse-chat-mcp"
    runtime.mkdir(parents=True, exist_ok=True)
    marker = runtime / "reviewed-version.json"
    wanted = {"upstream": PIN, "playwright": PLAYWRIGHT, "patch": 1}
    current = {}
    if marker.is_file():
        try:
            current = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    if current != wanted or not (runtime / "muse-driver.mjs").is_file() or not (runtime / "LICENSE").is_file():
        for name, expected in HASHES.items():
            url = f"https://raw.githubusercontent.com/duclm1x1/Muse-Chat-MCP/{PIN}/{name}"
            with urllib.request.urlopen(url, timeout=30) as response:
                data = response.read(100000)
            if hashlib.sha256(data).hexdigest() != expected:
                raise RuntimeError(f"Integrity check failed for {name}; installation stopped.")
            text = data.decode("utf-8")
            if name.endswith(".mjs"):
                text = safe_driver(text)
            (runtime / name).write_text(text, encoding="utf-8", newline="\n")
    package = runtime / "node_modules/playwright-core/package.json"
    installed = False
    if package.is_file():
        try:
            installed = json.loads(package.read_text(encoding="utf-8")).get("version") == PLAYWRIGHT
        except (OSError, ValueError):
            pass
    if not installed:
        print("Installing playwright-core: approximately 13 MB unpacked; no browser download.", flush=True)
        (runtime / "package.json").write_text(json.dumps({
            "name": "videotranslate-muse-runtime", "private": True, "type": "module",
            "dependencies": {"playwright-core": PLAYWRIGHT},
        }, indent=2) + "\n", encoding="utf-8")
        cache = runtime / ".npm-cache"
        cache_existed = cache.exists()
        env = os.environ.copy()
        env.update(PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD="1", npm_config_cache=str(cache))
        try:
            subprocess.run([npm, "install", "--ignore-scripts", "--no-audit", "--no-fund", "--loglevel=error"],
                           cwd=str(runtime), env=env, check=True)
        finally:
            if not cache_existed and cache.exists() and cache.resolve().is_relative_to(runtime.resolve()):
                shutil.rmtree(cache)
    marker.write_text(json.dumps(wanted, indent=2) + "\n", encoding="utf-8")
    print("Muse bridge ready. Open the app's Muse login button and sign in with your own account.")
    return runtime


if __name__ == "__main__":
    try:
        install()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"Muse setup failed: {exc}")
        raise SystemExit(1)

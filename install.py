#!/usr/bin/env python3
"""
Marstek Dashboard – one-click installer
Works on Windows, Linux and macOS without needing PowerShell or admin rights.
Run:  python install.py
"""
import os
import sys
import shutil
import subprocess
import platform
import time

ROOT   = os.path.dirname(os.path.abspath(__file__))
BACK   = os.path.join(ROOT, "backend")
FRONT  = os.path.join(ROOT, "frontend")
VENV   = os.path.join(BACK, "venv")
WIN    = platform.system() == "Windows"

# ── Colour helpers ────────────────────────────────────────────────────────────
def ok(msg):   print(f"\033[32m[OK]   {msg}\033[0m")
def info(msg): print(f"\033[33m[INFO] {msg}\033[0m")
def err(msg):  print(f"\033[31m[ERROR] {msg}\033[0m")
def hdr(msg):  print(f"\n\033[1;36m{msg}\033[0m")
def step(msg): print(f"\033[1m{msg}\033[0m")

def run(args, cwd=None, check=True, capture=False):
    """Run a command, return exit code."""
    if capture:
        result = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    else:
        result = subprocess.run(args, cwd=cwd)
    if check and result.returncode != 0:
        err(f"Command failed: {' '.join(str(a) for a in args)}")
        if capture and result.stderr:
            print(result.stderr[:500])
        input("\nPress Enter to exit…")
        sys.exit(1)
    return result

hdr("==============================================")
hdr("  Marstek Dashboard Installer")
hdr("==============================================")
print()

# ── Python version check ──────────────────────────────────────────────────────
if sys.version_info < (3, 8):
    err(f"Python 3.8+ required. You have {sys.version_info.major}.{sys.version_info.minor}")
    sys.exit(1)
ok(f"Python {sys.version.split()[0]}")

# ── Node.js check ─────────────────────────────────────────────────────────────
node_paths_win = [
    r"C:\Program Files\nodejs",
    r"C:\Program Files (x86)\nodejs",
    os.path.join(os.environ.get("APPDATA", ""), "nvm", "current"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "nodejs"),
]

def find_node():
    for exe in ("node", "node.exe"):
        found = shutil.which(exe)
        if found:
            npm = shutil.which("npm") or shutil.which("npm.cmd")
            if npm:
                return found, npm
    if WIN:
        for folder in node_paths_win:
            node_exe = os.path.join(folder, "node.exe")
            npm_cmd  = os.path.join(folder, "npm.cmd")
            if os.path.isfile(node_exe) and os.path.isfile(npm_cmd):
                os.environ["PATH"] = folder + os.pathsep + os.environ.get("PATH", "")
                return node_exe, npm_cmd
    return None, None

node_exe, npm_cmd = find_node()

if not node_exe:
    info("Node.js not found. Attempting install via winget…")
    if WIN and shutil.which("winget"):
        run(
            ["winget", "install", "OpenJS.NodeJS.LTS",
             "--silent", "--accept-package-agreements", "--accept-source-agreements"],
            check=False
        )
        for folder in node_paths_win:
            if os.path.isfile(os.path.join(folder, "node.exe")):
                os.environ["PATH"] = folder + os.pathsep + os.environ.get("PATH", "")
                break
        node_exe, npm_cmd = find_node()

    if not node_exe:
        err("Node.js not found and could not be installed automatically.")
        print("  Please install Node.js 18+ from: https://nodejs.org/")
        print("  Then re-run:  python install.py")
        input("\nPress Enter to exit…")
        sys.exit(1)
    ok("Node.js installed.")

node_ver = subprocess.check_output([node_exe, "--version"]).decode().strip()
npm_ver  = subprocess.check_output([npm_cmd,  "--version"]).decode().strip()
ok(f"Node.js {node_ver}  |  npm {npm_ver}")
print()

# ── Docker / InfluxDB check ───────────────────────────────────────────────────
step("[1/5] Checking Docker & InfluxDB…")

docker_exe  = shutil.which("docker") or shutil.which("docker.exe")
compose_cmd = None

if docker_exe:
    # Prefer `docker compose` (v2 plugin), fall back to `docker-compose` (v1)
    r = subprocess.run([docker_exe, "compose", "version"],
                       capture_output=True, text=True)
    if r.returncode == 0:
        compose_cmd = [docker_exe, "compose"]
    else:
        dc = shutil.which("docker-compose") or shutil.which("docker-compose.exe")
        if dc:
            compose_cmd = [dc]

if not docker_exe or not compose_cmd:
    info("Docker not found – InfluxDB will be skipped.")
    info("Install Docker Desktop from https://www.docker.com/products/docker-desktop/")
    info("then re-run install.py to enable time-series storage and strategy features.")
    influx_ok = False
else:
    # Check if InfluxDB container is already running
    r = subprocess.run(
        [docker_exe, "ps", "--filter", "name=marstek-influxdb",
         "--filter", "status=running", "--format", "{{.Names}}"],
        capture_output=True, text=True
    )
    if "marstek-influxdb" in r.stdout:
        ok("InfluxDB already running.")
        influx_ok = True
    else:
        info("Starting InfluxDB via Docker Compose…")
        r = run(compose_cmd + ["-f", os.path.join(ROOT, "docker-compose.yml"),
                               "up", "-d", "--pull", "missing"],
                cwd=ROOT, check=False)
        if r.returncode != 0:
            info("Docker Compose failed – continuing without InfluxDB.")
            influx_ok = False
        else:
            # Wait up to 20 s for InfluxDB to become healthy
            import urllib.request
            for attempt in range(20):
                try:
                    urllib.request.urlopen("http://localhost:8086/ping", timeout=2)
                    ok("InfluxDB started and healthy.")
                    influx_ok = True
                    break
                except Exception:
                    time.sleep(1)
            else:
                info("InfluxDB started but health check timed out. It may still be initialising.")
                influx_ok = True  # container is up, just slow

print()

# ── Step 2 – Python virtual environment ──────────────────────────────────────
step("[2/5] Creating Python virtual environment…")
if os.path.isdir(VENV):
    print("      Already exists, skipping.")
else:
    run([sys.executable, "-m", "venv", VENV])

# ── Step 3 – pip install ──────────────────────────────────────────────────────
step("[3/5] Installing Python dependencies…")
pip = os.path.join(VENV, "Scripts" if WIN else "bin", "pip")
run([pip, "install", "-r", os.path.join(BACK, "requirements.txt"),
     "-q", "--disable-pip-version-check"])
ok("Python dependencies installed.")

# ── Step 4 – npm install ──────────────────────────────────────────────────────
step("[4/5] Installing frontend dependencies…")
run([npm_cmd, "install", "--no-fund", "--no-audit"], cwd=FRONT)
ok("Frontend dependencies installed.")

# ── Step 5 – npm build ────────────────────────────────────────────────────────
step("[5/5] Building frontend…")
run([npm_cmd, "run", "build"], cwd=FRONT)
ok("Frontend built.")

# ── Summary ───────────────────────────────────────────────────────────────────
hdr("==============================================")
hdr("  Installation complete!")
print()
print("  Run start.bat  (Windows)  to launch the dashboard.")
print("  Run ./start.sh (Linux)    to launch the dashboard.")
print("  Dashboard opens at:  http://localhost:5000")
if influx_ok:
    print("  InfluxDB UI:         http://localhost:8086")
    print("    org:    marstek  |  bucket: energy")
    print("    token:  marstek-influx-token-local")
else:
    print()
    print("  ⚠  InfluxDB not running – install Docker Desktop and re-run install.py")
    print("     to enable time-series storage and the charging strategy feature.")
hdr("==============================================")
print()

launch = input("Launch the dashboard now? (y/n): ").strip().lower()
if launch == "y":
    if WIN:
        os.startfile(os.path.join(ROOT, "start.bat"))
    else:
        subprocess.Popen(["bash", os.path.join(ROOT, "start.sh")])
else:
    input("Press Enter to exit…")

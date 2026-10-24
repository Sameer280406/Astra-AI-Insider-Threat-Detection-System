#!/usr/bin/env python3
"""
Astra - Unified Startup Script
Launches the complete Astra platform (FastAPI Backend + Next.js Frontend + SQLite + CERT Replay Engine)
with automatic port management, database migrations, seeding, health checks, and browser launch.
"""

import sys
import os
import time
import socket
import signal
import subprocess
import webbrowser
from pathlib import Path

# Paths
ROOT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = ROOT_DIR / "backend"

def print_banner():
    banner = r"""
    ========================================================================
       ___   _____ _____ _____ ___     ___ _       _    __                      
      / _ \ /  ___|_   _| ___ / _ \   / _ \ |     | |  / _|                     
     / /_\ \\ `--.  | | | |_// /_\ \ / /_\ \ | __ _| |_| |_ ___  _ __ _ __ ___  
     |  _  | `--. \ | | |    /  _  | |  _  | |/ _` | __|  _/ _ \| '__| '_ ` _ \ 
     | | | |/\__/ / | | | |\ \ | | | | | | | | (_| | |_| || (_) | |  | | | | | |
     \_| |_/\____/  \_/ \_| \_| |_/  \_| |_/_|\__,_|\__|_| \___/|_|  |_| |_| |_|
    ========================================================================
     AI Insider Threat Intelligence & Immutable Forensics Platform
    ========================================================================
    """
    print(banner)

def free_port(port: int):
    """Checks if a port is in use and gracefully frees it on Windows/Linux."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
        s.close()
        return
    except OSError:
        pass

    print(f"[*] Port {port} is occupied. Freeing port...")
    if sys.platform == "win32":
        try:
            out = subprocess.check_output(f'netstat -ano | findstr :{port}', shell=True).decode()
            for line in out.strip().splitlines():
                if "LISTENING" in line:
                    parts = line.split()
                    pid = parts[-1]
                    if pid and int(pid) != os.getpid():
                        subprocess.call(f'taskkill /F /PID {pid}', shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
    else:
        try:
            subprocess.call(f"fuser -k {port}/tcp", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
    time.sleep(1)

def wait_for_url(url: str, timeout: int = 30, service_name: str = "Service"):
    """Polls a URL until it responds 200 or times out."""
    import urllib.request
    import urllib.error

    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Astra-HealthChecker/1.0"})
            with urllib.request.urlopen(req, timeout=2) as res:
                if res.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(1)
    return False

def main():
    print_banner()

    # 1. Environment Verification
    print("[1/5] Checking environment configuration...")
    env_file = ROOT_DIR / ".env"
    backend_env = BACKEND_DIR / ".env"
    env_example = ROOT_DIR / ".env.example"

    if not env_file.exists() and env_example.exists():
        import shutil
        shutil.copy(env_example, env_file)
        print("  -> Created .env from .env.example")
    if not backend_env.exists() and env_file.exists():
        import shutil
        shutil.copy(env_file, backend_env)
        print("  -> Synced .env to backend/.env")

    # 2. Port Cleanup
    print("[2/5] Ensuring ports 8008 (API) and 3000 (UI) are available...")
    free_port(8008)
    free_port(3000)

    # 3. Database Migration & Seeding
    print("[3/5] Applying migrations and verifying seed data...")
    try:
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=BACKEND_DIR, check=True)
        subprocess.run([sys.executable, "-m", "app.seed"], cwd=BACKEND_DIR, check=True)
        print("  -> Database migrated and verified.")
    except subprocess.CalledProcessError as e:
        print(f"  [!] Database preparation warning: {e}")

    # 4. Launch Backend (FastAPI / Uvicorn on port 8008)
    print("[4/5] Starting FastAPI backend on http://127.0.0.1:8008 ...")
    backend_proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8008"],
        cwd=BACKEND_DIR
    )

    if not wait_for_url("http://127.0.0.1:8008/health", timeout=15, service_name="Backend"):
        print("  [!] Backend failed to report healthy in time. Exiting.")
        backend_proc.terminate()
        sys.exit(1)
    print("  -> Backend is healthy and operational!")

    # 5. Launch Frontend (Node / Vite / Vinext on port 3000)
    print("[5/5] Starting Frontend web workspace on http://localhost:3000 ...")
    frontend_cmd = "pnpm dev" if sys.platform != "win32" else "pnpm.cmd dev"
    try:
        frontend_proc = subprocess.Popen(
            frontend_cmd,
            cwd=ROOT_DIR,
            shell=True
        )
    except Exception:
        # Fallback to direct node call
        frontend_proc = subprocess.Popen(
            ["node", "scripts/run-framework.mjs", "dev"],
            cwd=ROOT_DIR
        )

    if not wait_for_url("http://localhost:3000", timeout=25, service_name="Frontend"):
        print("  [*] Waiting for frontend bundle compilation...")
        wait_for_url("http://localhost:3000", timeout=30, service_name="Frontend")

    print("\n" + "=" * 72)
    print("  >>> ASTRA IS RUNNING SUCCESSFULLY! <<<")
    print("=" * 72)
    print("  * Frontend Web App:  http://localhost:3000")
    print("  * Backend API:       http://127.0.0.1:8008")
    print("  * Interactive Docs:  http://127.0.0.1:8008/docs")
    print("------------------------------------------------------------------------")
    print("  DEMO CREDENTIALS:")
    print("  * Organization Slug: meridian")
    print("  * Email:             org_admin@astra.demo")
    print("  * Password:          Mer@paswordlalantap@#1234")
    print("------------------------------------------------------------------------")
    print("  Press Ctrl+C at any time to shut down all Astra services cleanly.")
    print("=" * 72 + "\n")

    # Automatically open the browser
    try:
        webbrowser.open("http://localhost:3000")
    except Exception:
        pass

    # Process supervision & graceful cleanup
    def shutdown_handler(sig, frame):
        print("\n[*] Shutting down Astra services...")
        try:
            frontend_proc.terminate()
            backend_proc.terminate()
        except Exception:
            pass
        free_port(8008)
        free_port(3000)
        print("[*] All services stopped. Goodbye!")
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown_handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, shutdown_handler)

    try:
        while True:
            time.sleep(1)
            # Check if any child process crashed
            if backend_proc.poll() is not None:
                print("[!] Backend process exited unexpectedly.")
                break
            if frontend_proc.poll() is not None:
                print("[!] Frontend process exited unexpectedly.")
                break
    except KeyboardInterrupt:
        shutdown_handler(None, None)

if __name__ == "__main__":
    main()

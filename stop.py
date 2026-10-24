#!/usr/bin/env python3
"""
Astra - Unified Stop Script
Cleanly stops all Astra platform processes (FastAPI backend on port 8008 and Next.js frontend on port 3000).
"""

import sys
import os
import time
import socket
import subprocess

def free_port(port: int):
    """Checks if a port is in use and terminates the listening process."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
        s.close()
        print(f"  [+] Port {port} is already free.")
        return
    except OSError:
        pass

    print(f"  [*] Port {port} is occupied. Terminating process...")
    killed = False
    if sys.platform == "win32":
        try:
            out = subprocess.check_output(f'netstat -ano | findstr :{port}', shell=True).decode()
            for line in out.strip().splitlines():
                if "LISTENING" in line:
                    parts = line.split()
                    pid = parts[-1]
                    if pid and int(pid) != os.getpid():
                        print(f"      -> Killing process PID {pid} on port {port}...")
                        subprocess.call(f'taskkill /F /PID {pid}', shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        killed = True
        except Exception as e:
            print(f"      [!] Error checking/killing port {port}: {e}")
    else:
        try:
            subprocess.call(f"fuser -k {port}/tcp", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            killed = True
        except Exception:
            pass

    time.sleep(1)
    # Verify port is released
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
        s.close()
        print(f"  [+] Port {port} successfully freed.")
    except OSError:
        print(f"  [!] Port {port} could not be completely freed.")

def main():
    print("=" * 60)
    print("   ASTRA PLATFORM - STOPPING ALL SERVICES")
    print("=" * 60)
    print("[*] Terminating Backend (Port 8008)...")
    free_port(8008)
    print("\n[*] Terminating Frontend (Port 3000)...")
    free_port(3000)
    print("\n" + "=" * 60)
    print("  [SUCCESS] All Astra platform services have been stopped.")
    print("=" * 60)

if __name__ == "__main__":
    main()

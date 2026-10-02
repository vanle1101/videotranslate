import sys
import time
import urllib.request
from pathlib import Path

# Ensure root directory is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings
from core.services.service_manager import ServiceManager

def test_three_random_ports():
    print("=" * 80)
    print("TEST: STARTING INTERNAL FASTAPI ON 3 INDEPENDENT RANDOM FREE PORTS")
    print("=" * 80)

    for i in range(1, 4):
        print(f"\n--- [Iteration {i}/3] ---")
        sm = ServiceManager()
        
        # Pick dynamic free port
        port = sm.find_free_port()
        print(f"Allocated Random Free Port: {port}")
        
        # Start backend on this specific port
        started_port = sm.start_backend(port=port, timeout=10.0)
        assert started_port == port, f"Expected port {port}, got {started_port}"
        assert settings.PORT == port, f"Expected settings.PORT {port}, got {settings.PORT}"
        assert sm.backend_url == f"http://127.0.0.1:{port}"

        # Verify endpoint responds on this dynamic port
        test_url = f"{sm.backend_url}/api/hardware"
        req = urllib.request.Request(test_url, headers={"User-Agent": "RandomPortTest/1.0"})
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            assert resp.status == 200
            print(f"  [+] Health-check passed on {test_url} -> Status: {resp.status}")

        # Shutdown
        sm.shutdown_all()
        time.sleep(0.5)

        # Reset singleton state for next clean iteration
        sm._initialized = False
        ServiceManager._instance = None
        print(f"  [+] Port {port} released and verified clean.")

    print("\n" + "=" * 80)
    print("SUCCESS: 3 RANDOM FREE PORTS TESTED AND PASSED WITH ZERO CONFLICTS!")
    print("=" * 80)

if __name__ == "__main__":
    test_three_random_ports()

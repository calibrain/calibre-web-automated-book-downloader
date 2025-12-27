#!/usr/bin/env python3
"""
Test script for download client implementations.

Usage:
    1. Start the test stack:
       docker compose -f docker-compose.test-clients.yml up -d

    2. Wait for containers to initialize (first run takes ~30s)

    3. Run this script to verify clients are accessible:
       python scripts/test_clients.py

    4. Access cwabd at http://localhost:8084
       - Go to Settings > Prowlarr > Download Clients
       - Select a client from the dropdown
       - Click "Test Connection" to verify

Web UIs:
    - cwabd:        http://localhost:8084
    - qBittorrent:  http://localhost:8080
    - Transmission: http://localhost:9091
    - Deluge:       http://localhost:8112
    - NZBGet:       http://localhost:6789
    - SABnzbd:      http://localhost:8085

Prerequisites (for running this script locally):
    pip install requests transmission-rpc deluge-client qbittorrent-api

First-Time Setup:
    qBittorrent:
        - Check container logs for temporary password: docker logs test-qbittorrent
        - Login at http://localhost:8080, change password to something known
        - Default username is 'admin'

    Transmission:
        - No setup needed, credentials pre-configured (admin/admin)

    Deluge:
        1. Access Web UI at http://localhost:8112 (default password: deluge)
        2. Add auth line to .local/test-clients/deluge/config/auth:
           echo "admin:admin:10" >> .local/test-clients/deluge/config/auth
        3. Restart: docker restart test-deluge

    NZBGet:
        - No setup needed, credentials pre-configured (admin/admin)

    SABnzbd:
        - Complete the setup wizard at http://localhost:8085
        - API key will be auto-detected by this script
        - In cwabd, copy API key from SABnzbd Config > General
"""

import sys
import time

# Test configuration - matches docker-compose.test-clients.yml
CONFIG = {
    # Usenet clients
    "nzbget": {
        "url": "http://localhost:6789",
        "username": "admin",
        "password": "admin",
    },
    "sabnzbd": {
        "url": "http://localhost:8085",
        "api_key": None,  # Will be read from config on first run
    },
    # Torrent clients
    "qbittorrent": {
        "url": "http://localhost:8080",
        "username": "admin",
        "password": "5NCngsHXm",  # Temp password from: docker logs test-qbittorrent | grep password
    },
    "transmission": {
        "url": "http://localhost:9091",
        "username": "admin",
        "password": "admin",
    },
    "deluge": {
        "host": "localhost",
        "port": 58846,
        "username": "admin",
        "password": "admin",
    },
}

# Test magnet link (Ubuntu ISO - legal, small metadata)
TEST_MAGNET = "magnet:?xt=urn:btih:3b245504cf5f11bbdbe1201cea6a6bf45aee1bc0&dn=ubuntu-22.04.3-live-server-amd64.iso"


def test_nzbget():
    """Test NZBGet connection."""
    import requests

    print("\n" + "=" * 50)
    print("Testing NZBGet")
    print("=" * 50)

    url = CONFIG["nzbget"]["url"]
    username = CONFIG["nzbget"]["username"]
    password = CONFIG["nzbget"]["password"]

    try:
        # Test connection via JSON-RPC
        rpc_url = f"{url}/jsonrpc"
        response = requests.post(
            rpc_url,
            json={"method": "version", "params": []},
            auth=(username, password),
            timeout=10,
        )
        response.raise_for_status()
        result = response.json()
        version = result.get("result", "unknown")
        print(f"  Connected to NZBGet {version}")

        # Test status
        response = requests.post(
            rpc_url,
            json={"method": "status", "params": []},
            auth=(username, password),
            timeout=10,
        )
        status = response.json().get("result", {})
        print(f"  Server state: {'Paused' if status.get('ServerPaused') else 'Running'}")
        print(f"  Downloads in queue: {status.get('DownloadedSizeMB', 0)} MB downloaded")

        print("  SUCCESS: NZBGet is working!")
        return True

    except requests.exceptions.ConnectionError:
        print("  ERROR: Could not connect to NZBGet")
        print("  Is the container running? docker ps | grep nzbget")
        return False
    except Exception as e:
        print(f"  ERROR: {e}")
        return False


def test_sabnzbd():
    """Test SABnzbd connection."""
    import requests

    print("\n" + "=" * 50)
    print("Testing SABnzbd")
    print("=" * 50)

    url = CONFIG["sabnzbd"]["url"]
    api_key = CONFIG["sabnzbd"]["api_key"]

    # Try to get API key from config if not set
    if not api_key:
        try:
            import os
            ini_path = ".local/test-clients/sabnzbd/config/sabnzbd.ini"
            if os.path.exists(ini_path):
                with open(ini_path) as f:
                    for line in f:
                        if line.startswith("api_key"):
                            api_key = line.split("=")[1].strip()
                            print(f"  Found API key in config: {api_key[:8]}...")
                            break
        except Exception as e:
            print(f"  Could not read API key from config: {e}")

    if not api_key:
        print("  ERROR: No API key configured")
        print("  Please access http://localhost:8085 and complete initial setup")
        print("  Then copy the API key from Config > General")
        return False

    try:
        # Test connection
        response = requests.get(
            f"{url}/api",
            params={"apikey": api_key, "mode": "version", "output": "json"},
            timeout=10,
        )
        response.raise_for_status()
        result = response.json()
        version = result.get("version", "unknown")
        print(f"  Connected to SABnzbd {version}")

        # Test queue status
        response = requests.get(
            f"{url}/api",
            params={"apikey": api_key, "mode": "queue", "output": "json"},
            timeout=10,
        )
        queue = response.json().get("queue", {})
        print(f"  Queue status: {queue.get('status', 'unknown')}")
        print(f"  Items in queue: {len(queue.get('slots', []))}")

        print("  SUCCESS: SABnzbd is working!")
        return True

    except requests.exceptions.ConnectionError:
        print("  ERROR: Could not connect to SABnzbd")
        print("  Is the container running? docker ps | grep sabnzbd")
        return False
    except Exception as e:
        print(f"  ERROR: {e}")
        return False


def test_qbittorrent():
    """Test qBittorrent connection."""
    print("\n" + "=" * 50)
    print("Testing qBittorrent")
    print("=" * 50)

    try:
        import qbittorrentapi

        url = CONFIG["qbittorrent"]["url"]
        username = CONFIG["qbittorrent"]["username"]
        password = CONFIG["qbittorrent"]["password"]

        # Parse URL for host/port
        from urllib.parse import urlparse
        parsed = urlparse(url)

        client = qbittorrentapi.Client(
            host=parsed.hostname,
            port=parsed.port or 8080,
            username=username,
            password=password,
        )

        # Test connection
        client.auth_log_in()
        version = client.app.version
        print(f"  Connected to qBittorrent {version}")

        # Get torrent list
        torrents = client.torrents_info()
        print(f"  Active torrents: {len(torrents)}")

        # Test adding a torrent (then remove it)
        print("  Testing add/remove torrent...")
        result = client.torrents_add(urls=TEST_MAGNET, is_paused=True)
        if result == "Ok.":
            # Wait a moment for it to be added
            time.sleep(1)
            torrents = client.torrents_info()
            if torrents:
                test_torrent = torrents[-1]  # Most recently added
                print(f"  Added test torrent: {test_torrent.name[:50]}...")
                print(f"  Status: {test_torrent.state}")

                # Remove it
                client.torrents_delete(torrent_hashes=test_torrent.hash, delete_files=True)
                print("  Removed test torrent")
        else:
            print(f"  Add result: {result}")

        print("  SUCCESS: qBittorrent is working!")
        return True

    except ImportError:
        print("  ERROR: qbittorrent-api not installed")
        print("  Run: pip install qbittorrent-api")
        return False
    except Exception as e:
        print(f"  ERROR: {e}")
        if "Forbidden" in str(e) or "401" in str(e):
            print("\n  Authentication failed. Check password:")
            print("  1. docker logs test-qbittorrent | grep password")
            print("  2. Login to http://localhost:8080 and set a known password")
        return False


def test_transmission():
    """Test Transmission connection."""
    print("\n" + "=" * 50)
    print("Testing Transmission")
    print("=" * 50)

    try:
        from transmission_rpc import Client
        from urllib.parse import urlparse

        url = CONFIG["transmission"]["url"]
        parsed = urlparse(url)

        client = Client(
            host=parsed.hostname,
            port=parsed.port or 9091,
            username=CONFIG["transmission"]["username"],
            password=CONFIG["transmission"]["password"],
        )

        # Test connection
        session = client.get_session()
        print(f"  Connected to Transmission {session.version}")

        # Get torrent list
        torrents = client.get_torrents()
        print(f"  Active torrents: {len(torrents)}")

        # Test adding a torrent (then remove it)
        print("  Testing add/remove torrent...")
        torrent = client.add_torrent(TEST_MAGNET, paused=True)
        print(f"  Added test torrent: {torrent.name[:50]}...")

        # Get status
        status = client.get_torrent(torrent.id)
        print(f"  Status: {status.status} ({status.percent_done * 100:.1f}%)")

        # Remove it
        client.remove_torrent(torrent.id, delete_data=True)
        print("  Removed test torrent")

        print("  SUCCESS: Transmission is working!")
        return True

    except ImportError:
        print("  ERROR: transmission-rpc not installed")
        print("  Run: pip install transmission-rpc")
        return False
    except Exception as e:
        print(f"  ERROR: {e}")
        return False


def test_deluge():
    """Test Deluge connection."""
    print("\n" + "=" * 50)
    print("Testing Deluge")
    print("=" * 50)

    try:
        from deluge_client import DelugeRPCClient

        client = DelugeRPCClient(
            host=CONFIG["deluge"]["host"],
            port=CONFIG["deluge"]["port"],
            username=CONFIG["deluge"]["username"],
            password=CONFIG["deluge"]["password"],
        )

        # Test connection
        client.connect()
        version = client.call("daemon.info")
        print(f"  Connected to Deluge {version}")

        # Get torrent list
        torrents = client.call("core.get_torrents_status", {}, ["name"])
        print(f"  Active torrents: {len(torrents)}")

        # Test adding a torrent (then remove it)
        print("  Testing add/remove torrent...")
        torrent_id = client.call("core.add_torrent_magnet", TEST_MAGNET, {"add_paused": True})

        if torrent_id:
            print(f"  Added test torrent: {torrent_id[:20]}...")

            # Get status
            status = client.call("core.get_torrent_status", torrent_id, ["state", "progress"])
            state = status.get(b"state", b"unknown")
            if isinstance(state, bytes):
                state = state.decode()
            print(f"  Status: {state}")

            # Remove it
            client.call("core.remove_torrent", torrent_id, True)
            print("  Removed test torrent")
        else:
            print("  WARNING: Could not add test torrent")

        print("  SUCCESS: Deluge is working!")
        return True

    except ImportError:
        print("  ERROR: deluge-client not installed")
        print("  Run: pip install deluge-client")
        return False
    except Exception as e:
        print(f"  ERROR: {e}")
        if "Connection refused" in str(e):
            print("  Is the container running? docker ps | grep deluge")
        elif "Bad login" in str(e) or "auth" in str(e).lower():
            print("\n  Deluge auth setup required:")
            print("  1. Add 'admin:admin:10' to .local/test-clients/deluge/config/auth")
            print("  2. Restart: docker restart test-deluge")
            print("  3. Or access Web UI at http://localhost:8112 (password: deluge)")
        return False


def main():
    print("Download Client Test Suite")
    print("=" * 50)
    print("Make sure containers are running:")
    print("  docker compose -f docker-compose.test-clients.yml up -d")

    results = {}

    # Test usenet clients
    print("\n" + "=" * 50)
    print("USENET CLIENTS")
    print("=" * 50)
    results["nzbget"] = test_nzbget()
    results["sabnzbd"] = test_sabnzbd()

    # Test torrent clients
    print("\n" + "=" * 50)
    print("TORRENT CLIENTS")
    print("=" * 50)
    results["qbittorrent"] = test_qbittorrent()
    results["transmission"] = test_transmission()
    results["deluge"] = test_deluge()

    # Summary
    print("\n" + "=" * 50)
    print("SUMMARY")
    print("=" * 50)

    for client, success in results.items():
        status = "PASS" if success else "FAIL"
        print(f"  {client}: {status}")

    passed = sum(results.values())
    total = len(results)
    print(f"\n  Total: {passed}/{total} passed")

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())

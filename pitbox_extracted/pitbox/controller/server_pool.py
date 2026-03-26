import logging
import time
import subprocess
import threading
import socket
import json
import urllib.request
from dataclasses import dataclass, field
from typing import Optional, Dict, Any
from pathlib import Path

from controller.config import get_ac_server_root, get_config
from controller.ini_io import write_ini_atomic
from pitbox_common.runtime_paths import controller_data_dir

logger = logging.getLogger(__name__)


@dataclass
class ServerSlot:
    server_number: int
    tcp_port: int
    udp_port: int
    http_port: int
    status: str = "free"  # free | starting | running | stopping | error
    assigned_host_rig: Optional[str] = None
    track: str = ""
    car: str = ""
    max_players: int = 0
    process: Optional[subprocess.Popen] = None
    player_count: int = 0
    last_seen_nonempty: float = field(default_factory=time.time)
    last_error: str = ""


class ServerPoolManager:
    """Manages a pool of dynamic Assetto Corsa dedicated servers."""

    def __init__(self, pool_size: int = 15):
        self.slots: Dict[int, ServerSlot] = {}
        # Use ports starting from 9600 for UDP/TCP, and 8081 for HTTP
        for i in range(1, pool_size + 1):
            self.slots[i] = ServerSlot(
                server_number=i,
                tcp_port=9600 + i - 1,
                udp_port=9600 + i - 1,
                http_port=8081 + i - 1,
            )
        self._lock = threading.Lock()
        
        # Start background cleanup thread
        self._cleanup_thread = threading.Thread(
            target=self._cleanup_loop, daemon=True, name="server-pool-cleanup"
        )
        self._cleanup_thread.start()

    def get_server_status(self) -> list[dict[str, Any]]:
        """Return the status of all 15 slots."""
        with self._lock:
            status_list = []
            for num, slot in self.slots.items():
                status_list.append({
                    "server_number": slot.server_number,
                    "status": slot.status,
                    "ip": self._get_local_ip(),
                    "tcp_port": slot.tcp_port,
                    "udp_port": slot.udp_port,
                    "http_port": slot.http_port,
                    "assigned_host_rig": slot.assigned_host_rig,
                    "track": slot.track,
                    "car": slot.car,
                    "max_players": slot.max_players,
                    "pid": slot.process.pid if slot.process else None,
                    "player_count": slot.player_count,
                    "last_seen_nonempty": slot.last_seen_nonempty,
                    "last_error": slot.last_error,
                })
            return status_list

    def _get_local_ip(self) -> str:
        """Helper to get a bindable/routable IP for the response."""
        config = get_config()
        if getattr(config, "pool_server_ip", None):
            return config.pool_server_ip
        if getattr(config, "server_host", None):
            return config.server_host
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    def create_server(
        self, track: str, car: str, max_players: int, host_rig_id: Optional[str] = None
    ) -> dict[str, Any]:
        """Allocate a free slot and launch acServer.exe with the specified track/car."""
        with self._lock:
            slot = next((s for s in self.slots.values() if s.status in ("free", "error")), None)
            if not slot:
                raise RuntimeError("No free server slots available in the pool")

            slot.status = "starting"
            slot.track = track
            slot.car = car
            slot.max_players = max_players
            slot.assigned_host_rig = host_rig_id
            slot.player_count = 0
            slot.last_error = ""
            slot.last_seen_nonempty = time.time()

        try:
            ac_server_root = Path(
                getattr(get_config(), "pool_ac_server_root", None)
                or get_ac_server_root()
                or r"C:\Program Files (x86)\Steam\steamapps\common\assettocorsa\server"
            )
            ac_exe = ac_server_root / "acServer.exe"
            
            if not ac_exe.exists():
                raise FileNotFoundError(f"acServer.exe not found at {ac_exe}")

            # Create an isolated directory for this server slot's configs
            slot_dir = controller_data_dir() / "server_pool" / f"slot_{slot.server_number}"
            slot_dir.mkdir(parents=True, exist_ok=True)

            cfg_path = slot_dir / "server_cfg.ini"
            entry_path = slot_dir / "entry_list.ini"

            server_name = f"FASTEST LAP SERVER #{slot.server_number}"

            # Basic server_cfg.ini structure
            server_cfg = {
                "SERVER": {
                    "NAME": server_name,
                    "CARS": car,
                    "TRACK": track,
                    "SUN_ANGLE": "48",
                    "MAX_CLIENTS": str(max_players),
                    "UDP_PORT": str(slot.udp_port),
                    "TCP_PORT": str(slot.tcp_port),
                    "HTTP_PORT": str(slot.http_port),
                    "REGISTER_TO_LOBBY": "1",
                    "PICKUP_CHAT_JOIN": "1",
                    "SLEEP_TIME": "1",
                    "CLIENT_SEND_INTERVAL_HZ": "18",
                    "SEND_BUFFER_SIZE": "0",
                    "RECV_BUFFER_SIZE": "0",
                    "KICK_QUORUM": "85",
                    "VOTING_QUORUM": "80",
                    "VOTE_DURATION": "20",
                    "BLACKLIST_MODE": "1",
                    "FUEL_RATE": "100",
                    "DAMAGE_MULTIPLIER": "100",
                    "TYRE_WEAR_RATE": "100",
                    "ALLOWED_TYRES_OUT": "2",
                    "ABS_ALLOWED": "1",
                    "TCS_ALLOWED": "1",
                    "STABILITY_ALLOWED": "0",
                    "AUTOCLUTCH_ALLOWED": "0",
                    "TYRE_BLANKETS_ALLOWED": "1",
                    "FORCE_VIRTUAL_MIRROR": "1",
                    "LEGAL_TYRES": "V",
                    "LOCKED_ENTRY_LIST": "0",
                }
            }
            write_ini_atomic(cfg_path, server_cfg)

            # Basic entry_list.ini structure
            entry_list = {}
            for i in range(max_players):
                entry_list[f"CAR_{i}"] = {"MODEL": car}
            write_ini_atomic(entry_path, entry_list)

            logger.info("Starting pool server %s on ports TCP/UDP %s", slot.server_number, slot.tcp_port)
            
            creation_flags = (
                subprocess.CREATE_NEW_PROCESS_GROUP
                if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP")
                else 0
            )

            proc = subprocess.Popen(
                [str(ac_exe), "-c", str(cfg_path), "-e", str(entry_path)],
                cwd=ac_server_root,
                creationflags=creation_flags,
            )

            with self._lock:
                slot.process = proc
                slot.status = "running"
                logger.info(f"Slot {slot.server_number} marked running. PID: {proc.pid}")

            return {
                "ok": True,
                "server_number": slot.server_number,
                "ip": self._get_local_ip(),
                "tcp_port": slot.tcp_port,
                "udp_port": slot.udp_port,
                "http_port": slot.http_port,
                "server_name": server_name,
                "server_cfg_snapshot": server_cfg,
            }

        except Exception as e:
            with self._lock:
                slot.status = "error"
                slot.last_error = str(e)
            logger.exception("Failed to create pool server: %s", e)
            raise

    def release_server(self, server_number: int):
        """Kill the server process and release the slot."""
        with self._lock:
            slot = self.slots.get(server_number)
            if not slot:
                return

            if slot.process:
                try:
                    slot.process.terminate()
                    slot.process.wait(timeout=2)
                except Exception:
                    try:
                        slot.process.kill()
                    except Exception:
                        pass

            slot.status = "free"
            slot.process = None
            slot.assigned_host_rig = None
            slot.track = ""
            slot.car = ""
            slot.max_players = 0
            slot.player_count = 0
            slot.last_error = ""
            logger.info(f"Pool server slot {server_number} released.")

    def mark_server_activity(self, server_number: int, player_count: int):
        """Update last_seen_nonempty timestamp if players are present."""
        with self._lock:
            slot = self.slots.get(server_number)
            if slot and slot.status == "running":
                slot.player_count = player_count
                if player_count > 0:
                    slot.last_seen_nonempty = time.time()

    def _cleanup_loop(self):
        """Background loop to reap idle or dead servers."""
        while True:
            time.sleep(30)
            now = time.time()
            to_release = []

            with self._lock:
                for num, slot in self.slots.items():
                    if slot.status == "running":
                        if slot.process and slot.process.poll() is not None:
                            logger.warning("Pool server %s died unexpectedly. Marking error.", num)
                            slot.status = "error"
                            slot.process = None
                            slot.last_error = "Process died unexpectedly"
                            continue

                        # Poll live player count from HTTP API
                        try:
                            req = urllib.request.Request(f"http://127.0.0.1:{slot.http_port}/INFO", method="GET")
                            with urllib.request.urlopen(req, timeout=2.0) as response:
                                data = json.loads(response.read().decode('utf-8'))
                                slot.player_count = int(data.get("clients", 0))
                        except Exception as e:
                            logger.debug("Failed to poll server %s HTTP info: %s", num, e)
                            # Keep old player_count on temporary failure

                        if slot.player_count > 0:
                            slot.last_seen_nonempty = time.time()

                        if slot.player_count == 0 and now - slot.last_seen_nonempty > 300:  # 5 minutes idle
                            logger.info("Pool server %s idle timeout (no players). Releasing.", num)
                            to_release.append(num)

            for num in to_release:
                self.release_server(num)


# Global instance
pool_manager = ServerPoolManager()

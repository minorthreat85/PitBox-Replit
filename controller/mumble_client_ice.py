"""
Mumble ICE client for PitBox controller.

Connects to Murmur via ZeroC Ice (Mumble 1.3.x and most common Mumble 1.4.x installs).
zeroc-ice must be installed: pip install zeroc-ice

All public methods return plain dicts for JSON serialization.
Raises MumbleClientError on failure.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
from typing import Optional

logger = logging.getLogger(__name__)

_ICE_FILE = os.path.join(os.path.dirname(__file__), "MumbleServer.ice")
_ice_loaded = False
_ice_lock = threading.Lock()


class MumbleClientError(Exception):
    pass


def _find_ice_slice_dir() -> str:
    """Return the Ice built-in slice include directory, or empty string if not found."""
    try:
        import Ice
        ice_pkg = os.path.dirname(Ice.__file__)
        # zeroc-ice ships slice files at various locations depending on version/OS
        candidates = [
            os.path.join(ice_pkg, "slice"),
            os.path.join(ice_pkg, "..", "slice"),
            os.path.join(ice_pkg, "..", "..", "slice"),
            os.path.join(ice_pkg, "..", "Ice", "slice"),
            os.path.join(sys.prefix, "slice"),
            os.path.join(sys.prefix, "Lib", "site-packages", "slice"),
        ]
        for c in candidates:
            norm = os.path.normpath(c)
            if os.path.isdir(norm) and os.path.exists(os.path.join(norm, "Ice")):
                return norm
    except Exception:
        pass
    return ""


def _load_ice():
    """Load the MumbleServer.ice slice definition (once)."""
    global _ice_loaded
    if _ice_loaded:
        return
    with _ice_lock:
        if _ice_loaded:
            return
        try:
            import Ice  # noqa: F401
        except ImportError as e:
            raise MumbleClientError(
                "zeroc-ice not installed. Run: pip install zeroc-ice"
            ) from e
        if not os.path.exists(_ICE_FILE):
            raise MumbleClientError(
                f"MumbleServer.ice not found at {_ICE_FILE}"
            )
        try:
            import Ice
            ice_slice_dir = _find_ice_slice_dir()
            includes = f"-I{os.path.dirname(_ICE_FILE)}"
            if ice_slice_dir:
                includes += f" -I{ice_slice_dir}"
            Ice.loadSlice(f"--all {includes} {_ICE_FILE}")
        except Exception as e:
            raise MumbleClientError(f"Failed to load MumbleServer.ice: {e}") from e
        _ice_loaded = True


class MumbleClientICE:
    """Thin wrapper around the Murmur ZeroC Ice interface."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 6502,
        secret: str = "",
        server_id: int = 1,
    ):
        self.host = host
        self.port = port
        self.secret = secret
        self.server_id = server_id

    def _connect(self):
        """Return (Ice communicator, Server proxy, MumbleServer module).
        Caller MUST call ic.destroy() when done.
        """
        _load_ice()
        try:
            import Ice
            import MumbleServer  # loaded by Ice.loadSlice above
        except ImportError as e:
            raise MumbleClientError(f"Ice import failed: {e}") from e

        ic = Ice.initialize(sys.argv)
        try:
            if self.secret:
                ic.getImplicitContext().put("secret", self.secret)
            proxy_str = f"Meta:tcp -h {self.host} -p {self.port}"
            base = ic.stringToProxy(proxy_str)
            meta = MumbleServer.MetaPrx.checkedCast(base)
            if not meta:
                raise MumbleClientError(
                    f"No Meta proxy at {proxy_str} — check host/port and that Murmur ICE is enabled"
                )
            server = meta.getServer(self.server_id)
            if not server:
                raise MumbleClientError(
                    f"Server ID {self.server_id} not found on Murmur"
                )
            return ic, server
        except MumbleClientError:
            ic.destroy()
            raise
        except Exception as e:
            ic.destroy()
            raise MumbleClientError(f"ICE connection failed: {e}") from e

    def get_channels(self) -> list[dict]:
        ic, server = self._connect()
        try:
            channels = server.getChannels()
            result = []
            for cid, ch in channels.items():
                result.append({
                    "id": ch.id,
                    "parent_id": ch.parent if ch.parent != -1 else None,
                    "name": ch.name,
                    "description": ch.description or "",
                    "position": ch.position,
                })
            return result
        except MumbleClientError:
            raise
        except Exception as e:
            raise MumbleClientError(f"getChannels: {e}") from e
        finally:
            ic.destroy()

    def get_users(self) -> list[dict]:
        ic, server = self._connect()
        try:
            users = server.getUsers()
            result = []
            for sid, u in users.items():
                result.append({
                    "session": u.session,
                    "name": u.name,
                    "channel_id": u.channel,
                    "mute": u.mute,
                    "deaf": u.deaf,
                    "suppress": u.suppress,
                    "self_mute": u.selfMute,
                    "self_deaf": u.selfDeaf,
                    "comment": u.comment or "",
                })
            return result
        except MumbleClientError:
            raise
        except Exception as e:
            raise MumbleClientError(f"getUsers: {e}") from e
        finally:
            ic.destroy()

    def mute_user(self, session: int, mute: bool) -> dict:
        ic, server = self._connect()
        try:
            state = server.getState(session)
            state.mute = mute
            server.setState(state)
            return {"success": True, "session": session, "mute": mute}
        except MumbleClientError:
            raise
        except Exception as e:
            raise MumbleClientError(f"setState(mute): {e}") from e
        finally:
            ic.destroy()

    def move_user(self, session: int, channel_id: int) -> dict:
        ic, server = self._connect()
        try:
            state = server.getState(session)
            state.channel = channel_id
            server.setState(state)
            return {"success": True, "session": session, "channel_id": channel_id}
        except MumbleClientError:
            raise
        except Exception as e:
            raise MumbleClientError(f"setState(move): {e}") from e
        finally:
            ic.destroy()

    def kick_user(self, session: int, reason: str = "") -> dict:
        ic, server = self._connect()
        try:
            server.kickUser(session, reason)
            return {"success": True, "session": session}
        except MumbleClientError:
            raise
        except Exception as e:
            raise MumbleClientError(f"kickUser: {e}") from e
        finally:
            ic.destroy()

    def mute_channel(self, channel_id: int, mute: bool) -> list[dict]:
        users = self.get_users()
        results = []
        for u in users:
            if u.get("channel_id") == channel_id:
                try:
                    results.append(self.mute_user(u["session"], mute))
                except MumbleClientError as e:
                    results.append({"success": False, "session": u["session"], "error": str(e)})
        return results

    def send_text_message(self, text: str, channel_id: Optional[int] = None) -> dict:
        ic, server = self._connect()
        try:
            cid = channel_id if channel_id is not None else 0
            server.sendMessageChannel(cid, False, text)
            return {"success": True}
        except MumbleClientError:
            raise
        except Exception as e:
            raise MumbleClientError(f"sendMessageChannel: {e}") from e
        finally:
            ic.destroy()

    def ping(self) -> bool:
        try:
            self.get_channels()
            return True
        except Exception:
            return False

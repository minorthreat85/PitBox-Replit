"""
Mumble gRPC client for PitBox controller.

Connects to Murmur (Mumble 1.4+) via gRPC on localhost.
grpcio must be installed: pip install grpcio

All public methods return plain dicts for JSON serialization.
Raises MumbleClientError on failure.
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)


class MumbleClientError(Exception):
    pass


class MumbleClient:
    """Thin wrapper around the Murmur V1 gRPC service."""

    def __init__(self, host: str = "127.0.0.1", port: int = 50051, token: str = ""):
        self.host = host
        self.port = port
        self.token = token

    def _metadata(self):
        if self.token:
            return [("authorization", f"Bearer {self.token}")]
        return []

    def _stub(self):
        try:
            import grpc
            from controller.MurmurRPC_pb2_grpc import V1Stub
            channel = grpc.insecure_channel(f"{self.host}:{self.port}")
            return channel, V1Stub(channel)
        except ImportError as e:
            raise MumbleClientError(
                f"grpcio not available: {e}. Install grpcio on the controller PC."
            ) from e

    def _server_msg(self, server_id: int = 1):
        from controller.MurmurRPC_pb2 import Server
        return Server(id=server_id)

    def get_channels(self, server_id: int = 1) -> list[dict]:
        """Return flat list of all channels."""
        import grpc
        chan, stub = self._stub()
        try:
            resp = stub.GetAllChannels(self._server_msg(server_id), metadata=self._metadata(), timeout=5)
            result = []
            for c in resp.channels:
                parent_id = None
                if c.HasField("parent"):
                    parent_id = c.parent.id
                result.append({
                    "id": c.id,
                    "parent_id": parent_id,
                    "name": c.name,
                    "description": c.description or "",
                    "position": c.position if c.position else 0,
                })
            return result
        except grpc.RpcError as e:
            raise MumbleClientError(f"GetAllChannels: {e.details()}") from e
        finally:
            chan.close()

    def get_users(self, server_id: int = 1) -> list[dict]:
        """Return list of currently connected users."""
        import grpc
        chan, stub = self._stub()
        try:
            resp = stub.GetAllUsers(self._server_msg(server_id), metadata=self._metadata(), timeout=5)
            result = []
            for u in resp.users:
                channel_id = u.channel.id if u.HasField("channel") else 0
                result.append({
                    "session": u.session,
                    "name": u.name,
                    "channel_id": channel_id,
                    "mute": u.mute if u.HasField("mute") else False,
                    "deaf": u.deaf if u.HasField("deaf") else False,
                    "suppress": u.suppress if u.HasField("suppress") else False,
                    "self_mute": u.self_mute if u.HasField("self_mute") else False,
                    "self_deaf": u.self_deaf if u.HasField("self_deaf") else False,
                    "comment": u.comment if u.HasField("comment") else "",
                })
            return result
        except grpc.RpcError as e:
            raise MumbleClientError(f"GetAllUsers: {e.details()}") from e
        finally:
            chan.close()

    def mute_user(self, session: int, mute: bool, server_id: int = 1) -> dict:
        """Mute or unmute a user by session ID."""
        import grpc
        from controller.MurmurRPC_pb2 import User, Server
        chan, stub = self._stub()
        try:
            user = User(server=Server(id=server_id), session=session, mute=mute)
            stub.UserUpdate(user, metadata=self._metadata(), timeout=5)
            return {"success": True, "session": session, "mute": mute}
        except grpc.RpcError as e:
            raise MumbleClientError(f"UserUpdate(mute): {e.details()}") from e
        finally:
            chan.close()

    def move_user(self, session: int, channel_id: int, server_id: int = 1) -> dict:
        """Move a user to a different channel."""
        import grpc
        from controller.MurmurRPC_pb2 import User, Server, Channel
        chan, stub = self._stub()
        try:
            user = User(server=Server(id=server_id), session=session, channel=Channel(id=channel_id))
            stub.UserUpdate(user, metadata=self._metadata(), timeout=5)
            return {"success": True, "session": session, "channel_id": channel_id}
        except grpc.RpcError as e:
            raise MumbleClientError(f"UserUpdate(move): {e.details()}") from e
        finally:
            chan.close()

    def kick_user(self, session: int, reason: str = "", server_id: int = 1) -> dict:
        """Kick a user from the server."""
        import grpc
        from controller.MurmurRPC_pb2 import User_KickBan, User, Server
        chan, stub = self._stub()
        try:
            req = User_KickBan(
                server=Server(id=server_id),
                user=User(session=session, server=Server(id=server_id)),
                reason=reason,
                ban=False,
            )
            stub.UserKick(req, metadata=self._metadata(), timeout=5)
            return {"success": True, "session": session}
        except grpc.RpcError as e:
            raise MumbleClientError(f"UserKick: {e.details()}") from e
        finally:
            chan.close()

    def mute_channel(self, channel_id: int, mute: bool, server_id: int = 1) -> list[dict]:
        """Mute or unmute every user in a channel."""
        users = self.get_users(server_id=server_id)
        results = []
        for u in users:
            if u.get("channel_id") == channel_id:
                try:
                    results.append(self.mute_user(u["session"], mute, server_id=server_id))
                except MumbleClientError as e:
                    results.append({"success": False, "session": u["session"], "error": str(e)})
        return results

    def send_text_message(self, text: str, channel_id: Optional[int] = None, server_id: int = 1) -> dict:
        """Send a text message to a channel (or root if channel_id is None)."""
        import grpc
        from controller.MurmurRPC_pb2 import TextMessage, Server, Channel
        chan, stub = self._stub()
        try:
            ch = Channel(id=channel_id) if channel_id is not None else Channel(id=0)
            msg = TextMessage(server=Server(id=server_id), channels=[ch], text=text)
            stub.SendMessage(msg, metadata=self._metadata(), timeout=5)
            return {"success": True}
        except grpc.RpcError as e:
            raise MumbleClientError(f"SendMessage: {e.details()}") from e
        finally:
            chan.close()

    def ping(self, server_id: int = 1) -> bool:
        """Return True if the Mumble server is reachable."""
        try:
            self.get_channels(server_id=server_id)
            return True
        except Exception:
            return False


# ── Singleton ─────────────────────────────────────────────────────────────────

_client: Optional[MumbleClient] = None
_client_lock = threading.Lock()


def get_mumble_client() -> MumbleClient:
    global _client
    if _client is not None:
        return _client
    with _client_lock:
        if _client is not None:
            return _client
        try:
            from controller.config import get_config
            cfg = get_config()
            host = getattr(cfg, "mumble_host", None) or "127.0.0.1"
            port = getattr(cfg, "mumble_grpc_port", None) or 50051
            token = getattr(cfg, "mumble_token", None) or ""
        except Exception:
            host, port, token = "127.0.0.1", 50051, ""
        _client = MumbleClient(host=host, port=port, token=token)
        return _client


def reset_mumble_client() -> None:
    global _client
    with _client_lock:
        _client = None

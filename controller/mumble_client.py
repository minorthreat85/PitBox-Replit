"""
Mumble unified client for PitBox controller.

Supports both ZeroC Ice (Mumble 1.3.x / most installs, protocol='ice')
and gRPC (Mumble 1.4+, protocol='grpc').

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
    """Unified Mumble client — delegates to ICE or gRPC backend."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: Optional[int] = None,
        token: str = "",
        protocol: str = "ice",
        secret: str = "",
        server_id: int = 1,
    ):
        self.host = host
        self.protocol = protocol.lower() if protocol else "ice"
        self.server_id = server_id

        if self.protocol == "grpc":
            self.port = port if port is not None else 50051
            self.token = token
        else:
            self.port = port if port is not None else 6502
            self.secret = secret

    def _ice(self):
        from controller.mumble_client_ice import MumbleClientICE, MumbleClientError as ICEError
        return MumbleClientICE(
            host=self.host,
            port=self.port,
            secret=getattr(self, "secret", ""),
            server_id=self.server_id,
        ), ICEError

    def _grpc_metadata(self):
        if self.token:
            return [("authorization", f"Bearer {self.token}")]
        return []

    def _grpc_stub(self):
        try:
            import grpc
            from controller.MurmurRPC_pb2_grpc import V1Stub
            channel = grpc.insecure_channel(f"{self.host}:{self.port}")
            return channel, V1Stub(channel)
        except ImportError as e:
            raise MumbleClientError(
                f"grpcio not available: {e}. Install grpcio on the controller PC."
            ) from e

    def _grpc_server_msg(self, server_id: int = 1):
        from controller.MurmurRPC_pb2 import Server
        return Server(id=server_id)

    def get_channels(self, server_id: int = 1) -> list[dict]:
        """Return flat list of all channels."""
        sid = server_id or self.server_id
        if self.protocol == "grpc":
            import grpc
            chan, stub = self._grpc_stub()
            try:
                resp = stub.GetAllChannels(
                    self._grpc_server_msg(sid),
                    metadata=self._grpc_metadata(),
                    timeout=5,
                )
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
        else:
            ice, ICEError = self._ice()
            try:
                return ice.get_channels()
            except ICEError as e:
                raise MumbleClientError(str(e)) from e

    def get_users(self, server_id: int = 1) -> list[dict]:
        """Return list of currently connected users."""
        sid = server_id or self.server_id
        if self.protocol == "grpc":
            import grpc
            chan, stub = self._grpc_stub()
            try:
                resp = stub.GetAllUsers(
                    self._grpc_server_msg(sid),
                    metadata=self._grpc_metadata(),
                    timeout=5,
                )
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
        else:
            ice, ICEError = self._ice()
            try:
                return ice.get_users()
            except ICEError as e:
                raise MumbleClientError(str(e)) from e

    def mute_user(self, session: int, mute: bool, server_id: int = 1) -> dict:
        """Mute or unmute a user by session ID."""
        if self.protocol == "grpc":
            import grpc
            from controller.MurmurRPC_pb2 import User, Server
            chan, stub = self._grpc_stub()
            try:
                user = User(server=Server(id=server_id), session=session, mute=mute)
                stub.UserUpdate(user, metadata=self._grpc_metadata(), timeout=5)
                return {"success": True, "session": session, "mute": mute}
            except grpc.RpcError as e:
                raise MumbleClientError(f"UserUpdate(mute): {e.details()}") from e
            finally:
                chan.close()
        else:
            ice, ICEError = self._ice()
            try:
                return ice.mute_user(session, mute)
            except ICEError as e:
                raise MumbleClientError(str(e)) from e

    def move_user(self, session: int, channel_id: int, server_id: int = 1) -> dict:
        """Move a user to a different channel."""
        if self.protocol == "grpc":
            import grpc
            from controller.MurmurRPC_pb2 import User, Server, Channel
            chan, stub = self._grpc_stub()
            try:
                user = User(server=Server(id=server_id), session=session, channel=Channel(id=channel_id))
                stub.UserUpdate(user, metadata=self._grpc_metadata(), timeout=5)
                return {"success": True, "session": session, "channel_id": channel_id}
            except grpc.RpcError as e:
                raise MumbleClientError(f"UserUpdate(move): {e.details()}") from e
            finally:
                chan.close()
        else:
            ice, ICEError = self._ice()
            try:
                return ice.move_user(session, channel_id)
            except ICEError as e:
                raise MumbleClientError(str(e)) from e

    def kick_user(self, session: int, reason: str = "", server_id: int = 1) -> dict:
        """Kick a user from the server."""
        if self.protocol == "grpc":
            import grpc
            from controller.MurmurRPC_pb2 import User_KickBan, User, Server
            chan, stub = self._grpc_stub()
            try:
                req = User_KickBan(
                    server=Server(id=server_id),
                    user=User(session=session, server=Server(id=server_id)),
                    reason=reason,
                    ban=False,
                )
                stub.UserKick(req, metadata=self._grpc_metadata(), timeout=5)
                return {"success": True, "session": session}
            except grpc.RpcError as e:
                raise MumbleClientError(f"UserKick: {e.details()}") from e
            finally:
                chan.close()
        else:
            ice, ICEError = self._ice()
            try:
                return ice.kick_user(session, reason)
            except ICEError as e:
                raise MumbleClientError(str(e)) from e

    def mute_channel(self, channel_id: int, mute: bool, server_id: int = 1) -> list[dict]:
        """Mute or unmute every user in a channel."""
        if self.protocol == "grpc":
            users = self.get_users(server_id=server_id)
            results = []
            for u in users:
                if u.get("channel_id") == channel_id:
                    try:
                        results.append(self.mute_user(u["session"], mute, server_id=server_id))
                    except MumbleClientError as e:
                        results.append({"success": False, "session": u["session"], "error": str(e)})
            return results
        else:
            ice, ICEError = self._ice()
            try:
                return ice.mute_channel(channel_id, mute)
            except ICEError as e:
                raise MumbleClientError(str(e)) from e

    def send_text_message(self, text: str, channel_id: Optional[int] = None, server_id: int = 1) -> dict:
        """Send a text message to a channel (or root if channel_id is None)."""
        if self.protocol == "grpc":
            import grpc
            from controller.MurmurRPC_pb2 import TextMessage, Server, Channel
            chan, stub = self._grpc_stub()
            try:
                ch = Channel(id=channel_id) if channel_id is not None else Channel(id=0)
                msg = TextMessage(server=Server(id=server_id), channels=[ch], text=text)
                stub.SendMessage(msg, metadata=self._grpc_metadata(), timeout=5)
                return {"success": True}
            except grpc.RpcError as e:
                raise MumbleClientError(f"SendMessage: {e.details()}") from e
            finally:
                chan.close()
        else:
            ice, ICEError = self._ice()
            try:
                return ice.send_text_message(text, channel_id)
            except ICEError as e:
                raise MumbleClientError(str(e)) from e

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
            protocol = getattr(cfg, "mumble_protocol", None) or "ice"
            host = getattr(cfg, "mumble_host", None) or "127.0.0.1"
            if protocol == "grpc":
                port = getattr(cfg, "mumble_grpc_port", None) or 50051
                token = getattr(cfg, "mumble_token", None) or ""
                secret = ""
            else:
                port = getattr(cfg, "mumble_ice_port", None) or 6502
                token = ""
                secret = getattr(cfg, "mumble_secret", None) or ""
        except Exception:
            protocol, host, port, token, secret = "ice", "127.0.0.1", 6502, "", ""
        _client = MumbleClient(
            host=host,
            port=port,
            token=token,
            protocol=protocol,
            secret=secret,
        )
        return _client


def reset_mumble_client() -> None:
    global _client
    with _client_lock:
        _client = None

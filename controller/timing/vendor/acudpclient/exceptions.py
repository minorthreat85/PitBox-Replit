"""Custom exceptions used by the vendored acudpclient."""


class ACUDPClientException(Exception):
    """Base class for custom ACUDPClient exceptions."""


class NotEnoughBytes(ACUDPClientException):
    """Raised by ACUDPPacket.factory when a partial packet is in the buffer."""

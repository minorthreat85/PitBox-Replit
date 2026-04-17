"""Synchronous AC UDP client (kept for completeness; PitBox's engine uses
its own asyncio DatagramProtocol and does not depend on this class)."""
import io
import logging
import socket
import struct

from controller.timing.vendor.acudpclient.protocol import ACUDPConst
from controller.timing.vendor.acudpclient.packet_base import ACUDPPacket
# Importing the packet definitions registers them as ACUDPPacket subclasses
# so that ACUDPPacket.factory() can dispatch to them.
from controller.timing.vendor.acudpclient import packets  # noqa: F401


LOG = logging.getLogger("ac_udp_client")


class ACUDPClient(object):
    def __init__(self, port=10000, host='127.0.0.1', remote_port=10001):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.server_address = ('0.0.0.0', port)
        self.remote_port = remote_port
        self.host = host
        self._subscribers = {}
        self.file = None

    def listen(self):
        self.sock.bind(self.server_address)
        self.sock.setblocking(False)
        self.file = io.open(self.sock.fileno(), mode='rb', buffering=4096)

    def subscribe(self, subscriber):
        if id(subscriber) in self._subscribers:
            return False
        self._subscribers[id(subscriber)] = subscriber
        return True

    def unsubscribe(self, subscriber):
        if id(subscriber) not in self._subscribers:
            return False
        del self._subscribers[id(subscriber)]
        return True

    def get_next_event(self, call_subscribers=True):
        event = ACUDPPacket.factory(self.file)
        if event and call_subscribers:
            for subs in self._subscribers.values():
                method_name = 'on_%s' % (event.packet_name(),)
                method = getattr(subs, method_name, None)
                if method and callable(method):
                    method(event)
        return event

    def _sendto(self, data):
        sent = self.sock.sendto(data, (self.host, self.remote_port))
        if sent != len(data):
            raise ValueError('Not all bytes were sent.')

    def broadcast_message(self, message):
        size = len(message)
        if size > 255:
            raise ValueError('Message is too large')
        data = struct.pack("BB%ds" % (size * 4,),
                           ACUDPConst.ACSP_BROADCAST_CHAT,
                           size,
                           message.encode('utf_32_le'))
        self._sendto(data)

    def send_message(self, car_id, message):
        size = len(message)
        if size > 255:
            raise ValueError('Message is too large')
        data = struct.pack("BBB%ds" % (size * 4,),
                           ACUDPConst.ACSP_SEND_CHAT,
                           car_id,
                           size,
                           message.encode('utf_32_le'))
        self._sendto(data)

    def get_car_info(self, car_id):
        data = struct.pack("BB",
                           ACUDPConst.ACSP_GET_CAR_INFO,
                           car_id)
        self._sendto(data)

    def get_session_info(self, session_index=-1):
        data = struct.pack("<Bh",
                           ACUDPConst.ACSP_GET_SESSION_INFO,
                           session_index)
        self._sendto(data)

    def enable_realtime_report(self, hz_ms=1000):
        data = struct.pack("<BH",
                           ACUDPConst.ACSP_REALTIMEPOS_INTERVAL,
                           hz_ms)
        self._sendto(data)

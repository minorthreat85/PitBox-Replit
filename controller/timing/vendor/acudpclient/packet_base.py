"""Base classes for AC UDP packets."""
import logging
import struct

from controller.timing.vendor.acudpclient.protocol import ACUDPConst
from controller.timing.vendor.acudpclient.types import UINT8
from controller.timing.vendor.acudpclient.exceptions import NotEnoughBytes


LOG = logging.getLogger("ac_udp_packets")


class ACUDPPacket(object):
    @classmethod
    def packets(cls):
        pkts = {}
        for subclass in cls.__subclasses__():
            pkts[getattr(subclass, '_type')] = subclass
        return pkts

    @classmethod
    def factory(cls, file_obj):
        try:
            type_ = UINT8.get(file_obj)
            if type_ in ACUDPPacket.packets():
                class_ = ACUDPPacket.packets()[type_]
                return class_.from_file(file_obj)
            raise NotImplementedError("Type not implemented %s" % (type_,))
        except struct.error:
            raise NotEnoughBytes

    @classmethod
    def from_file(cls, file_obj):
        instance = cls()
        for name, data_type in cls._bytes:
            val = data_type.get(file_obj, instance)
            setattr(instance, name, val)
        return instance

    def packet_name(self):
        return ACUDPConst.id_to_name(self._type)

    def __repr__(self):
        return "<Packet(%s) %s>" % (
            ACUDPConst.id_to_name(self._type),
            ' '.join(["%s=%r" % (name, getattr(self, name, ''))
                      for name, _ in self._bytes])
        )


class ACUDPPacketData(object):
    @classmethod
    def from_file(cls, file_obj):
        instance = cls()
        for name, data_type in cls._bytes:
            val = data_type.get(file_obj)
            setattr(instance, name, val)
        return instance

    def __repr__(self):
        return "<%s>" % ' '.join(
            ["%s=%r" % (name, getattr(self, name, ''))
             for name, _ in self._bytes]
        )

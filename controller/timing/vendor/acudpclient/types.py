"""Core C types used by the AC UDP protocol."""
import struct


class ACUDPStruct(object):
    def __init__(self, fmt, formatter=lambda x: x):
        # AC's UDP protocol is little-endian on the wire. Force the byte-order
        # explicitly so we decode correctly on any architecture rather than
        # relying on the host's native endianness.
        if fmt and fmt[0] not in '<>=!@':
            fmt = '<' + fmt
        self.fmt = fmt
        self.formatter = formatter

    def size(self):
        return struct.calcsize(self.fmt)

    def get(self, file_obj, _context=None):
        bytes_ = file_obj.read(self.size())
        data = struct.unpack(self.fmt, bytes_)
        if len(data) == 1:
            return self.formatter(data[0])
        return self.formatter(data)


class ACUDPString(object):
    def __init__(self, char_size=1, decoder=lambda x: x.decode('ascii')):
        self.char_size = char_size
        self.decoder = decoder

    def get(self, file_obj, _context=None):
        size = UINT8.get(file_obj)
        bytes_ = file_obj.read(self.char_size * size)
        return self.decoder(bytes_)


class ACUDPConditionalStruct(object):
    def __init__(self, ac_struct, cond_func=lambda x: True, default=''):
        self.ac_struct = ac_struct
        self.cond_func = cond_func
        self.default = default

    def size(self):
        return self.ac_struct.size()

    def get(self, file_obj, context=None):
        if not self.cond_func(context):
            return self.default
        return self.ac_struct.get(file_obj, context)


class ACUDPPacketDataArray(object):
    def __init__(self, packet_data):
        self.packet_data = packet_data

    def get(self, file_obj, _context=None):
        size = UINT8.get(file_obj)
        return [self.packet_data.from_file(file_obj) for _ in range(size)]


def _decode_utf32(raw):
    """Decode UTF-32 LE bytes from the AC server.

    The plugin protocol sends raw UTF-32 little-endian without a BOM, so we
    decode with the explicit codec rather than the BOM-sensitive 'utf-32'
    alias. Any stray BOM is stripped defensively.
    """
    text = raw.decode('utf_32_le', errors='replace')
    if text.startswith('\ufeff'):
        text = text[1:]
    return text


UINT8 = ACUDPStruct('B')
BOOL = ACUDPStruct('B', formatter=lambda x: x != 0)
UINT16 = ACUDPStruct('H')
INT16 = ACUDPStruct('h')
UINT32 = ACUDPStruct('I')
INT32 = ACUDPStruct('i')
FLOAT = ACUDPStruct('f')
VECTOR3F = ACUDPStruct('fff')
UTF32 = ACUDPString(4, decoder=_decode_utf32)
ASCII = ACUDPString(1, decoder=lambda x: x.decode('ascii', errors='replace'))

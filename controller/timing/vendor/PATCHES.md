# Vendor patches

## acudpclient 1.1.0 (PyPI)

Upstream is Python 2 only. Patches applied locally:

- `client.py`
  - `dict.itervalues()` → `dict.values()` (Py3 removed itervalues).
  - `sock.setblocking(0)` → `sock.setblocking(False)` (style; behaviourally identical).
  - Imports rewritten from `acudpclient.X` to `controller.timing.vendor.acudpclient.X`.
- `packet_base.py`
  - `__repr__` of `ACUDPPacket` and `ACUDPPacketData` returned `bytes`
    (`output.encode('utf-8')`); Py3 requires `str`. Returns `output` directly.
  - Imports rewritten as above.
- `packets.py`, `protocol.py`, `exceptions.py`
  - Imports rewritten as above; no behavioural changes.
- `types.py`
  - Imports rewritten as above.
  - Numeric `ACUDPStruct` formats now force little-endian (`'<' + fmt`) so the
    decoder is byte-order-independent. AC's plugin protocol is little-endian
    on the wire; this matches the spec explicitly rather than relying on the
    host's native endianness.
  - `UTF32` now decodes with `utf_32_le` (no BOM) instead of the BOM-sensitive
    `'utf-32'` alias, matching what the AC dedicated server transmits.
  - `ASCII` decoder uses `errors='replace'` so a malformed driver name in a
    packet cannot crash the parser.
- `__init__.py`
  - Reduced to a one-liner exposing `VERSION`.

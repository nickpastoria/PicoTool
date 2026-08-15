"""Dependency-free PNG reader/writer.

Only what this tool needs: read the common things image editors emit
(indexed / gray / rgb / rgba, bit depths 1-16, non-interlaced) and write
8-bit indexed PNGs with a 16-colour palette.
"""

import struct
import zlib

PNG_SIG = b"\x89PNG\r\n\x1a\n"


class PngError(Exception):
    pass


class Image:
    """mode "P": data is a list of palette indices, palette is [(r,g,b), ...].
    mode "RGBA": data is a list of (r, g, b, a) tuples."""

    __slots__ = ("width", "height", "mode", "data", "palette")

    def __init__(self, width, height, mode, data, palette=None):
        self.width = width
        self.height = height
        self.mode = mode
        self.data = data
        self.palette = palette

    def pixel(self, x, y):
        return self.data[y * self.width + x]


def _chunks(blob):
    if blob[:8] != PNG_SIG:
        raise PngError("not a PNG file (bad signature)")
    pos = 8
    while pos + 8 <= len(blob):
        (length,) = struct.unpack(">I", blob[pos:pos + 4])
        ctype = blob[pos + 4:pos + 8]
        data = blob[pos + 8:pos + 8 + length]
        yield ctype, data
        pos += 12 + length


def _unfilter(raw, height, bpp, stride):
    out = bytearray()
    prev = bytearray(stride)
    pos = 0
    for _ in range(height):
        if pos >= len(raw):
            raise PngError("truncated image data")
        ft = raw[pos]
        pos += 1
        line = bytearray(raw[pos:pos + stride])
        if len(line) < stride:
            line.extend(bytes(stride - len(line)))
        pos += stride

        if ft == 0:
            pass
        elif ft == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 255
        elif ft == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 255
        elif ft == 3:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 255
        elif ft == 4:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                if pa <= pb and pa <= pc:
                    pr = a
                elif pb <= pc:
                    pr = b
                else:
                    pr = c
                line[i] = (line[i] + pr) & 255
        else:
            raise PngError("unknown scanline filter %d" % ft)

        out += line
        prev = line
    return out


def _samples(row_bytes, count, depth):
    """Pull `count` samples of `depth` bits out of one unfiltered scanline."""
    if depth == 8:
        return list(row_bytes[:count])
    if depth == 16:
        return [row_bytes[i * 2] for i in range(count)]
    out = []
    per_byte = 8 // depth
    mask = (1 << depth) - 1
    for i in range(count):
        byte = row_bytes[i // per_byte]
        shift = 8 - depth * (i % per_byte + 1)
        out.append((byte >> shift) & mask)
    return out


def read(path):
    with open(path, "rb") as fh:
        blob = fh.read()

    width = height = depth = ctype = None
    interlace = 0
    plte = None
    trns = None
    idat = bytearray()

    for name, data in _chunks(blob):
        if name == b"IHDR":
            width, height, depth, ctype, _, _, interlace = struct.unpack(">IIBBBBB", data[:13])
        elif name == b"PLTE":
            plte = [tuple(data[i:i + 3]) for i in range(0, len(data) - 2, 3)]
        elif name == b"tRNS":
            trns = data
        elif name == b"IDAT":
            idat += data
        elif name == b"IEND":
            break

    if width is None:
        raise PngError("missing IHDR chunk")
    if interlace:
        raise PngError("interlaced (Adam7) PNGs are not supported - re-save without interlacing")

    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(ctype)
    if channels is None:
        raise PngError("unsupported PNG colour type %d" % ctype)

    raw = zlib.decompress(bytes(idat))
    bits_per_pixel = channels * depth
    bpp = max(1, bits_per_pixel // 8)
    stride = (width * bits_per_pixel + 7) // 8
    flat = _unfilter(raw, height, bpp, stride)

    if ctype == 3:
        if plte is None:
            raise PngError("indexed PNG without a palette")
        data = []
        for y in range(height):
            row = flat[y * stride:(y + 1) * stride]
            data.extend(_samples(row, width, depth))
        return Image(width, height, "P", data, plte)

    scale = 255 // ((1 << depth) - 1) if depth < 8 else 1
    data = []
    for y in range(height):
        row = flat[y * stride:(y + 1) * stride]
        vals = _samples(row, width * channels, depth)
        for x in range(width):
            v = vals[x * channels:(x + 1) * channels]
            if ctype == 0:
                g = v[0] * scale
                a = 255
                if trns and len(trns) >= 2 and v[0] == struct.unpack(">H", trns[:2])[0]:
                    a = 0
                data.append((g, g, g, a))
            elif ctype == 4:
                g = v[0] * scale
                data.append((g, g, g, v[1] * scale))
            elif ctype == 2:
                data.append((v[0] * scale, v[1] * scale, v[2] * scale, 255))
            else:
                data.append((v[0] * scale, v[1] * scale, v[2] * scale, v[3] * scale))
    return Image(width, height, "RGBA", data, None)


def _chunk(name, data):
    body = name + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)


def write_indexed(path, width, height, indices, palette):
    """Write an 8-bit indexed PNG. `indices` is a flat row-major list of ints."""
    plte = bytearray()
    for r, g, b in palette:
        plte += bytes((r, g, b))

    raw = bytearray()
    for y in range(height):
        raw.append(0)
        raw += bytes(indices[y * width:(y + 1) * width])

    blob = bytearray(PNG_SIG)
    blob += _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 3, 0, 0, 0))
    blob += _chunk(b"PLTE", bytes(plte))
    blob += _chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    blob += _chunk(b"IEND", b"")

    with open(path, "wb") as fh:
        fh.write(blob)

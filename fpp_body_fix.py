"""Cyberpunk 2077 2.5: fix the missing first-person body on characters made before 2.5.

Characters created before patch 2.5 keep an old FPP_Body group in the save that has no first-person skin entry, so
on 2.5 you can't see your torso when you look down in first person (inventory and photo mode are fine).
This tool writes the first-person skin entries the game itself uses for that group (female_cco's FPP_Body options):
fpp_body_color and fpp_body_color_censored, with the first-person look of the character's own skin tone
(TPP_Body's body_color look, "_base__" -> "_fpp__"), and keeps the group's other first-person entries.
1.0/1.1 copied the third-person entries (body_color, ...) instead: the torso showed until the next visit to a mirror
or ripperdoc, which rebuilds FPP_Body from the fpp_ options and dropped it again. Saves fixed with 1.0/1.1 are
repaired by running this version on them.

Usage: drag a save folder onto "Fix first-person body.bat", or: python fpp_body_fix.py <save folder> [...]
The fixed copy is written as a new save (ManualSave-50 and up) next to the original. Nothing is overwritten.
Everything else in the save is kept byte for byte, and the result is re-read and checked before it's written.
Needs Python 3.8 or newer, nothing else.
"""
import shutil
import struct
import sys
import tempfile
from pathlib import Path


def need(ok, why):
    """A real check, unlike assert, which python -O / PYTHONOPTIMIZE would skip."""
    if not ok:
        raise ValueError(why)


def lz4_block_decompress(src, size):
    """Plain LZ4 block format (no frame header)."""
    out, i = bytearray(), 0
    while i < len(src):
        token = src[i]
        i += 1
        lit = token >> 4
        if lit == 15:
            while True:
                b = src[i]
                i += 1
                lit += b
                if b != 255:
                    break
        out += src[i:i + lit]
        i += lit
        if i >= len(src):
            break
        off = src[i] | src[i + 1] << 8
        i += 2
        mlen = token & 15
        if mlen == 15:
            while True:
                b = src[i]
                i += 1
                mlen += b
                if b != 255:
                    break
        mlen += 4
        start = len(out) - off
        for k in range(mlen):  # byte by byte: matches may overlap themselves
            out.append(out[start + k])
    need(len(out) == size, f"LZ4 gave {len(out)} bytes, expected {size}")
    return bytes(out)


def lz4_block_compress(src):
    """Greedy LZ4 block compressor (valid block format; ratio is not the point)."""
    n, out, table, anchor, i = len(src), bytearray(), {}, 0, 0

    def length_bytes(v):
        res = bytearray()
        while v >= 255:
            res.append(255)
            v -= 255
        res.append(v)
        return res

    def emit(lit, mlen, off):
        token_lit = min(len(lit), 15)
        token_m = 0 if mlen is None else min(mlen - 4, 15)
        out.append(token_lit << 4 | token_m)
        if len(lit) >= 15:
            out.extend(length_bytes(len(lit) - 15))
        out.extend(lit)
        if mlen is not None:
            out.extend(struct.pack("<H", off))
            if mlen - 4 >= 15:
                out.extend(length_bytes(mlen - 4 - 15))

    limit = n - 12  # LZ4: the last match must start at least 12 bytes before the end
    while i < limit:
        key = src[i:i + 4]
        cand = table.get(key)
        table[key] = i
        if cand is not None and i - cand <= 0xFFFF:
            m = 4
            while i + m < n - 5 and src[cand + m] == src[i + m]:  # last 5 bytes stay literals
                m += 1
            emit(src[anchor:i], m, i - cand)
            i += m
            anchor = i
        else:
            i += 1
    emit(src[anchor:], None, 0)
    return bytes(out)


need(lz4_block_decompress(lz4_block_compress(b"abcabcabcabcabcabc-xyz" * 50), 1100) == b"abcabcabcabcabcabc-xyz" * 50,
     "LZ4 self-test failed")


class Save:
    def __init__(self, data):
        if data[:4] != b"VASC":
            raise ValueError("not a CSAV save")
        self.raw = data
        self.version, self.game_version = struct.unpack_from("<II", data, 4)
        pos = data.index(b"FZLC")
        count = struct.unpack_from("<I", data, pos + 4)[0]
        self.chunks = [struct.unpack_from("<III", data, pos + 8 + i * 12) for i in range(count)]  # offset, zsize, size
        self.table_end = pos + 8 + count * 12
        parts = []
        for off, zsize, size in self.chunks:
            blob = data[off:off + zsize]
            if blob[:4] == b"4ZLX":  # "XLZ4" chunk: u32 size, then an LZ4 block
                parts.append(lz4_block_decompress(blob[8:], struct.unpack_from("<I", blob, 4)[0]))
            else:
                parts.append(lz4_block_decompress(blob, size))
        self.data = b"".join(parts)
        self.data_start = self.chunks[0][0]  # node offsets count from the file start, data begins at the 1st chunk
        self.nodes = self._read_nodes()

    def _read_nodes(self):
        """[(name, next, child, offset, size)] from the "EDON" table at the end of the file."""
        d = self.raw
        pos = struct.unpack_from("<I", d, len(d) - 8)[0]
        self.table_pos = pos
        need(d[pos:pos + 4] == b"EDON", "node table not found")
        pos += 4
        count, pos = read_vlq(d, pos)
        nodes, self.record_pos = [], []
        for _ in range(count):
            n, pos = read_vlq(d, pos)  # name length (the high-bit flag marks ASCII)
            name = d[pos:pos + n].decode("utf-8", "replace")
            pos += n
            nxt, child, off, size = struct.unpack_from("<iiII", d, pos)
            self.record_pos.append(pos)  # where this node's 16-byte record starts in the file
            pos += 16
            nodes.append((name, nxt, child, off, size))
        need(pos == len(d) - 8, "node table doesn't end at the footer")
        return nodes

    def node(self, name):
        name_, nxt, child, off, size = next(n for n in self.nodes if n[0] == name)
        return self.data[off - self.data_start:off - self.data_start + size]


def read_vlq(d, pos):
    """CDPR's packed int: bit 7 of the first byte is a flag, bit 6 a continuation, then 7 bits per byte."""
    b = d[pos]
    pos += 1
    value, shift = b & 0x3F, 6
    more = b & 0x40
    while more:
        b = d[pos]
        pos += 1
        value |= (b & 0x7F) << shift
        shift += 7
        more = b & 0x80
    return value, pos


NODE = "CharacetrCustomization_Appearances"  # CDPR's spelling
CHUNK = 0x40000
APPS = "base\\characters\\common\\player_base_bodies\\appearances\\"


def fnv1a64(path):
    """The game's resource path hash (paths are stored this way in the save)."""
    h = 0xCBF29CE484222325
    for b in path.lower().replace("/", "\\").encode():
        h = ((h ^ b) * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return h


need(fnv1a64("") == 0xCBF29CE484222325 and fnv1a64("a") == 0xAF63DC4C8601EC8C, "FNV-1a self-test failed")
FPP_APP = fnv1a64(APPS + "t0_000_fpp__full.app")
FPP_APP_CENSORED = fnv1a64(APPS + "t0_000_fpp__full_censored.app")


def packed(name):
    need(len(name) < 64, f"name too long for a one-byte length: {name}")
    return bytes([0x80 | len(name)]) + name.encode()


def group(d, name):
    """(start, end, entries) of a body group: name, u32 count, then per entry hash, look, option, two u32.
    Entries are (option, look, hash, a, b)."""
    start = d.index(packed(name))
    n, p = read_vlq(d, start)
    p += n
    count = struct.unpack_from("<I", d, p)[0]
    p += 4
    entries = []
    for _ in range(count):
        h = struct.unpack_from("<Q", d, p)[0]
        p += 8
        n, p = read_vlq(d, p)
        look = d[p:p + n].decode()
        p += n
        n, p = read_vlq(d, p)
        option = d[p:p + n].decode()
        p += n
        a, b = struct.unpack_from("<II", d, p)
        p += 8
        entries.append((option, look, h, a, b))
    return start, p, entries


def encode_group(name, entries):
    out = packed(name) + struct.pack("<I", len(entries))
    for option, look, h, a, b in entries:
        out += struct.pack("<Q", h) + packed(look) + packed(option) + struct.pack("<II", a, b)
    return out


def first_person_entries(node):
    """The FPP_Body entries this save should have: the first-person skin pair, then the group's own first-person
    entries (fpp_nipples_*, underpants, ...); third-person copies made by 1.0/1.1 are dropped."""
    _, _, fpp = group(node, "FPP_Body")
    _, _, tpp = group(node, "TPP_Body")
    skin = [look for option, look, *_ in tpp if option == "body_color"]
    need(len(skin) == 1 and "_base__" in skin[0], f"TPP_Body has no usable body_color ({skin})")
    look = skin[0].replace("_base__", "_fpp__")  # t0_000_pwa_base__01_ca_pale -> t0_000_pwa_fpp__01_ca_pale
    keep = [e for e in fpp if e[0] not in ("fpp_body_color", "fpp_body_color_censored")
            and (e[0].startswith("fpp_") or e[0] == "underpants" or e[0].startswith("body_scars"))]
    # tails as the game writes them: (1,1) for the main look, (1,0) for the censored one
    return [("fpp_body_color", look, FPP_APP, 1, 1), ("fpp_body_color_censored", look, FPP_APP_CENSORED, 1, 0)] + keep


def fixed_node(node):
    f0, f1, fpp = group(node, "FPP_Body")
    want = first_person_entries(node)
    if fpp == want:
        return None  # already has the 2.5 first-person entries
    return node[:f0] + encode_group("FPP_Body", want) + node[f1:]


def rebuild(src, new_node):
    s = Save(src)
    i = [n[0] for n in s.nodes].index(NODE)
    _, _, _, off, size = s.nodes[i]
    delta = len(new_node) - size
    a = off - s.data_start
    data = s.data[:a] + new_node + s.data[a + size:]

    need(all(c[2] == CHUNK for c in s.chunks[:-1]), "unexpected chunk layout")
    first = a // CHUNK  # chunks before the edit are copied as they are
    blobs = [src[c[0]:c[0] + c[1]] for c in s.chunks[:first]]
    for k in range(first * CHUNK, len(data), CHUNK):
        piece = data[k:k + CHUNK]
        blobs.append(b"4ZLX" + struct.pack("<I", len(piece)) + lz4_block_compress(piece))
    sizes = [len(data[k:k + CHUNK]) for k in range(0, len(data), CHUNK)]

    fz = src.index(b"FZLC")
    capacity = (s.data_start - fz - 8) // 12
    need(len(blobs) <= capacity, "chunk table full")
    table, pos = bytearray(), s.data_start
    for blob, sz in zip(blobs, sizes):
        table += struct.pack("<III", pos, len(blob), sz)
        pos += len(blob)
    head = src[:fz] + b"FZLC" + struct.pack("<I", len(blobs)) + table
    head += b"\0" * (s.data_start - len(head))

    nodes = bytearray(src[s.table_pos:len(src) - 8])  # same records, shifted offsets
    for (name, nxt, child, noff, nsize), rp in zip(s.nodes, s.record_pos):
        r = rp - s.table_pos
        if noff > off:
            struct.pack_into("<I", nodes, r + 8, noff + delta)
        if noff <= off and noff + nsize >= off + size:  # the node itself, or a node that contains it
            struct.pack_into("<I", nodes, r + 12, nsize + delta)
    body = b"".join(blobs)
    out = head + body + nodes + struct.pack("<I", s.data_start + len(body)) + b"ENOD"
    return out, s, delta


def check(src_save, out, new_node, delta):
    t = Save(out)
    need(len(t.data) == len(src_save.data) + delta, "fixed save has the wrong size")
    need(t.node(NODE) == new_node, "fixed save doesn't read back the new body entries")
    _, _, fpp = group(new_node, "FPP_Body")
    need(fpp == first_person_entries(new_node) and fpp[0][0] == "fpp_body_color",
         f"FPP_Body didn't come out as intended ({[o for o, *_ in fpp]})")
    need(group(new_node, "TPP_Body")[2] == group(src_save.node(NODE), "TPP_Body")[2], "TPP_Body changed")
    need(len(src_save.nodes) == len(t.nodes), "fixed save has a different number of nodes")
    for (name, *_), (name2, *_) in zip(src_save.nodes, t.nodes):
        need(name == name2, f"node {name} moved")
        if name != NODE:
            need(src_save.node(name) == t.node(name), f"node {name} changed")


def fix_folder(src_dir):
    """Fix one save folder into the next free ManualSave-N slot beside it. Returns the new folder, or None."""
    src = (src_dir / "sav.dat").read_bytes()
    s = Save(src)
    new_node = fixed_node(s.node(NODE))
    if new_node is None:
        print(f"{src_dir.name}: already has the 2.5 first-person body entries (fpp_body_color). Nothing to do.")
        return None
    out, s, delta = rebuild(src, new_node)
    check(s, out, new_node, delta)
    n = 50
    while (src_dir.parent / f"ManualSave-{n}").exists():  # never overwrite an existing save
        n += 1
    out_dir = src_dir.parent / f"ManualSave-{n}"
    # build the save in a hidden temp folder and rename it at the end, so a failed write never leaves a broken save
    tmp = Path(tempfile.mkdtemp(prefix=".fpp_fix_", dir=src_dir.parent))
    try:
        (tmp / "sav.dat").write_bytes(out)
        for f in src_dir.iterdir():
            if f.is_file() and f.name != "sav.dat":
                shutil.copy2(f, tmp / f.name)
        tmp.rename(out_dir)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    print(f"{src_dir.name}: fixed. The fixed copy is the new save {out_dir.name} (your original is unchanged).")
    return out_dir


def main():
    if len(sys.argv) < 2:
        print("Drag a save folder (for example ManualSave-3) onto 'Fix first-person body.bat'.")
        return 1
    status = 0
    for arg in sys.argv[1:]:
        src_dir = Path(arg)
        if not (src_dir / "sav.dat").is_file():
            print(f"{src_dir.name}: no sav.dat in this folder. Drag the save folder itself, not a file in it.")
            status = 1
            continue
        try:
            fix_folder(src_dir)
        except Exception as e:  # unknown save layout: stop without writing anything
            print(f"{src_dir.name}: not fixed, this save doesn't look like the ones the tool knows ({e}). "
                  f"Nothing was written.")
            status = 1
    return status


if __name__ == "__main__":
    sys.exit(main())

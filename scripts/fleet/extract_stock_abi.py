#!/usr/bin/env python3
"""Extract an independent vmlinux export/CRC baseline from an official Image.

Run locally with pyelftools after symbolizing Image using vmlinux-to-elf.
The ELF supplies symbol addresses only; table contents come from the raw Image.
The resulting baseline covers the replaced kernel, not separate stock modules.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import struct


def extract(image, elf_data, release):
    from elftools.elf.elffile import ELFFile

    assert ("Linux version " + release + " ").encode() in image
    elf = ELFFile(io.BytesIO(elf_data))
    symbols = {s.name: s["st_value"] for s in elf.get_section_by_name(".symtab").iter_symbols()}
    base = symbols["_text"]
    kernel = elf.get_section_by_name(".kernel")
    assert kernel["sh_addr"] == base and kernel.data() == image
    rows = {}
    for suffix, kind in [("", "EXPORT_SYMBOL"), ("_gpl", "EXPORT_SYMBOL_GPL")]:
        start = symbols["__start___ksymtab" + suffix] - base
        stop = symbols["__stop___ksymtab" + suffix] - base
        crc_start = symbols["__start___kcrctab" + suffix] - base
        crc_stop = symbols["__stop___kcrctab" + suffix] - base
        assert (stop - start) % 12 == 0 and (crc_stop - crc_start) % 4 == 0
        assert (stop - start) // 12 == (crc_stop - crc_start) // 4
        assert 0 <= start <= stop <= len(image) and 0 <= crc_start <= crc_stop <= len(image)
        for index, offset in enumerate(range(start, stop, 12)):
            def string_at(field):
                address = field + struct.unpack_from("<i", image, field)[0]
                assert 0 <= address < len(image)
                return image[address:image.index(b"\0", address)].decode("ascii")

            name, namespace = string_at(offset + 4), string_at(offset + 8)
            assert symbols["__ksymtab_" + name] == base + offset
            assert name not in rows
            crc = struct.unpack_from("<I", image, crc_start + index * 4)[0]
            rows[name] = f"0x{crc:08x}\t{name}\tvmlinux\t{kind}\t{namespace}".rstrip() + "\n"
    return "".join(rows[name] for name in sorted(rows)).encode(), len(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("elf", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--release", required=True)
    args = parser.parse_args()
    image = args.image.read_bytes()
    result, count = extract(image, args.elf.read_bytes(), args.release)
    args.output.write_bytes(result)
    print(json.dumps({"release": args.release, "symbols": count,
                      "image_sha256": hashlib.sha256(image).hexdigest(),
                      "symvers_sha256": hashlib.sha256(result).hexdigest()}))


if __name__ == "__main__":
    main()

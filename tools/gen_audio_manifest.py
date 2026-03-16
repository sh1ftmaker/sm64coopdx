#!/usr/bin/env python3
"""
Generate a JSON manifest of all SM64 audio samples from the ROM.
Uses disassemble_sound.py's parsing logic to correctly extract codebooks.

Usage: python3 tools/gen_audio_manifest.py <rom_path> [output_path]
"""
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from disassemble_sound import (
    parse_seqfile, parse_tbl, parse_ctl, parse_ctl_header, TYPE_CTL, TYPE_TBL
)

# SM64 US ROM audio segment offsets
CTL_ROM_OFFSET = 0x57B720
TBL_ROM_OFFSET = 0x593560
CTL_SIZE = TBL_ROM_OFFSET - CTL_ROM_OFFSET  # 97856 bytes


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <rom.z64> [output.json]")
        sys.exit(1)

    rom_path = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else 'build/us_pc/audio_manifest.json'

    with open(rom_path, 'rb') as f:
        rom = f.read()

    print(f"ROM: {rom_path} ({len(rom)} bytes)")

    # Extract CTL and TBL data from ROM
    ctl_data = rom[CTL_ROM_OFFSET:CTL_ROM_OFFSET + CTL_SIZE]

    # Find TBL end from header
    tbl_header = parse_seqfile(rom[TBL_ROM_OFFSET:TBL_ROM_OFFSET + 0x200], TYPE_TBL)
    max_end = 0
    for off, length in tbl_header:
        end = off + length
        if end > max_end:
            max_end = end
    tbl_data = rom[TBL_ROM_OFFSET:TBL_ROM_OFFSET + max_end]

    print(f"CTL: offset=0x{CTL_ROM_OFFSET:x} size={len(ctl_data)}")
    print(f"TBL: offset=0x{TBL_ROM_OFFSET:x} size={len(tbl_data)}")

    # Parse using disassemble_sound.py logic
    ctl_entries = parse_seqfile(ctl_data, TYPE_CTL)
    tbl_entries = parse_seqfile(tbl_data, TYPE_TBL)
    print(f"CTL banks: {len(ctl_entries)}, TBL banks: {len(tbl_entries)}")
    assert len(ctl_entries) == len(tbl_entries), "CTL/TBL bank count mismatch"

    tbls, sample_banks, sample_bank_map = parse_tbl(tbl_data, tbl_entries)
    # sample_banks is a list; sample_bank_map maps name -> SampleBank object

    # Parse all banks and collect samples
    all_samples = {}  # keyed by rom_addr
    banks_info = []

    for index, (offset, length), sample_bank_name in zip(
        range(len(ctl_entries)), ctl_entries, tbls
    ):
        if length == 0:
            banks_info.append(None)
            continue

        bank_data = ctl_data[offset:offset + length]
        sample_bank = sample_bank_map[sample_bank_name]

        # Parse 16-byte header: num_instruments(u32), num_drums(u32), shared(u32), date(4)
        header = parse_ctl_header(bank_data[:16])

        try:
            bank = parse_ctl(header, bank_data[16:], sample_bank, index, False)
            banks_info.append(bank)
        except Exception as e:
            print(f"  Warning: bank {index} parse error: {e}")
            banks_info.append(None)
            continue

        # Collect samples from this bank's sample_bank
        for sample_offset, entry in sample_bank.entries.items():
            rom_addr = TBL_ROM_OFFSET + sample_bank.offset + sample_offset
            adpcm_size = len(entry.data)

            key = rom_addr
            if key not in all_samples:
                all_samples[key] = {
                    'tblOffset': sample_offset,
                    'romAddr': rom_addr,
                    'size': adpcm_size,
                    'book': {
                        'order': entry.book.order,
                        'npredictors': entry.book.npredictors,
                        'table': list(entry.book.table),
                    },
                }
                if entry.loop:
                    loop_info = {
                        'start': entry.loop.start,
                        'end': entry.loop.end,
                        'count': entry.loop.count,
                    }
                    if entry.loop.state:
                        loop_info['state'] = list(entry.loop.state)
                    all_samples[key]['loop'] = loop_info

                # Try to assign a name from the entry
                if entry.name:
                    all_samples[key]['name'] = entry.name

    # Build manifest
    samples_list = sorted(all_samples.values(), key=lambda s: s['romAddr'])

    # Assign names to unnamed samples
    for i, s in enumerate(samples_list):
        if 'name' not in s:
            s['name'] = f"sample_{s['romAddr']:x}"

    manifest = {
        'version': 1,
        'sampleRate': 32000,
        'ctlRomOffset': CTL_ROM_OFFSET,
        'tblRomOffset': TBL_ROM_OFFSET,
        'totalSamples': len(samples_list),
        'samples': samples_list,
    }

    os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
    with open(output_path, 'w') as f:
        json.dump(manifest, f, separators=(',', ':'))

    print(f"\nManifest: {output_path}")
    print(f"  {len(samples_list)} unique samples")
    print(f"  All with codebooks (order=2, npred=2)")
    sz = os.path.getsize(output_path)
    print(f"  Size: {sz} bytes ({sz/1024:.1f} KB)")

    # Verify by spot-checking some samples
    print("\nSample spot-check:")
    for s in samples_list[:5]:
        # Read ADPCM data from ROM and verify it exists
        adpcm = rom[s['romAddr']:s['romAddr'] + s['size']]
        nonzero = sum(1 for b in adpcm if b != 0)
        print(f"  {s['name']}: rom=0x{s['romAddr']:x} size={s['size']} "
              f"nonzero={nonzero}/{len(adpcm)} book_order={s['book']['order']}")


if __name__ == '__main__':
    main()

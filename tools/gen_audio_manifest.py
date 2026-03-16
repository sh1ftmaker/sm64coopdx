#!/usr/bin/env python3
"""
Generate a JSON manifest of all SM64 audio samples.
Extracts codebooks from AIFC source files and matches them with ROM offsets.
Output is used by the JS VADPCM decoder for web audio playback.

Usage: python3 tools/gen_audio_manifest.py [output_path]
"""
import json
import os
import re
import struct
import sys


def parse_aifc_codebook(path):
    """Extract VADPCM codebook from AIFC file header."""
    with open(path, 'rb') as f:
        data = f.read()
    idx = data.find(b'APPL')
    while idx >= 0:
        app_type = data[idx+8:idx+12]
        if app_type == b'stoc':
            str_len = data[idx+12]
            name = data[idx+13:idx+13+str_len]
            if name == b'VADPCMCODES':
                code_start = idx + 13 + str_len
                if (str_len + 1) % 2 != 0:
                    code_start += 1
                order = struct.unpack_from('>H', data, code_start+2)[0]
                npredictors = struct.unpack_from('>H', data, code_start+4)[0]
                table_count = 8 * order * npredictors
                table = list(struct.unpack_from(f'>{table_count}h', data, code_start+6))
                return {'order': order, 'npredictors': npredictors, 'table': table}
        idx = data.find(b'APPL', idx+1)
    return None


def parse_aifc_loop(path):
    """Extract VADPCM loop info from AIFC file header."""
    with open(path, 'rb') as f:
        data = f.read()
    idx = data.find(b'APPL')
    while idx >= 0:
        app_type = data[idx+8:idx+12]
        if app_type == b'stoc':
            str_len = data[idx+12]
            name = data[idx+13:idx+13+str_len]
            if name == b'VADPCMLOOPS':
                loop_start = idx + 13 + str_len
                if (str_len + 1) % 2 != 0:
                    loop_start += 1
                version = struct.unpack_from('>H', data, loop_start)[0]
                nloops = struct.unpack_from('>H', data, loop_start+2)[0]
                if nloops > 0:
                    start = struct.unpack_from('>I', data, loop_start+4)[0]
                    end = struct.unpack_from('>I', data, loop_start+8)[0]
                    count = struct.unpack_from('>i', data, loop_start+12)[0]
                    return {'start': start, 'end': end, 'count': count}
        idx = data.find(b'APPL', idx+1)
    return None


def parse_aifc_num_frames(path):
    """Get number of sample frames from AIFC COMM chunk."""
    with open(path, 'rb') as f:
        data = f.read()
    idx = data.find(b'COMM')
    if idx >= 0:
        return struct.unpack_from('>I', data, idx+10)[0]
    return 0


def name_to_aifc_path(name, samples_dir):
    """Map a sample name from samples_assets.c to its AIFC source file."""
    # ROM_ASSET_LOAD_SAMPLE name format: sfx_1_00_twirl_aifc
    # AIFC path format: sfx_1/00_twirl.aifc
    # Strip _aifc suffix, replace last _ before digits with /
    n = name
    if n.endswith('_aifc') or n.endswith('_aiff'):
        n = n[:-5]  # strip _aifc/_aiff

    # Try various path patterns
    patterns = [
        # sfx_1_00_twirl -> sfx_1/00_twirl
        lambda n: re.sub(r'^([a-z_]+?)_(\d)', r'\1/\2', n),
        # sfx_mario_00_mario_jump_hoo -> sfx_mario/00_mario_jump_hoo
        lambda n: re.sub(r'^(sfx_[a-z_]+?)_(\d)', r'\1/\2', n),
        # instruments_00 -> instruments/00
        lambda n: re.sub(r'^(instruments)_(\d)', r'\1/\2', n),
        # piranha_music_box_00_music_box -> piranha_music_box/00_music_box
        lambda n: re.sub(r'^([a-z_]+?)_(\d)', r'\1/\2', n),
    ]

    for pat in patterns:
        p = pat(n)
        for ext in ['.aifc', '.aiff']:
            full = os.path.join(samples_dir, p + ext)
            if os.path.exists(full):
                return full
    return None


def main():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    output_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        base_dir, 'build', 'us_pc', 'audio_manifest.json')

    sound_dir = os.path.join(base_dir, 'sound')
    samples_dir = os.path.join(sound_dir, 'samples')

    # Parse samples_assets.c for ROM offsets
    assets_path = os.path.join(sound_dir, 'samples_assets.c')
    samples = []
    with open(assets_path) as f:
        for line in f:
            m = re.match(
                r'ROM_ASSET_LOAD_SAMPLE\((\w+),\s*&gSoundDataRaw\[\w+\],\s*'
                r'(0x[0-9a-fA-F]+),\s*(\d+),\s*0x[0-9a-fA-F]+,\s*(\d+)\);',
                line.strip()
            )
            if m:
                samples.append({
                    'name': m.group(1),
                    'rom_addr': int(m.group(2), 16),
                    'size': int(m.group(3)),
                })

    # Parse samples_offsets.h for TBL offsets
    offsets_path = os.path.join(sound_dir, 'samples_offsets.h')
    tbl_offsets = {}
    with open(offsets_path) as f:
        for line in f:
            m = re.match(r'#define\s+(\w+)\s+(0x[0-9a-fA-F]+)', line.strip())
            if m:
                tbl_offsets[m.group(1)] = int(m.group(2), 16)

    # Deduplicate by ROM address and match with AIFC codebooks
    seen = {}
    manifest_samples = []
    matched = 0
    unmatched = 0

    for s in samples:
        rom_addr = s['rom_addr']
        if rom_addr in seen:
            continue
        seen[rom_addr] = True

        entry = {
            'name': s['name'],
            'romAddr': rom_addr,
            'size': s['size'],
        }

        # Get TBL offset
        offset_name = 'SAMPLE_' + s['name']
        if offset_name in tbl_offsets:
            entry['tblOffset'] = tbl_offsets[offset_name]

        # Find matching AIFC file and extract codebook
        aifc_path = name_to_aifc_path(s['name'], samples_dir)
        if aifc_path:
            book = parse_aifc_codebook(aifc_path)
            if book:
                entry['book'] = book
                matched += 1
            loop = parse_aifc_loop(aifc_path)
            if loop:
                entry['loop'] = loop
            entry['numFrames'] = parse_aifc_num_frames(aifc_path)
        else:
            unmatched += 1

        manifest_samples.append(entry)

    manifest_samples.sort(key=lambda x: x['romAddr'])

    manifest = {
        'version': 1,
        'sampleRate': 32000,
        'totalSamples': len(manifest_samples),
        'samples': manifest_samples,
    }

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w') as f:
        json.dump(manifest, f, separators=(',', ':'))

    # Also write a pretty version for debugging
    debug_path = output_path.replace('.json', '_debug.json')
    with open(debug_path, 'w') as f:
        json.dump(manifest, f, indent=2)

    print(f"Audio manifest: {output_path}")
    print(f"  {len(manifest_samples)} unique samples")
    print(f"  {matched} with codebook (from AIFC)")
    print(f"  {unmatched} without AIFC match")
    sz = os.path.getsize(output_path)
    print(f"  Manifest size: {sz} bytes ({sz/1024:.1f} KB)")


if __name__ == '__main__':
    main()

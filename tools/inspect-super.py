#!/usr/bin/env python3
"""Inspect checksummed Android LP metadata from a bounded super prefix.

Offline report only: no mappings, mounts, device writes or network access.
Format: AOSP fs_mgr/liblp/include/liblp/metadata_format.h and utility.cpp.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct


MAX_INPUT = 16 * 1024 * 1024
SUPER_BYTES = 10737418240
FORMATS = ['<36s4I', '<QIQI', '<36sIQ', '<QIIQ36sI']


def sha(data):
    return hashlib.sha256(data).digest()


def require(ok, message):
    if not ok:
        raise ValueError(message)


def name(raw):
    head, _, tail = raw.partition(b'\0')
    require(not tail.strip(b'\0'), 'Nonzero name padding')
    require(re.fullmatch(rb'[a-zA-Z0-9_]{1,36}', head), 'Invalid LP name')
    return head.decode('ascii')


def geometry(data, offset):
    require(offset + 52 <= len(data), 'Truncated geometry')
    raw = data[offset:offset + 52]
    magic, size, checksum, maximum, slots, block = struct.unpack('<II32sIII', raw)
    require(magic == 0x616c4467 and size == 52, 'Invalid geometry magic/size')
    require(sha(raw[:8] + b'\0' * 32 + raw[40:]) == checksum, 'Geometry checksum mismatch')
    require(512 <= maximum <= MAX_INPUT and maximum % 512 == 0, 'Invalid metadata slot size')
    require(1 <= slots <= 32, 'Invalid metadata slot count')
    require(512 <= block <= 65536 and block & (block - 1) == 0, 'Invalid logical block size')
    return {'metadata_max_size': maximum, 'metadata_slot_count': slots,
            'logical_block_size': block}


def metadata(data, offset, geom, expected_size):
    maximum = geom['metadata_max_size']
    raw = data[offset:offset + maximum]
    require(len(raw) == maximum, 'Truncated metadata slot')
    magic, major, minor, header_size = struct.unpack_from('<IHHI', raw)
    require(magic == 0x414c5030 and major == 10 and minor <= 2, 'Unsupported metadata format')
    require(header_size == (256 if minor == 2 else 128), 'Unsupported metadata header size')
    require(header_size <= maximum, 'Header exceeds slot')
    header = raw[:header_size]
    require(sha(header[:12] + b'\0' * 32 + header[44:]) == header[12:44], 'Header checksum mismatch')
    table_size = struct.unpack_from('<I', header, 44)[0]
    require(header_size + table_size <= maximum, 'Tables exceed slot')
    tables = raw[header_size:header_size + table_size]
    require(sha(tables) == header[48:80], 'Table checksum mismatch')
    entries, spans = [], []
    for i, fmt in enumerate(FORMATS):
        start, count, width = struct.unpack_from('<III', header, 80 + 12 * i)
        require(width == struct.calcsize(fmt), 'Unsupported table entry size')
        end = start + count * width
        require(start <= table_size and end <= table_size, 'Table bounds invalid')
        if count:
            require(all(end <= a or start >= b for a, b in spans), 'Overlapping metadata tables')
            spans.append((start, end))
        entries.append([struct.unpack_from(fmt, tables, start + n * width) for n in range(count)])
    parts, raw_extents, raw_groups, raw_devices = entries
    require(raw_devices and raw_groups, 'Missing block device/group table')
    region = 12288 + 2 * maximum * geom['metadata_slot_count']
    devices = []
    for first, align, align_offset, size, raw_name, flags in raw_devices:
        require(first * 512 < size and size % 512 == 0, 'Invalid physical device size')
        devices.append({'name': name(raw_name), 'first_logical_sector': first,
                        'alignment': align, 'alignment_offset': align_offset,
                        'bytes': size, 'flags': flags})
    require(devices[0]['name'] == 'super' and devices[0]['bytes'] == expected_size,
            'Metadata is not for the expected physical super partition')
    require(devices[0]['first_logical_sector'] * 512 >= region, 'Metadata overlaps partition data')
    require(len({d['name'] for d in devices}) == len(devices), 'Duplicate physical device names')
    groups = [{'name': name(n), 'flags': f, 'maximum_bytes': s} for n, f, s in raw_groups]
    require(len({g['name'] for g in groups}) == len(groups), 'Duplicate group names')
    extents = []
    for sectors, kind, target, source in raw_extents:
        require(sectors > 0 and kind in (0, 1), 'Invalid extent length/type')
        if kind == 0:
            require(source < len(devices), 'Invalid physical device index')
            dev = devices[source]
            require(target >= dev['first_logical_sector'] and
                    (target + sectors) * 512 <= dev['bytes'], 'Extent outside physical device')
        else:
            require(target == 0 and source == 0, 'Invalid zero extent')
        extents.append({'sectors': sectors, 'type': 'linear' if kind == 0 else 'zero',
                        'physical_sector': target, 'block_device_index': source})
    partitions, used, group_sizes = [], set(), [0] * len(groups)
    for raw_name, attributes, start, count, group in parts:
        part_name = name(raw_name)
        require(part_name not in used, 'Duplicate partition name')
        used.add(part_name)
        require(start + count <= len(extents) and group < len(groups), 'Invalid partition extent/group index')
        require(attributes & ~(3 if minor == 0 else 15) == 0, 'Unknown partition attributes')
        selected = extents[start:start + count]
        size = sum(e['sectors'] * 512 for e in selected)
        group_sizes[group] += size
        partitions.append({'name': part_name, 'attributes': attributes,
                           'readonly_attribute': bool(attributes & 1),
                           'slot_suffixed_attribute': bool(attributes & 2),
                           'disabled_attribute': bool(attributes & 8),
                           'group': groups[group]['name'], 'bytes': size, 'extents': selected})
    for group, size in zip(groups, group_sizes):
        require(not group['maximum_bytes'] or size <= group['maximum_bytes'], 'Partition group capacity exceeded')
    return {'version': '{}.{}'.format(major, minor),
            'header_flags': struct.unpack_from('<I', header, 128)[0] if header_size == 256 else 0,
            'sha256': sha(raw[:header_size + table_size]).hex(),
            'block_devices': devices, 'groups': groups, 'partitions': partitions}


def inspect(data, slot='b', super_bytes=SUPER_BYTES):
    require(12288 <= len(data) <= MAX_INPUT, 'Expected a bounded raw super prefix (12 KiB to 16 MiB)')
    require(slot in ('a', 'b'), 'Select slot a or b explicitly')
    geoms, geometry_errors = {}, {}
    for label, offset in [('primary', 4096), ('backup', 8192)]:
        try:
            geoms[label] = geometry(data, offset)
        except ValueError as exc:
            geometry_errors[label] = str(exc)
    require(geoms, 'No checksummed geometry: ' + str(geometry_errors))
    require(len(geoms) != 2 or geoms['primary'] == geoms['backup'], 'Conflicting valid geometries')
    geom = next(iter(geoms.values()))
    index = 0 if slot == 'a' else 1
    require(index < geom['metadata_slot_count'], 'Selected metadata slot does not exist')
    maximum = geom['metadata_max_size']
    region = 12288 + 2 * maximum * geom['metadata_slot_count']
    require(region <= len(data), 'Prefix too short; need at least {} bytes'.format(region))
    copies, errors = {}, {}
    for label, base in [('primary', 12288), ('backup', 12288 + maximum * geom['metadata_slot_count'])]:
        try:
            copies[label] = metadata(data, base + maximum * index, geom, super_bytes)
        except ValueError as exc:
            errors[label] = str(exc)
    require(copies, 'No valid metadata copy: ' + str(errors))
    chosen = 'primary' if 'primary' in copies else 'backup'
    meta = copies[chosen]
    candidates = []
    for part in meta['partitions']:
        effective = part['name'] + '_' + slot if part['slot_suffixed_attribute'] else part['name']
        part['effective_name'] = effective
        if effective.endswith('_' + slot) and not part['disabled_attribute']:
            candidates.append(effective)
    return {'schema': 1, 'kind': 'offline-super-metadata-inspection',
            'slot': slot, 'metadata_slot_index': index, 'source_prefix_bytes': len(data),
            'source_prefix_sha256': sha(data).hex(), 'geometry': geom,
            'geometry_copy_errors': geometry_errors, 'metadata_region_bytes': region,
            'metadata_region_sha256': sha(data[:region]).hex(), 'chosen_copy': chosen,
            'metadata_copy_errors': errors,
            'valid_metadata_copies_equal': (copies['primary']['sha256'] == copies['backup']['sha256'])
                if len(copies) == 2 else None,
            'metadata': meta, 'active_partition_candidates': candidates,
            'checksums_verified': True, 'device_accessed': False,
            'mappings_created': False, 'mounts_performed': False, 'flashable_release': False,
            'scope': 'LP metadata checksums, layout and range validation only; no AVB, filesystem or runtime validation.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('prefix', type=Path)
    parser.add_argument('--slot', choices=['a', 'b'], required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--rootfs-layout', type=Path,
                        help='Attach a bounded, read-only rootfs layout text capture')
    args = parser.parse_args()
    try:
        require(args.prefix.is_file() and args.prefix.stat().st_size <= MAX_INPUT,
                'Input must be a regular prefix file, at most 16 MiB')
        require(not args.output.exists(), 'Output exists; choose a new report path')
        result = inspect(args.prefix.read_bytes(), args.slot)
        if args.rootfs_layout is not None:
            require(args.rootfs_layout.is_file() and args.rootfs_layout.stat().st_size <= 131072,
                    'Rootfs layout capture must be a regular file, at most 128 KiB')
            result['rootfs_layout'] = args.rootfs_layout.read_text(errors='replace')
        with args.output.open('x') as stream:
            stream.write(json.dumps(result, indent=2) + '\n')
        print('Checksummed metadata for slot {}: {}'.format(args.slot, ', '.join(result['active_partition_candidates'])))
        print('Report: {}'.format(args.output))
    except (OSError, ValueError, struct.error) as exc:
        parser.exit(1, 'Super inspection failed: {}\n'.format(exc))


if __name__ == '__main__':
    main()

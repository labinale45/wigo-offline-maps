"""Prepare Wigo walking packages from licensed OSM extracts, without a routing API.

Usage: python tools/build_walking_maps.py nepal-latest.osm.pbf --country NP --out maps/nepal
Install optional `osmium` for .pbf input; .osm XML uses the Python standard library.
Input must be a country extract for NP or a US/state extract for US. Map packages
retain OSM IDs, access tags and attribution. Publish only where distribution is
permitted. This command prepares files; it does not upload or buy any service.
"""
import argparse
import hashlib
import json
import math
import re
from pathlib import Path
import tempfile
import xml.etree.ElementTree as ET

HIGHWAYS = {'footway', 'path', 'pedestrian', 'living_street', 'residential',
            'service', 'steps', 'track', 'unclassified', 'tertiary', 'secondary'}
TAGS = {'highway', 'name', 'foot', 'access', 'sidewalk', 'oneway:foot', 'construction', 'area'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('--country', choices=['NP', 'US'], required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--name', default='Walking area')
    parser.add_argument('--cell-degrees', type=float, default=0.025)
    parser.add_argument('--base-url', default='', help='HTTPS URL of the output directory, if publishing')
    parser.add_argument('--bundle', action='store_true', help='Combine neighbouring grid cells into bounded download packages')
    args = parser.parse_args()
    if not 0.005 <= args.cell_degrees <= 0.1:
        parser.error('cell-degrees must be 0.005–0.1')
    if args.base_url and not args.base_url.startswith('https://'):
        parser.error('base-url must use HTTPS')
    args.out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='wigo-map-', dir=args.out) as workspace:
        temporary = Path(workspace)

        def collect(refs, coords, tags, way_id):
            if tags.get('highway') not in HIGHWAYS or len(refs) < 2:
                return
            if tags.get('foot') in {'no','private'} or (tags.get('access') in {'no','private'} and tags.get('foot') not in {'yes','designated','permissive'}) or 'construction' in tags or tags.get('area') == 'yes':
                return
            if tags.get('highway') in {'secondary','tertiary'} and tags.get('sidewalk') not in {'yes','both','left','right','separate'} and tags.get('foot') not in {'yes','designated'}:
                return
            if any(p is None for p in coords):
                return
            # Put the complete way in each cell it passes through, retaining
            # boundary junctions. No coordinate-based road intersections.
            cells = {(math.floor(lat / args.cell_degrees), math.floor(lng / args.cell_degrees))
                     for lat, lng in coords}
            nodes = [[ref, coord[0], coord[1]] for ref, coord in zip(refs, coords)]
            row = {'nodes': nodes, 'way': {'id':way_id, 'nodes': refs, 'tags': {k: v for k, v in tags.items() if k in TAGS}}}
            encoded = json.dumps(row, separators=(',', ':')) + '\n'
            for lat, lng in cells:
                with (temporary / f'{lat}_{lng}.jsonl').open('a', encoding='utf-8') as stream:
                    stream.write(encoded)

        if args.input.suffix == '.pbf':
            try:
                import osmium
            except ImportError as exc:
                raise SystemExit('For PBF extracts, install the free osmium Python package first.') from exc

            class Reader(osmium.SimpleHandler):
                def way(self, way):
                    if way.tags.get('highway') not in HIGHWAYS:
                        return
                    refs, coords = [], []
                    for node in way.nodes:
                        refs.append(node.ref)
                        coords.append((node.lat, node.lon) if node.location.valid() else None)
                    collect(refs, coords, dict(way.tags), way.id)

            Reader().apply_file(str(args.input), locations=True,
                                idx=f'sparse_file_array,{temporary / "locations.idx"}')
        else:
            # XML mode is intended for small extracts; PBF mode uses disk-backed
            # location indexing for national files.
            nodes = {}
            for _, item in ET.iterparse(args.input, events=['end']):
                if item.tag == 'node':
                    nodes[int(item.attrib['id'])] = (float(item.attrib['lat']), float(item.attrib['lon']))
                    item.clear()
                elif item.tag == 'way':
                    refs = [int(child.attrib['ref']) for child in item if child.tag == 'nd']
                    tags = {child.attrib['k']: child.attrib['v'] for child in item if child.tag == 'tag'}
                    collect(refs, [nodes.get(ref) for ref in refs], tags, int(item.attrib['id']))
                    item.clear()

        areas = []
        slug = re.sub(r'[^a-z0-9]+','-',args.name.lower()).strip('-')
        bundled_nodes, bundled_ways, bundled_cells = {}, {}, []
        package_index = 0

        def emit(nodes, ways, identity):
            nonlocal package_index
            if not nodes or not ways:
                return
            package_index += 1
            name = f'{args.name} · Area {package_index}' if args.bundle else f'{args.name} · {identity.replace("_", "/")}'
            identity = f'{slug}-{identity}'
            latitude = [node[1] for node in nodes.values()]
            longitude = [node[2] for node in nodes.values()]
            bounds = [min(latitude), min(longitude), max(latitude), max(longitude)]
            pack = {'format':'wigo-walk-v1','id':identity,'country':args.country,'name':name,
                    'attribution':'© OpenStreetMap contributors','license':'ODbL-1.0','source':args.input.name,
                    'bounds':bounds,'nodes':list(nodes.values()),'ways':list(ways.values())}
            encoded = json.dumps(pack,separators=(',',':'),ensure_ascii=False).encode('utf-8')
            if len(encoded)>15*1024*1024 or len(nodes)>100000 or len(ways)>50000:
                raise SystemExit(f'{identity} is too large. Use smaller --cell-degrees.')
            destination=args.out/f'{args.country}-{identity}.json'
            destination.write_bytes(encoded)
            areas.append({'country':args.country,'id':identity,'name':name,'bounds':bounds,
                          'bytes':len(encoded),'sha256':hashlib.sha256(encoded).hexdigest(),
                          'url':f'{args.base_url.rstrip("/")}/{destination.name}' if args.base_url else ''})

        for cell in sorted(temporary.glob('*.jsonl')):
            nodes, ways = {}, {}
            for line in cell.read_text(encoding='utf-8').splitlines():
                row = json.loads(line)
                nodes.update({node[0]: node for node in row['nodes']})
                ways[row['way']['id']] = row['way']
            if not args.bundle:
                emit(nodes,ways,cell.stem)
                continue
            # Conservative limits bound Flutter parsing and route-render memory.
            if bundled_nodes and (len(set(bundled_nodes)|set(nodes))>22000 or len(set(bundled_ways)|set(ways))>12000):
                emit(bundled_nodes,bundled_ways,f'area-{package_index+1:04d}')
                bundled_nodes,bundled_ways,bundled_cells={},{},[]
            bundled_nodes.update(nodes); bundled_ways.update(ways); bundled_cells.append(cell.stem)
        if args.bundle:
            emit(bundled_nodes,bundled_ways,f'area-{package_index+1:04d}')
        (args.out / 'catalogue.json').write_text(json.dumps({'format': 'wigo-catalogue-v1', 'areas': areas},
                                                           ensure_ascii=False), encoding='utf-8')
        print(f'Prepared {len(areas)} offline walking areas in {args.out.resolve()}')


if __name__ == '__main__':
    main()

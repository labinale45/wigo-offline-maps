"""Prepare a validated regional release from a public Geofabrik extract."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import urllib.request
import urllib.error
import urllib.parse


def gh(*args):
    subprocess.run(['gh', *map(str, args)], check=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--region', required=True)
    parser.add_argument('--version', required=True)
    parser.add_argument('--repo', required=True)
    parser.add_argument('--catalogue-only', action='store_true')
    args = parser.parse_args()
    regions = json.loads(Path('regions.json').read_text())
    if args.region not in regions and args.region != 'all':
        parser.error('Unknown region')
    if not re.fullmatch(r'[A-Za-z0-9._-]{1,32}', args.version):
        parser.error('Invalid version')
    if not re.fullmatch(r'[A-Za-z0-9._-]+/[A-Za-z0-9._-]+', args.repo):
        parser.error('Invalid repository')
    if args.catalogue_only:
        entries = []
        for slug, region in regions.items():
            url = f'https://github.com/{args.repo}/releases/download/maps-{slug}-{args.version}-p001/catalogue.json'
            try:
                with urllib.request.urlopen(url, timeout=30) as response:
                    data = json.load(response)
            except Exception:
                # Only advertise datasets that actually finished publishing.
                continue
            if not data.get('areas'):
                continue
            entries.append({'country': region['country'], 'name': region['name'], 'id': slug,
                            'url': url, 'areas': len(data['areas']), 'bytes': sum(a['bytes'] for a in data['areas'])})
        if not entries:
            raise SystemExit('No published datasets were found')
        Path('catalogue.json').write_text(json.dumps({'format':'wigo-catalogue-v1','version':args.version,
                                                     'regions':entries},separators=(',',':')))
        tag = f'catalogue-{args.version}-{os.environ.get("GITHUB_RUN_ID","manual")}-{os.environ.get("GITHUB_RUN_ATTEMPT","1")}'
        gh('release', 'create', tag, '--repo', args.repo, '--title', 'Wigo walking map catalogue',
           '--notes', 'OpenStreetMap-derived walking data. © OpenStreetMap contributors, ODbL 1.0. Only successfully prepared regions are listed.', '--draft')
        gh('release','upload',tag,'catalogue.json','--repo',args.repo)
        gh('release','edit',tag,'--repo',args.repo,'--draft=false','--latest')
        print(f'Catalogue includes {len(entries)} completed regions')
        return
    region = regions[args.region]
    first_tag = f'maps-{args.region}-{args.version}-p001'
    try:
        with urllib.request.urlopen(f'https://github.com/{args.repo}/releases/download/{first_tag}/catalogue.json',timeout=20) as response:
            if json.load(response).get('areas'):
                print(f"Keeping previously published immutable data for {region['name']}")
                return
    except Exception:
        pass
    work = Path('work').resolve()
    work.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='region-', dir=work) as temporary:
        folder = Path(temporary)
        source = region.get('source') or f'https://download.geofabrik.de/north-america/us/{args.region}-latest.osm.pbf'
        extract = folder / 'extract.osm.pbf'
        request = urllib.request.Request(source, headers={'User-Agent':'WigoOfflineMaps/1.0 (+https://github.com/'+args.repo+')'})
        try:
            response = urllib.request.urlopen(request, timeout=120)
        except urllib.error.HTTPError:
            # Some latest aliases intermittently loop between a filename and
            # a trailing slash. Resolve a real dated extract from its official
            # regional page rather than guessing a date or losing coverage.
            page = source.replace('-latest.osm.pbf', '.html')
            with urllib.request.urlopen(page, timeout=60) as listing:
                html = listing.read().decode('utf-8')
            candidates = re.findall(r'href=[\"\']([^\"\']*-\d{6}\.osm\.pbf)[\"\']', html)
            if not candidates:
                raise SystemExit('No dated extract available on the official region page')
            source = urllib.parse.urljoin(page, max(candidates, key=lambda url: re.search(r'-(\d{6})\.osm\.pbf', url).group(1)))
            response = urllib.request.urlopen(urllib.request.Request(source, headers={'User-Agent': 'WigoOfflineMaps/1.0'}), timeout=120)
        with response, extract.open('wb') as output:
            while block := response.read(1024*1024):
                output.write(block)
        output = folder / 'maps'
        subprocess.run(['python','build_walking_maps.py',str(extract),'--country',region['country'],
                        '--name',region['name'],'--out',str(output),'--bundle'],check=True)
        catalogue = json.loads((output/'catalogue.json').read_text())
        areas = catalogue['areas']
        if not areas:
            raise SystemExit('No usable walking areas were generated')
        files = []
        for index, area in enumerate(areas):
            path = output / f"{region['country']}-{area['id']}.json"
            encoded = path.read_bytes()
            if len(encoded) != area['bytes'] or hashlib.sha256(encoded).hexdigest() != area['sha256']:
                raise SystemExit('Package integrity validation failed')
            package = json.loads(encoded)
            if not package['nodes'] or not package['ways']:
                raise SystemExit('Empty walking graph')
            tag = f'maps-{args.region}-{args.version}-p{index//990+1:03d}'
            area['url'] = f'https://github.com/{args.repo}/releases/download/{tag}/{path.name}'
            files.append(path)
        (output/'catalogue.json').write_text(json.dumps(catalogue,separators=(',',':')))
        tags = []
        for offset in range(0,len(files),990):
            tag=f'maps-{args.region}-{args.version}-p{offset//990+1:03d}'
            existing=subprocess.run(['gh','release','view',tag,'--repo',args.repo],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            if existing.returncode:
                gh('release','create',tag,'--repo',args.repo,'--title',f"{region['name']} walking maps · {args.version}",
                   '--notes',f'© OpenStreetMap contributors. ODbL 1.0. Source: {source}. Walking graphs are not a guarantee of safety or current access. https://www.openstreetmap.org/copyright','--draft')
            batch = files[offset:offset+990]
            if offset==0:
                batch.append(output/'catalogue.json')
            for chunk in range(0,len(batch),30):
                gh('release','upload',tag,*batch[chunk:chunk+30],'--repo',args.repo,'--clobber')
            tags.append(tag)
        # Publish the catalogue-containing part last, after every referenced
        # package has uploaded and become available.
        for tag in reversed(tags):
            gh('release','edit',tag,'--repo',args.repo,'--draft=false','--latest=false')
        print(f"Published {len(areas)} walking packages for {region['name']}")


if __name__ == '__main__':
    main()

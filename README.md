# Wigo offline walking data

Downloadable OpenStreetMap-derived walking graphs for Nepal and all 50 US states plus Washington, DC. Data is prepared from Geofabrik country/state extracts. Releases contain map data, catalogue metadata and attribution.

The dataset includes mapped pedestrian paths and permitted local roads. It excludes driving-only highways and explicitly restricted/private foot access. Paths and conditions may be incomplete or outdated; a generated route is not a safety guarantee.

## Licences

Map data: © OpenStreetMap contributors, licensed under the Open Database Licence (ODbL) 1.0: https://www.openstreetmap.org/copyright and https://opendatacommons.org/licenses/odbl/1-0/.

Generated code in this distribution repository: MIT. This repository contains no personal tracking records, authentication credentials, or private Wigo application source.

## Building and distributing

The manual workflow uses standard public GitHub Actions runners and GitHub Releases. It does not enable a paid plan, paid runner, Actions artifact storage, or routing API. Regional extracts are downloaded sequentially per job, with at most two jobs in parallel.

Each release is immutable to app clients by a versioned URL. Catalogues contain file sizes, geographic bounds and SHA-256 integrity hashes. Clients download selected areas; downloading the whole US requires substantial device storage. Country-wide availability does not mean an entire country is bundled in the app.

Source extract service: https://download.geofabrik.de/.

No availability guarantee is provided by the data source or distribution platform. Provider terms and quotas can change.

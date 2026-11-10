# Tasmania Sentinel-1 backscatter change, April vs October 2026 (DEA NRB)

Statewide radar change map from Geoscience Australia's Sentinel-1 Normalised Radar Backscatter
(NRB, Collection 1, IW VV+VH gamma0), built to run on Pawsey Setonix.

## Data

- `s3://dea-public-data-dev/baseline/ga_s1_nrb_iw_vv_vh_1/<burst>/YYYY/MM/DD/<time>/` per-burst
  COGs (UTM, 20 m, linear gamma0, internal overviews x4 x16 x64 x125) plus a STAC item JSON
  sidecar. Public, unsigned, read over HTTPS; no STAC API needed.
- Over Tasmania (checked 2026-11-10): 249 bursts on tracks 045 and 147 (descending) and 126
  (ascending). All have 30 Sep and 12 Oct 2026; 111 mainland bursts also have 20 Apr 2026.
  Flinders Island has no April date. `data/index_2026-11-10.json` is that burst list.
- DEA's full archive is due early 2027. Rebuild the index (`index` step) and widen the date
  pairs in `burst_change()` to use it.

## Method

For each burst, both dates come from the same burst, so the grids are identical (no
co-registration). Layover/shadow pixels (NRB mask != 0) are dropped, a 5 x 5 boxcar is applied in
linear power to cut speckle, and change = 10 log10(late / early) in dB. Two pairs:

| Band | Pair | Use |
|---|---|---|
| `vh_apr_oct`, `vv_apr_oct` | 20 Apr -> 12 Oct 2026 | Seasonal change plus any events |
| `vh_sep_oct`, `vv_sep_oct` | 30 Sep -> 12 Oct 2026 | 12-day reference: noise floor and weather |

Bursts are averaged onto one EPSG:28355 (MGA zone 55) grid, all tracks together and by orbit
direction, and open water is masked with Natural Earth 10 m land minus lakes (wind on water moves
backscatter by 5-20 dB between dates and swamps the land).

## Results: native 20 m (Pawsey Setonix)

Full state at native 20 m (`burst --level 0 --boxcar 5`, `mosaic --res 20 --by-orbit`), Setonix
job 52711341, 10 Nov 2026, code at commit 7885158 (about 30 min on one 32-core node).

![20 m change map](figures/tas_s1_change_20m.png)

Histogram: [`figures/tas_s1_change_hist_20m.png`](figures/tas_s1_change_hist_20m.png).
Land-only stats (`figures/tas_s1_change_stats_20m.json`):

| Band | Median (dB) | SD (dB) | > +3 dB | < -3 dB |
|---|---|---|---|---|
| VH Apr -> Oct | +0.29 | 1.14 | 3.7% | 0.22% |
| VV Apr -> Oct | +0.41 | 1.50 | 4.4% | 0.19% |
| VH Sep -> Oct | -0.36 | 0.85 | 0.11% | 0.32% |
| VV Sep -> Oct | -0.30 | 0.93 | 0.97% | 0.38% |

The figure and these stats come from the mosaic averaged down 7x (about 140 m; `figure --max-px
4000`), not every 20 m pixel. Read the GeoTIFFs below for full-resolution numbers.

What it shows:

- **April -> October:** the Midlands, north-east and the eastern Central Plateau brighten by 2-4
  dB by spring (wetter soils and pasture growth after a dry autumn). The wet west and south-west
  barely change. That seasonal signal is much larger than any single event, so an event map
  needs a same-season baseline (the full archive will provide it).
- **12-day reference:** the noise floor is about 0.9 dB SD, but whole passes shift by about 1 dB
  with straight edges along track boundaries (the north-west on one pass): weather at overpass
  time, such as wet canopy or rain. Per-pass normalisation (subtract each burst's median) would
  remove most of it.
- **Artefacts (still present at 20 m):** small lakes missing from Natural Earth show in VV (Central
  Plateau); a striped strip on the north-west coast (x 300-330 km, y 5420-5440 km in MGA55) comes
  from near-coast iw3 bursts. Neither matters for land interpretation.

### Outputs (public, Pawsey Acacia)

Under <https://projects.pawsey.org.au/aad-derived/s1/tasmania-s1-change/>, readable directly
with no credentials:

| File | Size | Contents |
|---|---|---|
| `tas_s1_change_all_20m.tif` | 3.3 GB | All tracks averaged |
| `tas_s1_change_ascending_20m.tif` | 2.9 GB | Ascending passes only (track 126) |
| `tas_s1_change_descending_20m.tif` | 2.8 GB | Descending passes only (tracks 045, 147) |
| `tas_s1_change_20m.png`, `tas_s1_change_hist_20m.png`, `tas_s1_change_stats_20m.json` | | As in `figures/` |

Each GeoTIFF is a Float32 COG on a 21098 x 24690 grid at 20 m in EPSG:28355 (DEFLATE, internal
overviews) with 5 bands: `vh_apr_oct_db`, `vv_apr_oct_db`, `vh_sep_oct_db`, `vv_sep_oct_db`
(dB, NaN = water, no data or layover/shadow) and `n_bursts_vh_apr_oct` (bursts averaged per
pixel). The run itself wrote plain tiled GeoTIFFs; these were converted to COG with
`gdal raster convert --of COG` before upload, and the `mosaic` step now writes COGs directly.

```bash
gdal raster info /vsicurl/https://projects.pawsey.org.au/aad-derived/s1/tasmania-s1-change/tas_s1_change_all_20m.tif
```

## Quicklook (80 m inputs, 100 m mosaic, run in a cloud sandbox)

`all --level 1 --boxcar 3 --res 100`, the first test before Setonix:
[map](figures/quicklook_100m.png), [histogram](figures/quicklook_hist_100m.png),
[stats](figures/quicklook_stats_100m.json). It shows the same patterns as the 20 m run, with
narrower tails from the extra smoothing (and a 2x average in the figure step, so about 200 m):

| Band | Median (dB) | SD (dB) | > +3 dB | < -3 dB |
|---|---|---|---|---|
| VH Apr -> Oct | +0.26 | 0.94 | 2.6% | 0.09% |
| VV Apr -> Oct | +0.38 | 1.35 | 3.4% | 0.09% |
| VH Sep -> Oct | -0.32 | 0.74 | 0.07% | 0.14% |
| VV Sep -> Oct | -0.28 | 0.83 | 0.88% | 0.25% |

## Run it

```bash
python tas_s1_change.py index                          # ~2 min, writes work/index.json
python tas_s1_change.py burst --level 0 --boxcar 5     # native 20 m (~30 s per burst per thread)
python tas_s1_change.py mosaic --res 20 --by-orbit
python tas_s1_change.py figure --res 20
python tas_s1_change.py all --level 1 --boxcar 3 --res 100   # quick look, as above
```

`TAS_S1_WORK` and `TAS_S1_OUT` set the working and output folders (default `work/`, `out/`).
`mosaic --by-orbit` writes `out/tas_s1_change_{all,ascending,descending}_<res>m.tif`; `figure`
uses the `all` mosaic. At 20 m each burst output is ~30-65 MB, ~16 GB for the state; the 20 m
mosaic needs ~40 GB RAM.

## Setonix setup (once, on a login node)

```bash
module load python/3.11.6     # Setonix needs an explicit version; see: module avail python
python -m venv $MYSOFTWARE/venvs/tas-s1
source $MYSOFTWARE/venvs/tas-s1/bin/activate
pip install -r requirements.txt
```

Then, from inside the repo folder, `sbatch --account=<project> setonix.slurm` (one node, 32 threads, under an hour). For the
full archive or all of Australia use `setonix_array.slurm`, one burst per array task, followed by
a dependent mosaic job (commands are in the file header). Outputs go to
`$MYSCRATCH/tas-s1-change/`. If you used a different Python module or venv path, set
`PYTHON_MODULE` and/or `TAS_S1_VENV` before `sbatch` (Slurm passes the environment through).

## Publish to Acacia

From Setonix, after a run (`pawsey1197` is the rclone remote for the `aad-derived` bucket, which is
public-read). Use `copy`, never `sync`: `sync` deletes anything at the destination that is not in
the source.

```bash
SRC=$MYSCRATCH/tas-s1-change/out
DST=pawsey1197:aad-derived/s1/tasmania-s1-change
rclone copy --dry-run $SRC $DST                     # what would be sent
rclone copy -P --transfers 4 --s3-chunk-size 64M --s3-upload-concurrency 4 $SRC $DST
rclone check $SRC $DST                              # expect 0 differences
```

## Next steps

- Per-pass median normalisation, then a VH-drop map for forest clearing (plantation harvest in
  the north-east is the likely visible signal) and landslide scars.
- Same-season baselines once the 2027 archive lands, and a time-series step (per-pixel z-score
  against the stack) to flag events such as landslides or fire scars under cloud.
- Check flagged areas against DEA Sentinel-2 ARD for the same dates.

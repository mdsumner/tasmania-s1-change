#!/usr/bin/env python3
"""Tasmania Sentinel-1 backscatter change from DEA NRB (April vs October 2026).

Data: Geoscience Australia / DEA Sentinel-1 Normalised Radar Backscatter (NRB) Collection 1,
IW VV+VH gamma0 (linear), per-burst COGs in the public dev bucket
s3://dea-public-data-dev/baseline/ga_s1_nrb_iw_vv_vh_1/ (unsigned, read over HTTPS).
The full Australian archive is expected from DEA in early 2027; this script only needs the
burst index rebuilt to use it.

Steps (each is a subcommand, so the per-burst step can be a Slurm array task):

  index   list bursts over Tasmania and their STAC item sidecars -> work/index.json
  burst   for one burst (or --task-id N from the index), compute dB change for
          April->October (seasonal + events) and September->October (12-day noise reference)
          -> work/bursts/<burst>.tif  (4 bands: vh_apr_oct, vv_apr_oct, vh_sep_oct, vv_sep_oct)
  mosaic  average all burst outputs onto one EPSG:28355 (MGA zone 55) grid -> out/*.tif
  figure  maps + histograms -> out/*.png
  all     run every step serially (fine at --level 2 or coarser on a laptop)

Change is 10*log10(late/early) in dB, after a boxcar filter in linear power to cut speckle.
Pixels flagged by the NRB layover/shadow mask (mask != 0) are dropped. Both dates of a pair
come from the same burst, so the geometry is identical and no co-registration is needed.

Usage
  python tas_s1_change.py index
  python tas_s1_change.py burst --task-id 0 --level 0
  python tas_s1_change.py mosaic --res 40
  python tas_s1_change.py figure
  python tas_s1_change.py all --level 2 --res 160      # quick look
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

for _k, _v in {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif",
    "GDAL_HTTP_MULTIRANGE": "YES",
    "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
    "GDAL_HTTP_MAX_RETRY": "5",
    "GDAL_HTTP_RETRY_DELAY": "2",
    "VSI_CACHE": "TRUE",
}.items():
    os.environ.setdefault(_k, _v)

import rasterio  # noqa: E402
from rasterio.warp import Resampling, reproject, transform_bounds  # noqa: E402

BUCKET = "dea-public-data-dev"
BASE = f"https://{BUCKET}.s3.ap-southeast-2.amazonaws.com"
PRODUCT = "baseline/ga_s1_nrb_iw_vv_vh_1"
# Tasmania incl. King and Flinders Islands, lon/lat
TAS_BBOX = (143.7, -43.8, 148.6, -39.4)
DST_CRS = "EPSG:28355"
WORK = Path(os.environ.get("TAS_S1_WORK", "work"))
OUT = Path(os.environ.get("TAS_S1_OUT", "out"))
BANDS = ["vh_apr_oct", "vv_apr_oct", "vh_sep_oct", "vv_sep_oct"]


# ---------------------------------------------------------------- S3 listing helpers
def s3_list(prefix: str, delimiter: str | None = None) -> tuple[list[str], list[str]]:
    keys, prefixes, token = [], [], None
    while True:
        q = {"list-type": "2", "prefix": prefix, "max-keys": "1000"}
        if delimiter:
            q["delimiter"] = delimiter
        if token:
            q["continuation-token"] = token
        x = urllib.request.urlopen(f"{BASE}/?{urllib.parse.urlencode(q)}", timeout=60).read().decode()
        keys += re.findall(r"<Key>([^<]*)</Key>", x)
        prefixes += [p for p in re.findall(r"<Prefix>([^<]*)</Prefix>", x) if p != prefix]
        m = re.search(r"<NextContinuationToken>([^<]*)<", x)
        if not m:
            return keys, prefixes
        token = m.group(1)


def get_json(key: str) -> dict:
    return json.load(urllib.request.urlopen(f"{BASE}/{key}", timeout=60))


def intersects(a, b) -> bool:
    return a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]


def burst_items(burst: str) -> list[dict]:
    keys, _ = s3_list(f"{PRODUCT}/{burst}/")
    out = []
    for k in sorted(k for k in keys if k.endswith("_stac-item.json")):
        d = get_json(k)
        p = d["properties"]
        folder = k.rsplit("/", 1)[0]
        hrefs = {}
        for a in ("vv_gamma0", "vh_gamma0", "oa_layover_shadow_mask"):
            h = d["assets"][a]["href"]
            hrefs[a] = h if h.startswith("http") else f"{BASE}/{folder}/{h}"
        out.append(dict(burst=burst, datetime=p["datetime"], bbox=d["bbox"], crs=p.get("proj:code"),
                        orbit=p.get("sat:orbit_state"), platform=p.get("platform"), assets=hrefs))
    return out


# ---------------------------------------------------------------- index
def cmd_index(args) -> None:
    """Find bursts over Tasmania. Burst ids run in order along each track, so sample every
    20th IW2 burst to find the right stretch of each track, then read every burst near it."""
    WORK.mkdir(parents=True, exist_ok=True)
    _, prefixes = s3_list(f"{PRODUCT}/", "/")
    bursts = sorted(p.rstrip("/").split("/")[-1] for p in prefixes)
    print(f"{len(bursts)} bursts in {PRODUCT}")
    have = set(bursts)
    sample = [b for b in bursts if b.endswith("iw2")][:: args.step]

    def first_bbox(b):
        keys, _ = s3_list(f"{PRODUCT}/{b}/")
        ks = [k for k in keys if k.endswith("_stac-item.json")]
        return b, (get_json(ks[0])["bbox"] if ks else None)

    pad = (-2.0, -2.0, 2.0, 2.0)
    wide = tuple(t + p for t, p in zip(TAS_BBOX, pad))
    near = set()
    with ThreadPoolExecutor(args.threads) as ex:
        for b, bb in ex.map(first_bbox, sample):
            if bb and intersects(bb, wide):
                track, num, _ = b.split("_")
                for k in range(int(num) - args.step - 5, int(num) + args.step + 6):
                    for sw in (1, 2, 3):
                        cand = f"{track}_{k:06d}_iw{sw}"
                        if cand in have:
                            near.add(cand)
    print(f"{len(near)} candidate bursts near Tasmania")
    with ThreadPoolExecutor(args.threads) as ex:
        items = [i for lst in ex.map(burst_items, sorted(near)) for i in lst]
    items = [i for i in items if intersects(i["bbox"], TAS_BBOX)]
    by = {}
    for i in items:
        by.setdefault(i["burst"], []).append(i)
    index = [{"burst": b, "items": sorted(v, key=lambda i: i["datetime"])} for b, v in sorted(by.items())]
    (WORK / "index.json").write_text(json.dumps(index, indent=1))
    print(f"{len(index)} bursts over Tasmania -> {WORK / 'index.json'}")


# ---------------------------------------------------------------- per-burst change
def boxcar(a: np.ndarray, k: int) -> np.ndarray:
    """Mean filter of size k x k that ignores NaN (needs at least half the window valid)."""
    if k <= 1:
        return a
    v = np.isfinite(a)
    z = np.where(v, a, 0.0).astype("float64")
    pad = k // 2

    def csum(x):
        x = np.pad(x, pad, mode="constant")
        c = x.cumsum(0).cumsum(1)
        c = np.pad(c, ((1, 0), (1, 0)))
        return c[k:, k:] - c[:-k, k:] - c[k:, :-k] + c[:-k, :-k]

    s, n = csum(z), csum(v.astype("float64"))
    out = np.where(n >= (k * k) / 2, s / np.maximum(n, 1), np.nan)
    return out.astype("float32")


def read_level(url: str, level: int, masked_nodata=True):
    with rasterio.open(f"/vsicurl/{url}", OVERVIEW_LEVEL=level - 1) if level > 0 else rasterio.open(f"/vsicurl/{url}") as d:
        a = d.read(1).astype("float32")
        if d.nodata is not None and not (isinstance(d.nodata, float) and math.isnan(d.nodata)):
            a[a == d.nodata] = np.nan
        return a, d.profile, d.transform, d.crs


def pick(items, month_prefix):
    m = [i for i in items if i["datetime"].startswith(month_prefix)]
    return m[-1] if m else None


def burst_change(entry: dict, level: int, k: int) -> Path | None:
    items = entry["items"]
    late = pick(items, "2026-10")
    apr = pick(items, "2026-04")
    sep = pick(items, "2026-09")
    if late is None:
        print(f"{entry['burst']}: no October 2026 date, skipped")
        return None
    out = WORK / "bursts" / f"{entry['burst']}.tif"
    out.parent.mkdir(parents=True, exist_ok=True)

    def load(item, pol):
        a, prof, tr, crs = read_level(item["assets"][f"{pol}_gamma0"], level)
        m, *_ = read_level(item["assets"]["oa_layover_shadow_mask"], level)
        a[(m != 0) | ~np.isfinite(m) | (a <= 0)] = np.nan
        return boxcar(a, k), prof, tr, crs

    ref = {}
    for pol in ("vh", "vv"):
        ref[pol], prof, tr, crs = load(late, pol)
    bands = []
    for early in (apr, sep):
        for pol in ("vh", "vv"):
            if early is None:
                bands.append(np.full_like(ref[pol], np.nan))
                continue
            e, *_ = load(early, pol)
            if e.shape != ref[pol].shape:
                raise RuntimeError(f"{entry['burst']}: grid mismatch {e.shape} vs {ref[pol].shape}")
            with np.errstate(divide="ignore", invalid="ignore"):
                bands.append((10 * np.log10(ref[pol] / e)).astype("float32"))
    prof.update(driver="GTiff", count=4, dtype="float32", nodata=np.nan, compress="deflate",
                tiled=True, blockxsize=512, blockysize=512, predictor=3)
    prof.pop("photometric", None)
    with rasterio.open(out, "w", **prof) as d:
        for b, (name, arr) in enumerate(zip(BANDS, bands), 1):
            d.write(arr, b)
            d.set_band_description(b, name)
        d.update_tags(burst=entry["burst"], late=late["datetime"],
                      apr=apr["datetime"] if apr else "", sep=sep["datetime"] if sep else "",
                      orbit=late["orbit"] or "", level=str(level), boxcar=str(k))
    return out


def cmd_burst(args) -> None:
    index = json.loads((WORK / "index.json").read_text())
    if args.task_id is not None:
        entries = [index[args.task_id]]
    elif args.burst:
        entries = [e for e in index if e["burst"] in args.burst]
    else:
        entries = index
    todo = [e for e in entries if args.overwrite or not (WORK / "bursts" / f"{e['burst']}.tif").exists()]
    print(f"{len(todo)} bursts to process at overview level {args.level}")

    def run(e):
        try:
            return burst_change(e, args.level, args.boxcar)
        except Exception as exc:  # keep going; report at the end
            print(f"{e['burst']}: FAILED {exc}", file=sys.stderr)
            return None

    with ThreadPoolExecutor(args.threads) as ex:
        done = [p for p in ex.map(run, todo) if p]
    print(f"wrote {len(done)} burst files")


# ---------------------------------------------------------------- mosaic
NE = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/"


def land_mask(transform, w: int, h: int) -> np.ndarray:
    """True on land, from Natural Earth 10 m land minus 10 m lakes (cached in WORK).
    Open water is dropped because wind on the sea and lakes changes backscatter by many dB
    between dates and swamps any land signal."""
    from rasterio.features import rasterize
    from rasterio.warp import transform_geom
    from shapely.geometry import box, mapping, shape

    def shapes(name):
        p = WORK / f"{name}.geojson"
        if not p.exists():
            p.write_bytes(urllib.request.urlopen(NE + f"{name}.geojson", timeout=120).read())
        out = []
        for f in json.loads(p.read_text())["features"]:
            g = f["geometry"]
            if g and intersects(_geom_bbox(g), TAS_BBOX):
                clipped = shape(g).intersection(box(*TAS_BBOX))
                if not clipped.is_empty:
                    out.append(transform_geom("EPSG:4326", DST_CRS, mapping(clipped)))
        return out

    land = rasterize([(g, 1) for g in shapes("ne_10m_land")], out_shape=(h, w), transform=transform, fill=0, dtype="uint8")
    lakes = shapes("ne_10m_lakes")
    if lakes:
        land[rasterize([(g, 1) for g in lakes], out_shape=(h, w), transform=transform, fill=0, dtype="uint8") == 1] = 0
    print(f"land mask: {land.mean():.1%} of grid is land")
    return land.astype(bool)


def _geom_bbox(g) -> tuple:
    xs, ys = [], []

    def walk(c):
        if isinstance(c[0], (int, float)):
            xs.append(c[0]); ys.append(c[1])
        else:
            for cc in c:
                walk(cc)
    walk(g["coordinates"])
    return (min(xs), min(ys), max(xs), max(ys))


def cmd_mosaic(args) -> None:
    files = sorted((WORK / "bursts").glob("*.tif"))
    if not files:
        sys.exit("no burst outputs; run the burst step first")
    l, b, r, t = transform_bounds("EPSG:4326", DST_CRS, *TAS_BBOX, densify_pts=21)
    res = args.res
    l, b = math.floor(l / res) * res, math.floor(b / res) * res
    r, t = math.ceil(r / res) * res, math.ceil(t / res) * res
    w, h = int((r - l) / res), int((t - b) / res)
    dst_tr = rasterio.transform.from_origin(l, t, res, res)
    print(f"grid {w} x {h} at {res} m in {DST_CRS}")
    OUT.mkdir(parents=True, exist_ok=True)
    land = None if args.no_land_mask else land_mask(dst_tr, w, h)
    groups = {"all": files}
    if args.by_orbit:
        for f in files:
            with rasterio.open(f) as d:
                groups.setdefault(d.tags().get("orbit", "unknown"), []).append(f)
    for gname, gfiles in groups.items():
        acc = np.zeros((4, h, w), "float32")
        cnt = np.zeros((4, h, w), "uint16")
        for f in gfiles:
            with rasterio.open(f) as src:
                # only reproject into the window this burst covers
                bl, bb_, br, bt = transform_bounds(src.crs, DST_CRS, *src.bounds, densify_pts=21)
                c0, c1 = max(0, int((bl - l) // res) - 1), min(w, int((br - l) // res) + 2)
                r0, r1 = max(0, int((t - bt) // res) - 1), min(h, int((t - bb_) // res) + 2)
                if c1 <= c0 or r1 <= r0:
                    continue
                win_tr = rasterio.transform.from_origin(l + c0 * res, t - r0 * res, res, res)
                for bi in range(4):
                    dst = np.full((r1 - r0, c1 - c0), np.nan, "float32")
                    reproject(rasterio.band(src, bi + 1), dst, dst_transform=win_tr, dst_crs=DST_CRS,
                              src_nodata=np.nan, dst_nodata=np.nan, resampling=Resampling.average)
                    v = np.isfinite(dst)
                    acc[bi, r0:r1, c0:c1][v] += dst[v]
                    cnt[bi, r0:r1, c0:c1][v] += 1
        mean = np.where(cnt > 0, acc / np.maximum(cnt, 1), np.nan).astype("float32")
        if land is not None:
            mean[:, ~land] = np.nan
        out = OUT / f"tas_s1_change_{gname}_{int(res)}m.tif"
        prof = dict(driver="GTiff", width=w, height=h, count=5, dtype="float32", crs=DST_CRS,
                    transform=dst_tr, nodata=np.nan, compress="deflate", tiled=True,
                    blockxsize=512, blockysize=512, predictor=3)
        with rasterio.open(out, "w", **prof) as d:
            for bi in range(4):
                d.write(mean[bi], bi + 1)
                d.set_band_description(bi + 1, BANDS[bi] + "_db")
            d.write(cnt[0].astype("float32"), 5)
            d.set_band_description(5, "n_bursts_vh_apr_oct")
            d.update_tags(units="dB, 10*log10(late/early) of gamma0", source=f"s3://{BUCKET}/{PRODUCT}",
                          n_files=str(len(gfiles)))
        print(f"{gname}: {len(gfiles)} bursts -> {out}")


# ---------------------------------------------------------------- figure
def cmd_figure(args) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    f = OUT / f"tas_s1_change_all_{int(args.res)}m.tif"
    with rasterio.open(f) as d:
        # decimate big mosaics for plotting (stats then come from the decimated grid too)
        fac = max(1, math.ceil(max(d.width, d.height) / args.max_px))
        a = d.read(out_shape=(d.count, d.height // fac, d.width // fac), resampling=Resampling.average)
        ext = (d.bounds.left, d.bounds.right, d.bounds.bottom, d.bounds.top)
    stats = {}
    fig, axs = plt.subplots(2, 2, figsize=(12, 11), constrained_layout=True)
    titles = {"vh_apr_oct": "VH change, April -> October 2026",
              "vv_apr_oct": "VV change, April -> October 2026",
              "vh_sep_oct": "VH change, September -> October 2026 (12-day reference)",
              "vv_sep_oct": "VV change, September -> October 2026 (12-day reference)"}
    for ax, bi in zip(axs.flat, range(4)):
        x = a[bi]
        v = x[np.isfinite(x)]
        stats[BANDS[bi]] = dict(n=int(v.size), p01=float(np.percentile(v, 1)) if v.size else None,
                                median=float(np.median(v)) if v.size else None,
                                p99=float(np.percentile(v, 99)) if v.size else None,
                                sd=float(v.std()) if v.size else None,
                                frac_lt_minus3=float((v < -3).mean()) if v.size else None,
                                frac_gt_plus3=float((v > 3).mean()) if v.size else None)
        im = ax.imshow(x, extent=ext, cmap="RdBu", vmin=-4, vmax=4, interpolation="nearest")
        ax.set_title(titles[BANDS[bi]], fontsize=10)
        ax.set_xlabel("Easting (m, MGA zone 55)")
        ax.set_ylabel("Northing (m)")
        ax.ticklabel_format(style="plain")
        ax.tick_params(labelsize=7)
        # 50 km scale bar + north arrow (grid north ~ true north in MGA55 here)
        x0, y0 = ext[0] + 0.05 * (ext[1] - ext[0]), ext[2] + 0.05 * (ext[3] - ext[2])
        ax.plot([x0, x0 + 50000], [y0, y0], "k-", lw=3)
        ax.text(x0 + 25000, y0 + 6000, "50 km", ha="center", fontsize=8)
        xn, yn = ext[1] - 0.07 * (ext[1] - ext[0]), ext[3] - 0.12 * (ext[3] - ext[2])
        ax.annotate("N", xy=(xn, yn + 30000), xytext=(xn, yn), ha="center", fontsize=10,
                    arrowprops=dict(arrowstyle="-|>", color="k"))
    fig.colorbar(im, ax=axs, shrink=0.6, label="Backscatter change (dB); blue = brighter later, red = darker later")
    fig.suptitle("DEA Sentinel-1 NRB gamma0 change over Tasmania, 2026 (burst-wise, layover/shadow masked)")
    p = OUT / f"tas_s1_change_{int(args.res)}m.png"
    fig.savefig(p, dpi=110)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(7, 4), constrained_layout=True)
    bins = np.linspace(-8, 8, 161)
    for bi in range(4):
        v = a[bi][np.isfinite(a[bi])]
        ax.hist(v, bins=bins, histtype="step", density=True, label=BANDS[bi])
    ax.set_xlabel("Change (dB)")
    ax.set_ylabel("Density")
    ax.set_title("Distribution of backscatter change, Tasmania 2026")
    ax.legend(fontsize=8)
    ph = OUT / f"tas_s1_change_hist_{int(args.res)}m.png"
    fig.savefig(ph, dpi=110)
    plt.close(fig)
    (OUT / f"tas_s1_change_stats_{int(args.res)}m.json").write_text(json.dumps(stats, indent=1))
    print(json.dumps(stats, indent=1))
    print(f"-> {p}\n-> {ph}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--threads", type=int, default=int(os.environ.get("SLURM_CPUS_PER_TASK", 8)))
        p.add_argument("--level", type=int, default=0, help="0 = native 20 m; 1,2,3,4 = COG overviews (x4, x16, x64, x125 on this product)")
        p.add_argument("--boxcar", type=int, default=5, help="speckle filter window in pixels (1 = none)")
        p.add_argument("--res", type=float, default=40.0, help="mosaic pixel size in metres")

    p = sub.add_parser("index"); common(p); p.add_argument("--step", type=int, default=20)
    p = sub.add_parser("burst"); common(p)
    p.add_argument("--task-id", type=int, default=None, help="row of index.json (use $SLURM_ARRAY_TASK_ID)")
    p.add_argument("--burst", nargs="*")
    p.add_argument("--overwrite", action="store_true")
    p = sub.add_parser("mosaic"); common(p); p.add_argument("--by-orbit", action="store_true")
    p.add_argument("--no-land-mask", action="store_true")
    p = sub.add_parser("figure"); common(p)
    p.add_argument("--max-px", type=int, default=4000, help="decimate the mosaic to at most this many pixels a side")
    p = sub.add_parser("all"); common(p); p.add_argument("--step", type=int, default=20)
    args = ap.parse_args()

    if args.cmd == "index":
        cmd_index(args)
    elif args.cmd == "burst":
        cmd_burst(args)
    elif args.cmd == "mosaic":
        cmd_mosaic(args)
    elif args.cmd == "figure":
        cmd_figure(args)
    else:
        if not (WORK / "index.json").exists():
            cmd_index(args)
        args.task_id, args.burst, args.overwrite, args.by_orbit, args.no_land_mask = None, None, False, True, False
        args.max_px = 4000
        cmd_burst(args)
        cmd_mosaic(args)
        cmd_figure(args)


if __name__ == "__main__":
    main()

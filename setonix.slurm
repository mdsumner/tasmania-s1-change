#!/bin/bash --login
# Tasmania Sentinel-1 backscatter change (DEA NRB), whole state at native 20 m.
# One node is plenty: about 30 s per burst per thread, ~250 bursts, ~16 GB of burst outputs.
#
#   sbatch --account=<your-pawsey-project> setonix.slurm
#
#SBATCH --job-name=tas-s1-change
#SBATCH --partition=work
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=32
#SBATCH --mem=96G
#SBATCH --time=01:00:00
#SBATCH --output=%x-%j.out

set -euo pipefail
export PYTHONUNBUFFERED=1   # so progress shows in the .out file as it happens

# Needs Python with rasterio, numpy, shapely and matplotlib: a venv made once on the login
# node (see README, "Setonix setup").
# Setonix disables default module versions, so name one (override with PYTHON_MODULE=...).
# It must match the module the venv was built with.
module load "${PYTHON_MODULE:-python/3.11.6}"
source "${TAS_S1_VENV:-${MYSOFTWARE}/venvs/tas-s1}/bin/activate"

SRC="${SLURM_SUBMIT_DIR}/tas_s1_change.py"
export TAS_S1_WORK="${MYSCRATCH}/tas-s1-change/work"
export TAS_S1_OUT="${MYSCRATCH}/tas-s1-change/out"
mkdir -p "$TAS_S1_WORK" "$TAS_S1_OUT"
# GDAL HTTP cache per job, on scratch not /tmp
export CPL_VSIL_CURL_CACHE_SIZE=200000000

T=${SLURM_CPUS_PER_TASK}
[ -f "$TAS_S1_WORK/index.json" ] || python "$SRC" index --threads "$T"
python "$SRC" burst  --level 0 --boxcar 5 --threads "$T"
python "$SRC" mosaic --res 20 --by-orbit --threads "$T"
python "$SRC" figure --res 20 --max-px 4000

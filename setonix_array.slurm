#!/bin/bash --login
# Array variant: one burst per task. Use when the full DEA archive arrives (many dates and
# bursts), or for all of Australia. Run index first (login node is fine, it is light):
#   python tas_s1_change.py index
#   N=$(python -c "import json,os;print(len(json.load(open(os.environ['TAS_S1_WORK']+'/index.json')))-1)")
#   jid=$(sbatch --parsable --account=<proj> --array=0-$N%64 setonix_array.slurm)
#   sbatch --account=<proj> --dependency=afterok:$jid --cpus-per-task=8 --mem=96G \
#          --wrap "python tas_s1_change.py mosaic --res 20 --by-orbit && python tas_s1_change.py figure --res 20"
#
#SBATCH --job-name=tas-s1-burst
#SBATCH --partition=work
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
#SBATCH --time=00:15:00
#SBATCH --output=logs/%x-%A_%a.out

set -euo pipefail
export PYTHONUNBUFFERED=1   # so progress shows in the .out file as it happens
# Setonix disables default module versions, so name one (override with PYTHON_MODULE=...).
# It must match the module the venv was built with.
module load "${PYTHON_MODULE:-python/3.11.6}"
source "${TAS_S1_VENV:-${MYSOFTWARE}/venvs/tas-s1}/bin/activate"
export TAS_S1_WORK="${TAS_S1_WORK:-${MYSCRATCH}/tas-s1-change/work}"
python "${SLURM_SUBMIT_DIR}/tas_s1_change.py" burst --task-id "${SLURM_ARRAY_TASK_ID}" --level 0 --boxcar 5 --threads 2

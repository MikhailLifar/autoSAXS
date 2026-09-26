#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="/home/mikl/KurchatovCoop/autosaxs"
PY="/home/mikl/.conda/envs/dev_autosaxs/bin/python"
PIP="/home/mikl/.conda/envs/dev_autosaxs/bin/pip"

mkdir -p "${REPO_DIR}"
cd "${REPO_DIR}"

LOG_PATH="${REPO_DIR}/commit.log"
{
  echo
  echo "============================================================"
  echo "run_tests.sh started at $(date -Is)"
  echo "cwd: $(pwd)"
  echo "============================================================"
} >> "${LOG_PATH}"

exec > >(tee -a "${LOG_PATH}") 2>&1

step() {
  echo
  echo "==> $*"
}

PYTEST_OPTS=(-q --tb=short --import-mode=importlib)

step "Commit gate: skill contracts / CLI"
"${PY}" -m pytest "${REPO_DIR}/tests/must-run/skills" "${PYTEST_OPTS[@]}"

step "Commit gate: real-data light"
"${PY}" -m pytest "${REPO_DIR}/tests/must-run/real_data/light" "${PYTEST_OPTS[@]}"

step "Commit gate: real-data heavy (model_dam; only after light)"
"${PY}" -m pytest "${REPO_DIR}/tests/must-run/real_data/heavy" "${PYTEST_OPTS[@]}"

step "Install autosaxs with GUI extra (needed for guisaxs-liveview tests)"
"${PIP}" install -e "${REPO_DIR}[gui]"

step "Commit gate: liveview light (headless)"
cd "${REPO_DIR}"
xvfb-run -a "${PY}" -m pytest tests/must-run/guisaxs_liveview/light -v --tb=short --import-mode=importlib

step "Commit gate: liveview pipeline (headless; no DAM)"
xvfb-run -a "${PY}" -m pytest tests/must-run/guisaxs_liveview/pipeline -v --tb=short --import-mode=importlib

step "Tests OK"
echo "run_tests.sh finished at $(date -Is)"
echo "Note: tests/optional/ and tests/development/ are NOT part of the commit gate."

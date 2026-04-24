#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="/home/mikl/KurchatovCoop/repos"
PY="/home/mikl/.conda/envs/LLMAssistant/bin/python"
PIP="/home/mikl/.conda/envs/LLMAssistant/bin/pip"

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

step "Run unit/skill tests"
"${PY}" -m pytest "${REPO_DIR}/tests/test_skill.py" -q --tb=short

step "Run real-data tests"
"${PY}" -m pytest "${REPO_DIR}/tests/test_skills_real_data.py" -q --tb=short

step "Install autosaxs with GUI extra (needed for guisaxs tests)"
"${PIP}" install -e "${REPO_DIR}[gui]"

step "Run GUI tests headless (xvfb-run required)"
cd "${REPO_DIR}"
xvfb-run -a "${PY}" -m pytest tests/test_guisaxs_liveview.py -v --tb=short

step "Tests OK"
echo "run_tests.sh finished at $(date -Is)"


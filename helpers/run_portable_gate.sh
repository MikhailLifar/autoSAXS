#!/usr/bin/env bash
# Portable commit gate for Cloud Agents / non-lab checkouts.
# Sibling to helpers/run_tests.sh (lab gate: hardcoded conda + real_data + liveview/pipeline).
# This gate: lint-imports + skills must-run + liveview light only (no validation TIFF tree).
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PYTHON:-python3}"
PY_DIR="$(cd "$(dirname "${PY}")" && pwd)"
LINT_IMPORTS="${PY_DIR}/lint-imports"

cd "${REPO_DIR}"

step() {
  echo
  echo "==> $*"
}

PYTEST_OPTS=(-q --tb=short --import-mode=importlib)

step "Portable gate: lint-imports"
if [[ -x "${LINT_IMPORTS}" ]]; then
  "${LINT_IMPORTS}"
elif command -v lint-imports >/dev/null 2>&1; then
  lint-imports
else
  echo "error: lint-imports not found next to ${PY}; install import-linter (pip install '.[dev]')" >&2
  exit 1
fi

step "Portable gate: skill contracts / CLI"
"${PY}" -m pytest "${REPO_DIR}/tests/must-run/skills" "${PYTEST_OPTS[@]}"

step "Portable gate: liveview light (headless; no real_data / pipeline)"
cd "${REPO_DIR}"
MPLBACKEND=Agg xvfb-run -a "${PY}" -m pytest \
  tests/must-run/guisaxs_liveview/light \
  -v --tb=short --import-mode=importlib

step "Portable gate OK"
echo "run_portable_gate.sh finished at $(date -Is)"
echo "Skipped (lab-only): tests/must-run/real_data, tests/must-run/guisaxs_liveview/pipeline"

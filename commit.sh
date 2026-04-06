#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="/home/mikl/KurchatovCoop/repos"
PY="/home/mikl/.conda/envs/LLMAssistant/bin/python"
PIP="/home/mikl/.conda/envs/LLMAssistant/bin/pip"

if [[ "${1:-}" == "" ]]; then
  echo "Usage: $0 \"commit message\"" >&2
  exit 2
fi

COMMIT_MSG="$1"

mkdir -p "${REPO_DIR}"
cd "${REPO_DIR}"

LOG_PATH="${REPO_DIR}/commit.log"
{
  echo
  echo "============================================================"
  echo "commit.sh started at $(date -Is)"
  echo "cwd: $(pwd)"
  echo "commit message: ${COMMIT_MSG}"
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
xvfb-run -a "${PY}" -m pytest tests/test_guisaxs.py -v --tb=short

step "Update skills documentation"
"${PY}" "${REPO_DIR}/autosaxs_skills_explained.py"

step "Patch-bump autosaxs version in pyproject.toml and autosaxs/__init__.py"
"${PY}" - <<'PY'
from __future__ import annotations

import re
from pathlib import Path

REPO = Path("/home/mikl/KurchatovCoop/repos")
PYPROJECT = REPO / "pyproject.toml"
INIT = REPO / "autosaxs" / "__init__.py"

py_text = PYPROJECT.read_text(encoding="utf-8")
m = re.search(r'(?m)^\s*version\s*=\s*"(\d+)\.(\d+)\.(\d+)"\s*$', py_text)
if not m:
    raise SystemExit("Could not find [project].version in pyproject.toml")
maj, minor, patch = map(int, m.groups())
new_ver = f"{maj}.{minor}.{patch + 1}"
py_text2 = re.sub(
    r'(?m)^(\s*version\s*=\s*")(\d+\.\d+\.\d+)("\s*)$',
    lambda mm: f'{mm.group(1)}{new_ver}{mm.group(3)}',
    py_text,
    count=1,
)
if py_text2 == py_text:
    raise SystemExit("Failed to update version in pyproject.toml")
PYPROJECT.write_text(py_text2, encoding="utf-8")

init_text = INIT.read_text(encoding="utf-8")
init_text2 = re.sub(
    r'(?m)^(?:__version__\s*=\s*")(\d+\.\d+\.\d+)(")\s*$',
    lambda mm: f'__version__ = "{new_ver}"',
    init_text,
    count=1,
)
if init_text2 == init_text:
    raise SystemExit("Failed to update __version__ in autosaxs/__init__.py")
INIT.write_text(init_text2, encoding="utf-8")

print(f"Bumped autosaxs version: {m.group(0).split('=')[1].strip()} -> {new_ver}")
PY

step "Re-install autosaxs editable with GUI extra"
"${PIP}" install -e "${REPO_DIR}[gui]"

step "Verify reinstall + version"
"${PY}" -c "import autosaxs; print(autosaxs.__version__)"
/home/mikl/.conda/envs/LLMAssistant/bin/autosaxs --help >/dev/null

step "Git commit + push (tracked changes only; no git add)"
git commit -a -m "${COMMIT_MSG}"
git push

step "Done"
echo "commit.sh finished at $(date -Is)"


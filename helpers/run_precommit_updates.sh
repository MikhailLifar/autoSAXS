#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="/home/mikl/KurchatovCoop/repos"
PY="/home/mikl/.conda/envs/LLMAssistant/bin/python"
PIP="/home/mikl/.conda/envs/LLMAssistant/bin/pip"
AUTOSAXS="/home/mikl/.conda/envs/LLMAssistant/bin/autosaxs"

REQUESTED_VERSION="${1:-}"

mkdir -p "${REPO_DIR}"
cd "${REPO_DIR}"

LOG_PATH="${REPO_DIR}/commit.log"
{
  echo
  echo "============================================================"
  echo "run_precommit_updates.sh started at $(date -Is)"
  echo "cwd: $(pwd)"
  if [[ -n "${REQUESTED_VERSION}" ]]; then
    echo "requested version: ${REQUESTED_VERSION}"
  else
    echo "requested version: (patch bump)"
  fi
  echo "============================================================"
} >> "${LOG_PATH}"

exec > >(tee -a "${LOG_PATH}") 2>&1

step() {
  echo
  echo "==> $*"
}

step "Update skills documentation"
"${PY}" "${REPO_DIR}/autosaxs_skills_explained.py"

step "Update autosaxs version"
PYCODE=$'from __future__ import annotations\n\nimport re\nimport sys\nfrom pathlib import Path\n\nREPO = Path(\"/home/mikl/KurchatovCoop/repos\")\nPYPROJECT = REPO / \"pyproject.toml\"\nINIT = REPO / \"autosaxs\" / \"__init__.py\"\n\nrequested = (sys.argv[1].strip() if len(sys.argv) > 1 else \"\") or None\n\npy_text = PYPROJECT.read_text(encoding=\"utf-8\")\nm = re.search(r\"(?m)^\\s*version\\s*=\\s*\\\"(\\d+)\\.(\\d+)\\.(\\d+)\\\"\\s*$\", py_text)\nif not m:\n    raise SystemExit(\"Could not find [project].version in pyproject.toml\")\ncur = tuple(map(int, m.groups()))\n\ndef parse_ver(s: str) -> tuple[int, int, int]:\n    mm = re.fullmatch(r\"(\\d+)\\.(\\d+)\\.(\\d+)\", s.strip())\n    if not mm:\n        raise SystemExit(f\"Invalid version format: {s!r} (expected MAJOR.MINOR.PATCH)\")\n    return tuple(map(int, mm.groups()))\n\nif requested is None:\n    new = (cur[0], cur[1], cur[2] + 1)\nelse:\n    new = parse_ver(requested)\n    if new <= cur:\n        raise SystemExit(\n            f\"Requested version {requested} is not larger than current {cur[0]}.{cur[1]}.{cur[2]}\"\n        )\n\nnew_ver = f\"{new[0]}.{new[1]}.{new[2]}\"\n\npy_text2 = re.sub(\n    r\"(?m)^(\\s*version\\s*=\\s*\\\")(\\d+\\.\\d+\\.\\d+)(\\\"\\s*)$\",\n    lambda mm: f\"{mm.group(1)}{new_ver}{mm.group(3)}\",\n    py_text,\n    count=1,\n)\nif py_text2 == py_text:\n    raise SystemExit(\"Failed to update version in pyproject.toml\")\nPYPROJECT.write_text(py_text2, encoding=\"utf-8\")\n\ninit_text = INIT.read_text(encoding=\"utf-8\")\ninit_text2 = re.sub(\n    r\"(?m)^(?:__version__\\s*=\\s*\\\")(\\d+\\.\\d+\\.\\d+)(\\\")\\s*$\",\n    lambda mm: f\"__version__ = \\\"{new_ver}\\\"\",\n    init_text,\n    count=1,\n)\nif init_text2 == init_text:\n    raise SystemExit(\"Failed to update __version__ in autosaxs/__init__.py\")\nINIT.write_text(init_text2, encoding=\"utf-8\")\n\nprint(new_ver)\n'
NEW_VERSION="$(${PY} -c "${PYCODE}" "${REQUESTED_VERSION}")"
echo "autosaxs version is now ${NEW_VERSION}"

step "Re-install autosaxs editable with GUI extra"
"${PIP}" install -e "${REPO_DIR}[gui]"

step "Update Cursor skills (repo)"
"${AUTOSAXS}" get-skills -o "${REPO_DIR}/.cursor/skills"

step "Update Cursor skills (user)"
"${AUTOSAXS}" get-skills -o "${HOME}/.cursor/skills"

step "Verify reinstall + version"
"${PY}" -c "import autosaxs; print(autosaxs.__version__)"
"${AUTOSAXS}" --help >/dev/null

step "Pre-commit updates OK (version ${NEW_VERSION})"
echo "${NEW_VERSION}"
echo "run_precommit_updates.sh finished at $(date -Is)"


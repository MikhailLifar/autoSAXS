#!/usr/bin/env bash
# Install-autoSAXS.sh — beginner installer (Linux). No global Python required for the UI.
# Uses zenity or kdialog. Installs via conda create + pip install "autosaxs[gui]".
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ASSETS_DIR="${SCRIPT_DIR}/assets"
ICON_PNG="${ASSETS_DIR}/autosaxs_icon.png"
ENV_NAME="autosaxs"
DEFAULT_ENV_NAME="autosaxs"
PIP_SPEC="autosaxs[gui]"
MINICONDA_URL="https://docs.anaconda.com/miniconda/miniconda-install/"

DIALOG=""
if command -v zenity >/dev/null 2>&1; then
  DIALOG=zenity
elif command -v kdialog >/dev/null 2>&1; then
  DIALOG=kdialog
else
  echo "autoSAXS installer needs a dialog tool (zenity or kdialog)."
  echo "On Ubuntu/Debian:  sudo apt install zenity"
  echo "On Fedora:         sudo dnf install zenity"
  echo "Then run this script again."
  if command -v xmessage >/dev/null 2>&1; then
    xmessage -center "Install zenity (or kdialog), then run Install-autoSAXS.sh again."
  fi
  exit 1
fi

info() {
  local msg="$1"
  if [[ "$DIALOG" == zenity ]]; then
    zenity --info --title="Install autoSAXS" --width=420 --text="$msg" || true
  else
    kdialog --title "Install autoSAXS" --msgbox "$msg" || true
  fi
}

error() {
  local msg="$1"
  if [[ "$DIALOG" == zenity ]]; then
    zenity --error --title="Install autoSAXS" --width=480 --text="$msg" || true
  else
    kdialog --title "Install autoSAXS" --error "$msg" || true
  fi
}

question_yesno() {
  local msg="$1"
  if [[ "$DIALOG" == zenity ]]; then
    zenity --question --title="Install autoSAXS" --width=420 --text="$msg"
  else
    kdialog --title "Install autoSAXS" --yesno "$msg"
  fi
}

find_conda() {
  if command -v conda >/dev/null 2>&1; then
    local c
    c="$(command -v conda)"
    if validate_conda "$c"; then
      printf '%s\n' "$c"
      return 0
    fi
  fi
  local root c
  while IFS= read -r root; do
    [[ -n "$root" && -d "$root" ]] || continue
    if c="$(resolve_conda_from_dir "$root")" && validate_conda "$c"; then
      printf '%s\n' "$c"
      return 0
    fi
  done < <(ls -td "${HOME}"/miniconda* "${HOME}"/Miniconda* "${HOME}"/anaconda* "${HOME}"/Anaconda* /opt/conda /usr/local/miniconda3 2>/dev/null | awk '!seen[tolower($0)]++')
  return 1
}

resolve_conda_from_dir() {
  local root="$1"
  root="${root%/}"
  root="${root#\"}"
  root="${root%\"}"
  [[ -d "$root" ]] || return 1
  local c
  for c in "${root}/bin/conda" "${root}/Scripts/conda.exe"; do
    if [[ -x "$c" ]]; then
      printf '%s\n' "$c"
      return 0
    fi
  done
  return 1
}

validate_conda() {
  local conda_exe="$1"
  [[ -n "$conda_exe" && -x "$conda_exe" ]] || return 1
  "$conda_exe" --version >/dev/null 2>&1
}

confirm_conda_choice() {
  if [[ "$DIALOG" == zenity ]]; then
    choice="$(zenity --list --title="Install autoSAXS" --width=520 --height=280 \
      --text="Miniconda / Anaconda ready:

${CONDA}

How do you want to continue?" \
      --column="Action" \
      "Continue to install options" \
      "Choose a different install folder…" \
      "Exit" \
      || true)"
    case "$choice" in
      "Continue to install options") return 0 ;;
      "Choose a different install folder…")
        CONDA=""
        if prompt_manual_conda_dir; then
          confirm_conda_choice
          return $?
        fi
        return 1
        ;;
      *) return 1 ;;
    esac
  else
    choice="$(kdialog --title "Install autoSAXS" --menu \
      "Miniconda / Anaconda ready:

${CONDA}" \
      continue "Continue to install options" \
      manual "Choose a different install folder…" \
      exit "Exit" \
      continue 2>/dev/null || true)"
    case "$choice" in
      continue) return 0 ;;
      manual)
        CONDA=""
        if prompt_manual_conda_dir; then
          confirm_conda_choice
          return $?
        fi
        return 1
        ;;
      *) return 1 ;;
    esac
  fi
}

prompt_manual_conda_dir() {
  local dir="" exe=""
  while true; do
    if [[ "$DIALOG" == zenity ]]; then
      dir="$(zenity --file-selection --directory --title="Select Miniconda/Anaconda install folder" 2>/dev/null || true)"
      [[ -z "$dir" ]] && return 1
    else
      dir="$(kdialog --getexistingdirectory "${HOME}" --title "Select Miniconda/Anaconda install folder" 2>/dev/null || true)"
      [[ -z "$dir" ]] && return 1
    fi
    if exe="$(resolve_conda_from_dir "$dir")" && validate_conda "$exe"; then
      CONDA="$exe"
      return 0
    fi
    error "That folder does not look like a Miniconda / Anaconda install.

Choose the top-level folder that contains bin/conda (for example ${HOME}/miniconda3)."
  done
}

handle_conda_missing_page() {
  if [[ "$DIALOG" == zenity ]]; then
    choice="$(zenity --list --title="Install autoSAXS" --width=520 --height=300 \
      --text="autoSAXS needs Miniconda (a free Python toolbox).\nIt was not found automatically.\n\nInstall Miniconda, enter your conda directory path, or click Retry." \
      --column="Action" \
      "Open Miniconda download page" \
      "Enter conda directory path…" \
      "Retry search" \
      "Exit" \
      || true)"
    case "$choice" in
      "Open Miniconda download page")
        (xdg-open "$MINICONDA_URL" >/dev/null 2>&1 || true)
        ;;
      "Enter conda directory path…")
        if prompt_manual_conda_dir; then
          return 0
        fi
        ;;
      "Retry search") ;;
      *) exit 1 ;;
    esac
  else
    choice="$(kdialog --title "Install autoSAXS" --menu \
      "autoSAXS needs Miniconda (not found automatically)." \
      manual "Enter conda directory path…" \
      download "Open Miniconda download page" \
      retry "Retry search" \
      exit "Exit" \
      manual 2>/dev/null || true)"
    case "$choice" in
      manual)
        if prompt_manual_conda_dir; then
          return 0
        fi
        ;;
      download)
        xdg-open "$MINICONDA_URL" >/dev/null 2>&1 || true
        ;;
      retry) ;;
      *) exit 1 ;;
    esac
  fi
  return 1
}

validate_env_name() {
  local name="$1"
  [[ -n "$name" && ${#name} -le 64 && "$name" =~ ^[a-zA-Z0-9][a-zA-Z0-9._-]*$ ]]
}

prompt_env_name() {
  local name=""
  while true; do
    if [[ "$DIALOG" == zenity ]]; then
      name="$(zenity --entry --title="Install autoSAXS" --width=420 \
        --text="Conda environment name for autoSAXS:" \
        --entry-text="${DEFAULT_ENV_NAME}" 2>/dev/null || true)"
    else
      name="$(kdialog --title "Install autoSAXS" --inputbox "Conda environment name for autoSAXS:" "${DEFAULT_ENV_NAME}" 2>/dev/null || true)"
    fi
    [[ -z "$name" ]] && exit 0
    if validate_env_name "$name"; then
      ENV_NAME="$name"
      return 0
    fi
    error "Invalid environment name. Use letters, numbers, dots, hyphens, and underscores (for example: autosaxs)."
  done
}

create_shortcut() {
  local liveview="$1"
  local desktop="${XDG_DESKTOP_DIR:-}"
  if [[ -z "$desktop" || ! -d "$desktop" ]]; then
    desktop="${HOME}/Desktop"
  fi
  mkdir -p "$desktop"
  local app_dir="${HOME}/.local/share/applications"
  mkdir -p "$app_dir"
  local icon_line=""
  if [[ -f "$ICON_PNG" ]]; then
    icon_line="Icon=${ICON_PNG}"
  fi
  local dest
  for dest in "${desktop}/GUISAXS-LiveView.desktop" "${app_dir}/GUISAXS-LiveView.desktop"; do
    cat >"$dest" <<EOF
[Desktop Entry]
Name=GUISAXS-LiveView
Comment=Live-view app for online SAXS processing
Exec=${liveview}
Path=${desktop}
Terminal=false
Type=Application
Categories=Science;Education;
${icon_line}
EOF
    chmod +x "$dest"
  done
}

# --- Page 1: conda ---
CONDA=""
while true; do
  if [[ -z "$CONDA" ]]; then
    CONDA="$(find_conda || true)"
  fi
  if [[ -n "$CONDA" ]]; then
    if confirm_conda_choice; then
      break
    else
      CONDA=""
      exit 0
    fi
  fi
  if handle_conda_missing_page; then
    continue
  fi
done

# --- Page 2: options (environment name + Desktop shortcut) ---
prompt_env_name

CREATE_SHORTCUT=1
if question_yesno "Create a Desktop shortcut for GUISAXS-LiveView?\n\n(Recommended: Yes)"; then
  CREATE_SHORTCUT=1
else
  CREATE_SHORTCUT=0
fi

if ! question_yesno "Install autoSAXS into conda environment '${ENV_NAME}' now?\n\nThis downloads packages from the internet and may take several minutes."; then
  exit 0
fi

# --- Page 3: install ---
LOG="$(mktemp /tmp/autosaxs-install-XXXXXX.log)"
LIVEVIEW=""
STATUS=0

run_install() {
  echo "Using conda: ${CONDA}"
  if ! "${CONDA}" env list | awk '{print $1}' | grep -qx "${ENV_NAME}"; then
    echo "Creating environment ${ENV_NAME} (python 3.12)..."
    set +e
    CONDA_ALWAYS_YES=true "${CONDA}" create -n "${ENV_NAME}" python=3.12 pip -y
    local create_rc=$?
    if [[ "$create_rc" -ne 0 ]]; then
      echo "Clearing incomplete conda package downloads and retrying once..."
      CONDA_ALWAYS_YES=true "${CONDA}" clean --packages -y
      CONDA_ALWAYS_YES=true "${CONDA}" create -n "${ENV_NAME}" python=3.12 pip -y
      create_rc=$?
    fi
    set -e
    if [[ "$create_rc" -ne 0 ]]; then
      return "$create_rc"
    fi
  else
    echo "Environment ${ENV_NAME} already exists - upgrading package..."
  fi
  echo "Installing ${PIP_SPEC}..."
  "${CONDA}" run -n "${ENV_NAME}" python -m pip install -U "${PIP_SPEC}"
  local prefix
  prefix="$("${CONDA}" run -n "${ENV_NAME}" python -c 'import sys; print(sys.prefix)')"
  LIVEVIEW="${prefix}/bin/guisaxs-liveview"
  if [[ ! -x "$LIVEVIEW" ]]; then
    echo "ERROR: guisaxs-liveview not found at ${LIVEVIEW}" >&2
    return 1
  fi
  echo "LiveView: ${LIVEVIEW}"
  if [[ "$CREATE_SHORTCUT" -eq 1 ]]; then
    create_shortcut "$LIVEVIEW"
    echo "Desktop shortcut created."
  fi
  echo "Done."
}

if [[ "$DIALOG" == zenity ]]; then
  # Run install in background; show pulsating progress until finished.
  (
    set +e
    run_install >"$LOG" 2>&1
    echo $? >"${LOG}.rc"
  ) &
  BGPID=$!
  (
    while kill -0 "$BGPID" 2>/dev/null; do
      echo "# Installing autoSAXS (please wait)…"
      sleep 1
    done
  ) | zenity --progress --title="Install autoSAXS" --width=480 --pulsate --auto-close --no-cancel \
    --text="Installing… (this may take several minutes)" || true
  wait "$BGPID" || true
  STATUS="$(cat "${LOG}.rc" 2>/dev/null || echo 1)"
  rm -f "${LOG}.rc"
else
  kdialog --title "Install autoSAXS" --passivepopup "Installing autoSAXS… please wait." 3 || true
  set +e
  run_install >"$LOG" 2>&1
  STATUS=$?
  set -e
fi

LIVEVIEW="$(grep '^LiveView: ' "$LOG" | tail -n1 | sed 's/^LiveView: //' || true)"

# --- Page 4: finish ---
if [[ "$STATUS" -eq 0 ]]; then
  MSG="autoSAXS was installed successfully."
  if [[ "$CREATE_SHORTCUT" -eq 1 ]]; then
    MSG="${MSG}\n\nA Desktop shortcut GUISAXS-LiveView was created. Double-click it to start."
  fi
  if [[ -n "$LIVEVIEW" ]] && [[ -x "$LIVEVIEW" ]] && question_yesno "${MSG}\n\nOpen GUISAXS-LiveView now?"; then
    nohup "$LIVEVIEW" >/dev/null 2>&1 &
  else
    info "$MSG"
  fi
  rm -f "$LOG"
  exit 0
fi

error "Installation failed.\n\n$(tail -n 40 "$LOG" | sed 's/&/\&amp;/g; s/</\&lt;/g')"
rm -f "$LOG"
exit 1

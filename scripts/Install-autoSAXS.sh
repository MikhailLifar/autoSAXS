#!/usr/bin/env bash
# Install-autoSAXS.sh — beginner installer (Linux). No global Python required for the UI.
# Uses zenity or kdialog. Installs via conda create + pip install "autosaxs[gui]".
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ASSETS_DIR="${SCRIPT_DIR}/assets"
ICON_PNG="${ASSETS_DIR}/autosaxs_icon.png"
ENV_NAME="autosaxs"
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
    command -v conda
    return 0
  fi
  local c
  for c in \
    "${HOME}/miniconda3/bin/conda" \
    "${HOME}/Miniconda3/bin/conda" \
    "${HOME}/anaconda3/bin/conda" \
    "${HOME}/Anaconda3/bin/conda" \
    "/opt/conda/bin/conda" \
    "/usr/local/miniconda3/bin/conda"
  do
    if [[ -x "$c" ]]; then
      printf '%s\n' "$c"
      return 0
    fi
  done
  return 1
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
  if CONDA="$(find_conda)"; then
    if question_yesno "Miniconda / Anaconda found:\n\n${CONDA}\n\nContinue to install options?"; then
      break
    else
      exit 0
    fi
  fi
  if [[ "$DIALOG" == zenity ]]; then
    choice="$(zenity --list --title="Install autoSAXS" --width=480 --height=260 \
      --text="autoSAXS needs Miniconda (a free Python toolbox).\nIt was not found on this computer.\n\nInstall Miniconda with the official installer, then click Retry." \
      --column="Action" "Open Miniconda download page" "Retry" "Exit" \
      || true)"
    case "$choice" in
      "Open Miniconda download page")
        (xdg-open "$MINICONDA_URL" >/dev/null 2>&1 || true)
        ;;
      "Retry") continue ;;
      *) exit 1 ;;
    esac
  else
    if kdialog --title "Install autoSAXS" --yesno \
      "autoSAXS needs Miniconda (not found).\n\nOpen the Miniconda download page?"; then
      xdg-open "$MINICONDA_URL" >/dev/null 2>&1 || true
    fi
    if ! kdialog --title "Install autoSAXS" --yesno "Retry conda search?"; then
      exit 1
    fi
  fi
done

# --- Page 2: options (Create Desktop shortcut) ---
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
    echo "Creating environment ${ENV_NAME} (python 3.12)…"
    "${CONDA}" create -n "${ENV_NAME}" python=3.12 pip -y
  else
    echo "Environment ${ENV_NAME} already exists — upgrading package…"
  fi
  echo "Installing ${PIP_SPEC}…"
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

#!/usr/bin/env bash
# Install-autoSAXS.sh — beginner installer (Linux). No global Python required for the UI.
# Uses zenity or kdialog. Installs via conda create + pip install stable (PyPI) or nightbuilt (GitHub).
#
# Happy path (conda already found): Continue → pick install preset → install.
# Optional: AUTOSAXS_INSTALLER_DRY_RUN=1 or --smoke-ui stops before conda work (UI smoke only).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ASSETS_DIR="${SCRIPT_DIR}/assets"
ICON_PNG="${ASSETS_DIR}/autosaxs_icon.png"
ENV_NAME="autosaxs"
DEFAULT_ENV_NAME="autosaxs"
# Keep in sync with autosaxs.cli.package_update
PIP_SPEC_STABLE="autosaxs[gui]"
PIP_SPEC_NIGHTBUILT="autosaxs[gui] @ git+https://github.com/MikhailLifar/autoSAXS.git"
PIP_SPEC="${PIP_SPEC_STABLE}"
INSTALL_SOURCE="stable"
INSTALL_SOURCE_LABEL="stable (PyPI)"
CREATE_SHORTCUT=1
MINICONDA_URL="https://docs.anaconda.com/miniconda/miniconda-install/"
ATSAS_URL="https://www.embl-hamburg.de/biosaxs/download.html"
GIT_URL="https://git-scm.com/download/linux"

DRY_RUN=0
if [[ "${1:-}" == "--smoke-ui" ]] || [[ "${AUTOSAXS_INSTALLER_DRY_RUN:-}" == "1" ]]; then
  DRY_RUN=1
fi

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

# Zenity list rows get crushed when --text is long and --height is modest.
# Size the window so short option lists show every row without a scrollbar.
zenity_list_height_for_rows() {
  local n_rows="$1"
  # Chrome + short caption + ~36px per row + padding
  echo $(( 220 + n_rows * 36 ))
}

prereq_status_line() {
  local git_status="not found"
  local atsas_status="not found (optional)"
  if command -v git >/dev/null 2>&1; then
    git_status="found"
  fi
  if command -v dammif >/dev/null 2>&1; then
    atsas_status="found (optional)"
  fi
  printf 'Git: %s · ATSAS: %s' "$git_status" "$atsas_status"
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

# Secondary menu when the user wants links / another conda folder.
show_conda_more_options() {
  local status
  status="$(prereq_status_line)"
  local text="Miniconda is ready at:
${CONDA}

${status}

Pick an action:"
  local choice=""
  if [[ "$DIALOG" == zenity ]]; then
    local h
    h="$(zenity_list_height_for_rows 4)"
    choice="$(zenity --list --title="Install autoSAXS" --width=560 --height="$h" --hide-header \
      --text="${text}" \
      --ok-label="OK" --cancel-label="Back" \
      --column="Action" \
      "Open Git download page" \
      "Open ATSAS download page" \
      "Choose a different conda folder…" \
      "Exit" \
      || true)"
    case "$choice" in
      "Open Git download page")
        (xdg-open "$GIT_URL" >/dev/null 2>&1 || true)
        show_conda_more_options
        return $?
        ;;
      "Open ATSAS download page")
        (xdg-open "$ATSAS_URL" >/dev/null 2>&1 || true)
        show_conda_more_options
        return $?
        ;;
      "Choose a different conda folder…")
        CONDA=""
        if prompt_manual_conda_dir; then
          return 0
        fi
        return 1
        ;;
      "Exit") return 1 ;;
      *) return 1 ;;
    esac
  else
    choice="$(kdialog --title "Install autoSAXS" --menu \
      "${text}" \
      git "Open Git download page" \
      atsas "Open ATSAS download page" \
      manual "Choose a different conda folder…" \
      exit "Exit" \
      git 2>/dev/null || true)"
    case "$choice" in
      git)
        xdg-open "$GIT_URL" >/dev/null 2>&1 || true
        show_conda_more_options
        return $?
        ;;
      atsas)
        xdg-open "$ATSAS_URL" >/dev/null 2>&1 || true
        show_conda_more_options
        return $?
        ;;
      manual)
        CONDA=""
        if prompt_manual_conda_dir; then
          return 0
        fi
        return 1
        ;;
      *) return 1 ;;
    esac
  fi
}

confirm_conda_choice() {
  local status
  status="$(prereq_status_line)"
  local text="Miniconda is ready:
${CONDA}

${status}

Only Miniconda is required. Continue?"

  if [[ "$DIALOG" == zenity ]]; then
    local out="" rc=0
    set +e
    out="$(zenity --question --title="Install autoSAXS" --width=520 \
      --text="${text}" \
      --ok-label="Continue" --cancel-label="Exit" \
      --extra-button="More options…" 2>/dev/null)"
    rc=$?
    set -e
    # 0 = Continue; 1 + stdout = extra button; 1 empty = Exit/Cancel
    if [[ "$rc" -eq 0 ]]; then
      return 0
    fi
    if [[ "$out" == "More options…" ]]; then
      if show_conda_more_options; then
        confirm_conda_choice
        return $?
      fi
      confirm_conda_choice
      return $?
    fi
    return 1
  else
    if kdialog --title "Install autoSAXS" --yesnocancel "${text}" \
      --yes-label "Continue" --no-label "More options…" --cancel-label "Exit" 2>/dev/null; then
      return 0
    else
      local krc=$?
      if [[ "$krc" -eq 1 ]]; then
        if show_conda_more_options; then
          confirm_conda_choice
          return $?
        fi
        confirm_conda_choice
        return $?
      fi
      return 1
    fi
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
  local status
  status="$(prereq_status_line)"
  local text="autoSAXS needs Miniconda (a free Python toolbox).
It was not found automatically.

${status}"

  local choice=""
  if [[ "$DIALOG" == zenity ]]; then
    local h
    h="$(zenity_list_height_for_rows 5)"
    choice="$(zenity --list --title="Install autoSAXS" --width=560 --height="$h" --hide-header \
      --text="${text}" \
      --ok-label="OK" --cancel-label="Exit" \
      --column="Action" \
      "Open Miniconda download page" \
      "Enter conda directory path…" \
      "Retry search" \
      "Open Git download page" \
      "Open ATSAS download page" \
      || true)"
    case "$choice" in
      "Open Miniconda download page")
        (xdg-open "$MINICONDA_URL" >/dev/null 2>&1 || true)
        ;;
      "Open Git download page")
        (xdg-open "$GIT_URL" >/dev/null 2>&1 || true)
        ;;
      "Open ATSAS download page")
        (xdg-open "$ATSAS_URL" >/dev/null 2>&1 || true)
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
      "${text}" \
      download "Open Miniconda download page" \
      manual "Enter conda directory path…" \
      retry "Retry search" \
      git "Open Git download page" \
      atsas "Open ATSAS download page" \
      download 2>/dev/null || true)"
    case "$choice" in
      manual)
        if prompt_manual_conda_dir; then
          return 0
        fi
        ;;
      download)
        xdg-open "$MINICONDA_URL" >/dev/null 2>&1 || true
        ;;
      git)
        xdg-open "$GIT_URL" >/dev/null 2>&1 || true
        ;;
      atsas)
        xdg-open "$ATSAS_URL" >/dev/null 2>&1 || true
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
        --text="Conda environment name:" \
        --entry-text="${DEFAULT_ENV_NAME}" 2>/dev/null || true)"
    else
      name="$(kdialog --title "Install autoSAXS" --inputbox "Conda environment name:" "${DEFAULT_ENV_NAME}" 2>/dev/null || true)"
    fi
    [[ -z "$name" ]] && return 1
    if validate_env_name "$name"; then
      ENV_NAME="$name"
      return 0
    fi
    error "Invalid environment name. Use letters, numbers, dots, hyphens, and underscores (for example: autosaxs)."
  done
}

apply_install_preset() {
  local preset="$1"
  case "$preset" in
    "Stable (PyPI) + Desktop shortcut — recommended"|"stable_shortcut")
      INSTALL_SOURCE="stable"
      PIP_SPEC="${PIP_SPEC_STABLE}"
      INSTALL_SOURCE_LABEL="stable (PyPI)"
      CREATE_SHORTCUT=1
      ;;
    "Stable (PyPI), no Desktop shortcut"|"stable_noshortcut")
      INSTALL_SOURCE="stable"
      PIP_SPEC="${PIP_SPEC_STABLE}"
      INSTALL_SOURCE_LABEL="stable (PyPI)"
      CREATE_SHORTCUT=0
      ;;
    "Nightbuilt (GitHub) + Desktop shortcut"|"night_shortcut")
      INSTALL_SOURCE="nightbuilt"
      PIP_SPEC="${PIP_SPEC_NIGHTBUILT}"
      INSTALL_SOURCE_LABEL="nightbuilt (GitHub)"
      CREATE_SHORTCUT=1
      ;;
    "Nightbuilt (GitHub), no Desktop shortcut"|"night_noshortcut")
      INSTALL_SOURCE="nightbuilt"
      PIP_SPEC="${PIP_SPEC_NIGHTBUILT}"
      INSTALL_SOURCE_LABEL="nightbuilt (GitHub)"
      CREATE_SHORTCUT=0
      ;;
    *)
      return 1
      ;;
  esac
  return 0
}

# One screen: all install presets visible (no scrollbar), OK starts install.
prompt_install_options() {
  ENV_NAME="${DEFAULT_ENV_NAME}"
  local choice=""
  local text="Install into conda environment “${DEFAULT_ENV_NAME}”.

All options are listed below — pick one, then click Install:"

  if [[ "$DIALOG" == zenity ]]; then
    local h
    h="$(zenity_list_height_for_rows 5)"
    choice="$(zenity --list --radiolist --title="Install autoSAXS" --width=580 --height="$h" --hide-header \
      --text="${text}" \
      --ok-label="Install" --cancel-label="Cancel" \
      --column="Select" --column="Option" \
      TRUE "Stable (PyPI) + Desktop shortcut — recommended" \
      FALSE "Stable (PyPI), no Desktop shortcut" \
      FALSE "Nightbuilt (GitHub) + Desktop shortcut" \
      FALSE "Nightbuilt (GitHub), no Desktop shortcut" \
      FALSE "Customize environment name…" \
      2>/dev/null || true)"
    [[ -z "$choice" ]] && exit 0
    if [[ "$choice" == "Customize environment name…" ]]; then
      prompt_env_name || exit 0
      text="Install into conda environment “${ENV_NAME}”.

Pick a package and shortcut preference:"
      h="$(zenity_list_height_for_rows 4)"
      choice="$(zenity --list --radiolist --title="Install autoSAXS" --width=580 --height="$h" --hide-header \
        --text="${text}" \
        --ok-label="Install" --cancel-label="Cancel" \
        --column="Select" --column="Option" \
        TRUE "Stable (PyPI) + Desktop shortcut — recommended" \
        FALSE "Stable (PyPI), no Desktop shortcut" \
        FALSE "Nightbuilt (GitHub) + Desktop shortcut" \
        FALSE "Nightbuilt (GitHub), no Desktop shortcut" \
        2>/dev/null || true)"
      [[ -z "$choice" ]] && exit 0
    fi
    apply_install_preset "$choice" || exit 0
  else
    choice="$(kdialog --title "Install autoSAXS" --radiolist \
      "${text}" \
      stable_shortcut "Stable (PyPI) + Desktop shortcut — recommended" on \
      stable_noshortcut "Stable (PyPI), no Desktop shortcut" off \
      night_shortcut "Nightbuilt (GitHub) + Desktop shortcut" off \
      night_noshortcut "Nightbuilt (GitHub), no Desktop shortcut" off \
      customize "Customize environment name…" off \
      2>/dev/null || true)"
    [[ -z "$choice" ]] && exit 0
    if [[ "$choice" == "customize" ]]; then
      prompt_env_name || exit 0
      choice="$(kdialog --title "Install autoSAXS" --radiolist \
        "Install into conda environment “${ENV_NAME}”." \
        stable_shortcut "Stable (PyPI) + Desktop shortcut — recommended" on \
        stable_noshortcut "Stable (PyPI), no Desktop shortcut" off \
        night_shortcut "Nightbuilt (GitHub) + Desktop shortcut" off \
        night_noshortcut "Nightbuilt (GitHub), no Desktop shortcut" off \
        2>/dev/null || true)"
      [[ -z "$choice" ]] && exit 0
    fi
    apply_install_preset "$choice" || exit 0
  fi
}

ensure_git_for_nightbuilt() {
  if [[ "$INSTALL_SOURCE" != "nightbuilt" ]]; then
    return 0
  fi
  if command -v git >/dev/null 2>&1; then
    return 0
  fi
  local prefix
  prefix="$("${CONDA}" run -n "${ENV_NAME}" python -c 'import sys; print(sys.prefix)')"
  if [[ -x "${prefix}/bin/git" ]]; then
    return 0
  fi
  echo "Nightbuilt install needs git; installing git into '${ENV_NAME}'..."
  if ! CONDA_ALWAYS_YES=true "${CONDA}" install -n "${ENV_NAME}" git -y; then
    echo "ERROR: could not install git into conda environment '${ENV_NAME}' (required for nightbuilt)." >&2
    return 1
  fi
  if [[ ! -x "${prefix}/bin/git" ]] && ! command -v git >/dev/null 2>&1; then
    echo "ERROR: git is still missing after conda install git." >&2
    return 1
  fi
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

# --- Page 2: install options (single list — all presets visible) ---
prompt_install_options

if [[ "$INSTALL_SOURCE" == "nightbuilt" ]] && ! command -v git >/dev/null 2>&1; then
  if ! question_yesno "Git was not found on PATH.

Nightbuilt installs need git. The installer can install git into the conda environment automatically.

Continue anyway?"; then
    exit 0
  fi
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
  info "Smoke UI OK.

Would install ${INSTALL_SOURCE_LABEL} into conda environment “${ENV_NAME}”
Desktop shortcut: $([[ "$CREATE_SHORTCUT" -eq 1 ]] && echo yes || echo no)

Stopping before conda (dry-run / --smoke-ui)."
  exit 0
fi

# --- Page 3: install ---
LOG="$(mktemp /tmp/autosaxs-install-XXXXXX.log)"
LIVEVIEW=""
STATUS=0

run_install() {
  echo "Using conda: ${CONDA}"
  echo "Install source: ${INSTALL_SOURCE_LABEL}"
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
  ensure_git_for_nightbuilt
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

show_install_log_live() {
  # Run install in background; stream "$LOG" to a visible dialog.
  : >"$LOG"
  (
    set +e
    run_install >>"$LOG" 2>&1
    echo $? >"${LOG}.rc"
  ) &
  local bgpid=$!

  if [[ "$DIALOG" == zenity ]]; then
    (
      while [[ ! -s "$LOG" ]] && kill -0 "$bgpid" 2>/dev/null; do
        sleep 0.2
      done
      # --pid makes tail exit when the installer process ends.
      if tail --help 2>&1 | grep -q -- '--pid'; then
        tail -n +1 -f "$LOG" --pid="$bgpid" 2>/dev/null || true
      else
        while kill -0 "$bgpid" 2>/dev/null; do
          sleep 0.5
        done
        cat "$LOG" 2>/dev/null || true
      fi
      echo ""
      echo "---- finished — click Continue ----"
    ) | zenity --text-info --title="Install autoSAXS - log" --width=760 --height=480 \
      --auto-scroll --ok-label="Continue" 2>/dev/null || true
    wait "$bgpid" || true
  else
    local dbus_ref=""
    dbus_ref="$(kdialog --title "Install autoSAXS" --progressbar "Installing autoSAXS..." 0 2>/dev/null || true)"
    if [[ -n "$dbus_ref" ]]; then
      qdbus $dbus_ref showCancelButton false >/dev/null 2>&1 || true
      while kill -0 "$bgpid" 2>/dev/null; do
        local line=""
        line="$(tail -n 1 "$LOG" 2>/dev/null || true)"
        if [[ -n "$line" ]]; then
          line="${line:0:100}"
          qdbus $dbus_ref setLabelText "$line" >/dev/null 2>&1 || true
        fi
        sleep 0.8
      done
      wait "$bgpid" || true
      qdbus $dbus_ref close >/dev/null 2>&1 || true
    else
      kdialog --title "Install autoSAXS" --passivepopup "Installing autoSAXS... please wait." 5 || true
      wait "$bgpid" || true
    fi
    if [[ -s "$LOG" ]]; then
      kdialog --title "Install autoSAXS - log" --textbox "$LOG" 760 480 2>/dev/null || true
    fi
  fi

  STATUS="$(cat "${LOG}.rc" 2>/dev/null || echo 1)"
  rm -f "${LOG}.rc"
}

show_install_log_live

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

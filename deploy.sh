#!/usr/bin/env bash
/opt/anaconda/bin/pip install "autosaxs[gui] @ git+https://github.com/MikhailLifar/autoSAXS.git" --force-reinstall
/opt/anaconda/bin/autosaxs get-skills -o/etc/opencode/skills

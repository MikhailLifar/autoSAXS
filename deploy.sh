#!/usr/bin/env bash
/opt/anaconda/bin/pip install "autosaxs[gui] @ git+http://hpc.nano.sfedu.ru:8080/mikhail/saxsprocessing.git" --force-reinstall
/opt/anaconda/bin/autosaxs get-skills -o/etc/opencode/skills

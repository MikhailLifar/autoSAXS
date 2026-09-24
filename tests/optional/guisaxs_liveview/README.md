# Exhaustive liveview tests — agent-run only when the matching subsystem
# changed significantly (not on typos, style, help text, or unrelated skills).
#
# Owners (see docs/liveview_architecture.md §1.4):
#   ingest/         — RevisionSettler, RevisionIngress, invalid .dat/.tif (wave 1)
#   preprocessing/  — calib / buffer / Stop–Resume order (wave 1)
#   analysis/       — right presenters, Guinier / GNOM adjust, arming (wave 1)
#   multi_file/     — TREE burst, DnD, Process, poll (wave 2)
#   churn/          — buffer / intake / Stop / mono mid-queue (wave 2)
#   modeling/       — shape / DR child apps, DAMMIF / MIXTURE UI
#
# Commit gate: tests/must-run/guisaxs_liveview/
# Do not add these paths to helpers/run_tests.sh.
#
# Wave 1 (ingest + preprocessing + analysis mono) — prefer one process per owner:
#   GUISAXS_LIVEVIEW_TEST_TIMEOUT=1800
#   for d in ingest preprocessing analysis; do
#     xvfb-run -a python -m pytest tests/optional/guisaxs_liveview/$d \
#       -v --tb=short --import-mode=importlib -s || true
#   done
#
# Wave 2 (TREE multi-file + session churn; no FLAT; no mid-flight calib/mask):
#   GUISAXS_LIVEVIEW_TEST_TIMEOUT=1800
#   for d in multi_file churn; do
#     xvfb-run -a python -m pytest tests/optional/guisaxs_liveview/$d \
#       -v --tb=short --import-mode=importlib -s || true
#   done
#
# Findings from attacks are written to FINDINGS.md (and _findings_accum.jsonl during the run).
# Do not fix product bugs in the same attack pass — report first.

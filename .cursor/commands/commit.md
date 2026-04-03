# commit

The command argument is a commit message.
When the command is invoked, you should follow these steps:
0. IMPORTANT: DO NOT ADD FILES TO GIT. NEVER RUN `git add` (so it is up to user which files to add to Git).
1. Run tests with the project interpreter:
   - `/home/mikl/.conda/envs/LLMAssistant/bin/python /home/mikl/KurchatovCoop/repos/tests/test_skill.py`
2. If tests fail, report on the errors, potential causes, and possible fixes. DON'T TRY TO FIX THE CODE. STOP HERE IF TESTS FAILED (DON'T PROCEED WITH NEXT STEPS).
3. Run real-data tests with the same interpreter:
   - `/home/mikl/.conda/envs/LLMAssistant/bin/python /home/mikl/KurchatovCoop/repos/tests/test_skills_real_data.py`
4. If tests fail, report on the errors, potential causes, and possible fixes. DON'T TRY TO FIX THE CODE. STOP HERE IF THE TEST FAILED (DON'T PROCEED WITH NEXT STEPS).
5. Run GUI tests headless (requires `xvfb` / `xvfb-run` on the system):
   - `cd /home/mikl/KurchatovCoop/repos && xvfb-run -a /home/mikl/.conda/envs/LLMAssistant/bin/python -m pytest tests/test_guisaxs.py -v --tb=short`
6. If tests fail, report on the errors, potential causes, and possible fixes. DON'T TRY TO FIX THE CODE. STOP HERE IF THE TEST FAILED (DON'T PROCEED WITH NEXT STEPS).
7. Update skills documentation:
   - `/home/mikl/.conda/envs/LLMAssistant/bin/python /home/mikl/KurchatovCoop/repos/autosaxs_skills_explained.py`
8. Bump `autosaxs` version (patch bump unless user specifies otherwise) in BOTH places:
   - `/home/mikl/KurchatovCoop/repos/pyproject.toml` `[project].version`
   - `/home/mikl/KurchatovCoop/repos/autosaxs/__init__.py` `__version__ = "..."`
   If dependencies need adjusting, update them in `/home/mikl/KurchatovCoop/repos/pyproject.toml` `[project].dependencies`.
9. Re-install `autosaxs` into the project environment:
   - `/home/mikl/.conda/envs/LLMAssistant/bin/pip install -e /home/mikl/KurchatovCoop/repos`
10. Verify reinstall + version:
   - `/home/mikl/.conda/envs/LLMAssistant/bin/python -c "import autosaxs; print(autosaxs.__version__)"`
   - `/home/mikl/.conda/envs/LLMAssistant/bin/autosaxs --help`
11. Commit + push tracked changes only (NO `git add`):
```bash
cd /home/mikl/KurchatovCoop/repos/ 
git commit -a -m "message from the command argument (use verbatim; do not transform or substitute)"
git push
```

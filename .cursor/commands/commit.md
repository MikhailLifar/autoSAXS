# commit

The command arguments are:
a commit message;
(optional) a specific version to update the autosaxs package to;
Inocation examples:
/commit "commit message"
/commit "commit message" version=new.version.update.to

When the command is invoked, you should follow these steps:

Run `git status`:
```bash
cd /home/mikl/KurchatovCoop/repos
git status
```

Then:
- If there are any untracked files, you MUST STOP the workflow and ask the user which untracked files should be added with `git add`.
- After receiving the user response:
  - If the user wants to add some files, run `git add <paths...>` exactly for those files.
  - If the user wants to add none of them, do not run `git add`. 
  - Continue the workflow.

Run tests (required):
```bash
cd /home/mikl/KurchatovCoop/repos
bash helpers/run_tests.sh
```

Then:
- If the tests script fails (non-zero exit code), read the output (and `repos/commit.log`) and produce a report describing the failures, likely root causes, and possible fixes. DON'T TRY TO FIX THE CODE.
- IF AND ONLY IF ALL TESTS PASS, run the pre-commit updates:
```bash
cd /home/mikl/KurchatovCoop/repos
bash helpers/run_precommit_updates.sh [<new_version_if_provided_in_command_arguments>]
```
- IF AND ONLY IF the pre-commit updates succeed (exit code 0), run git commit and git push (do NOT run git add here):
```bash
cd /home/mikl/KurchatovCoop/repos
git commit -a -m "message from the command argument (use verbatim; do not transform or substitute)"
git push
```

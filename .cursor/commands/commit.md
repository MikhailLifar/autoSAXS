# commit

The command argument is a commit message.
When the command is invoked, you should follow these steps:
Run:
```bash
cd /home/mikl/KurchatovCoop/repos
bash commit.sh "message from the command argument (use verbatim; do not transform or substitute)"
```

Then:
- If the script fails (non-zero exit code), read the output (and `repos/commit.log`) and report on the errors, potential causes, and possible fixes. DON'T TRY TO FIX THE CODE.
- If the script succeeds (exit code 0), do nothing further.

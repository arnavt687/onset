# Working on Onset (for parallel sessions)

The project lives in WSL Ubuntu at `~/onset` and is reached from Windows as
`\\wsl.localhost\Ubuntu\home\arnav\onset`. Commands must run inside Ubuntu.

## Your worktree

```bash
# once, inside Ubuntu (piece = face | arms | speech | decide | harness | chert-api)
cd ~/onset && git fetch -q origin && git worktree add ~/onset-wt/<piece> -b piece/<piece> origin/main
```

Edit files at `\\wsl.localhost\Ubuntu\home\arnav\onset-wt\<piece>\...` with the file tools.
Use the shared virtualenv and models; do not create your own and do not `pip install` into it.
If you need a new package, write it in your PR description and `notes/<piece>.md`; the main
session installs it.

- Python: `~/onset/.venv/bin/python`  (3.12, packages from `requirements.txt` plus mediapipe, pytest)
- Models: `~/onset/models/face_landmarker.task`, `~/onset/models/pose_landmarker_full.task`
- Keys: `~/onset/.env.local` (load with `dotenv`; never print, copy or commit values)

## Running commands from Windows

Quoting through PowerShell and Git Bash breaks `$` and paths. Write a script file and run it:

```powershell
wsl -d Ubuntu -- bash /home/arnav/onset-wt/<piece>/notes/run.sh
```

From the Bash tool, prefix `MSYS_NO_PATHCONV=1` before `wsl.exe` so Linux paths are not rewritten.

## Tests

```bash
cd ~/onset-wt/<piece> && ~/onset/.venv/bin/python -m pytest -q tests/test_<piece>.py
```

## Commit, push, PR (inside Ubuntu; `gh` is signed in)

```bash
git add <your files> && git commit -m "<piece>: <what>"   # end the message with the Co-Authored-By line
git push -u origin piece/<piece>
gh pr create --draft --base main --title "<piece>: <summary>" --body-file notes/<piece>.md
```

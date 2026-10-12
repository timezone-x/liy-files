# Operation Halcyon: Run Sheet

Everything runs as root on your server. One file: `liy.py`. Needs only Python 3, which comes with Ubuntu Server.

---

## Before the session


1. **Copy the script over:**
   ```
   scp liy.py user@<server-ip>:~
   ```
1. **Check password SSH is on.** Players log in with passwords; setup warns you if it's off.
1. **Dry run:** make one test player and play a tier over real SSH. Then `reset` them, or `teardown`.

---

## Setup

```
sudo python3 liy.py setup students.txt     # one username per line
sudo python3 liy.py setup                  # or type names in, blank line to finish
```
- Logins print at the end. To see them again: `sudo python3 liy.py creds`
- Latecomers: run setup again. Existing players and real accounts on the server are skipped.

---

## During the session

**Start the game server and leave it running** (second SSH session or tmux):
```
sudo python3 liy.py serve
```
- Keeps every player's `~/STATUS.txt` updated (every 10s)
- Records completion times
- Web leaderboard on the projector: `http://<server-ip>:8080`
- Options: `--port 9000`, `--interval 5`

**Terminal leaderboard** (optional, works on its own too):
```
sudo python3 liy.py board
```

**Someone stuck or confused:**
```
sudo python3 liy.py debug <user>
```
Shows their codes, what's in their dead drop, which cleanup files are left, and which hints they've read.

**Someone broke something:**
```
sudo python3 liy.py reset <user>            # everything back to the start
sudo python3 liy.py reset <user> silver     # just one tier
```

---

## After

```
sudo python3 liy.py teardown
```
Type `yes`. It removes all players, their home folders and every game file.

---

## What players see

```
~/README.txt          how it works
~/briefings/          1-bronze ... 4-platinum (start anywhere)
~/deaddrop/           submit: touch ~/deaddrop/<code>
~/hints/              2 per tier, 1 for platinum
~/STATUS.txt          live checklist (cat it)
```

| Tier | Story | Skills | Code looks like |
|---|---|---|---|
| Bronze | Infiltration | hidden files, decoys, `ls -la` | `cedar-6013` |
| Silver | The Logs | `grep` twice, 3 folders deep | `tundra-4139` |
| Gold | The Shredded File | nesting, hidden folder, ordering | `raven-glacier-meadow` |
| Platinum | The Payload | `grep -r`, reading `ls -l` dates | `tempest-1247` |

## Scoring

- **A tier counts** when its correct code is in the dead drop.
- **Messy agent:** the code is in, but the "delete me" files are still there. The tier still counts, but it's flagged on the board and in `STATUS.txt`.
- **Hints:** the board shows how many each player read. It's tracked by whether the file was opened, with no penalty.
- **Order:** highest tier first, then earliest finish.

## Things to know

- **Paths differ per player.** File names, folders, decoys and codes are randomised for each person, so answers can't be shared across the room.
- **Wrong codes** show in `STATUS.txt` with the exact `rm` command to remove them.
- **Spotting pieces by size:** gold pieces are slightly bigger than the junk files, so `ls -l` gives them away. Consider it a reward for anyone who notices.

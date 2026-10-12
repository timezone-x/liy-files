#!/usr/bin/env python3
"""
Operation Halcyon -- LIY flag hunt, round 2.

Run everything as root on the game server:

  sudo python3 liy.py setup [students.txt]   create players (from a file, or type names in)
  sudo python3 liy.py serve [--port 8080]    keeps STATUS.txt updated + web leaderboard (leave running)
  sudo python3 liy.py board                  leaderboard in the terminal
  sudo python3 liy.py creds                  reprint everyone's logins
  sudo python3 liy.py debug <user>           exactly where a player is up to
  sudo python3 liy.py reset <user> [tier]    put a player (or just one tier) back to the start
  sudo python3 liy.py teardown               delete all players and game files

Standard library only. No internet needed.
"""

import argparse
import fcntl
import html
import http.server
import json
import os
import pwd
import random
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime

# --------------------------------------------------------------------------- config

STATE_DIR = "/root/liy-halcyon"
GAME_FILE = os.path.join(STATE_DIR, "game.json")
PROGRESS_FILE = os.path.join(STATE_DIR, "progress.json")
LOCK_FILE = os.path.join(STATE_DIR, ".lock")

TIERS = ["bronze", "silver", "gold", "platinum"]
TIER_NUM = {t: i + 1 for i, t in enumerate(TIERS)}
TIER_TITLE = {
    "bronze": "Infiltration",
    "silver": "The Logs",
    "gold": "The Shredded File",
    "platinum": "The Payload",
}
HINTS_PER_TIER = {"bronze": 2, "silver": 2, "gold": 2, "platinum": 1}

USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")

WORDS = [
    "harbour", "granite", "falcon", "cobalt", "ember", "quartz", "maple", "raven",
    "tundra", "comet", "violet", "badger", "cedar", "walrus", "nimbus", "saffron",
    "lantern", "orchid", "copper", "meadow", "glacier", "thistle", "juniper", "hollow",
    "canyon", "summit", "beacon", "willow", "marble", "ripple", "sparrow", "onyx",
]
CODENAMES = [
    "nightjar", "kestrel", "basilisk", "monolith", "sundial", "tempest",
    "obsidian", "aurora", "citadel", "vanguard", "mirage", "halberd",
]
STAFF = [
    "d.okafor", "k.morrow", "j.tanaka", "r.silva", "m.novak", "a.kowalski",
    "s.patel", "t.nguyen", "l.fischer", "b.osei", "c.reyes", "e.lindqvist",
    "h.brennan", "p.duval", "n.haddad", "v.marsh",
]
ROLES = [
    "Accountant", "Receptionist", "Security", "Facilities", "HR",
    "Marketing", "Intern", "Developer", "Payroll", "Legal",
]
MONTHS = ["january", "february", "march", "april", "may", "june", "july",
          "august", "september", "october", "november", "december"]
DOORS = ["MAIN-ENTRY", "LOADING-DOCK", "LEVEL-2", "LEVEL-3", "SERVER-ROOM",
         "CAFETERIA", "CARPARK", "ROOF-ACCESS"]
EVENTS = ["ACCESS GRANTED"] * 8 + ["ACCESS DENIED", "DOOR HELD OPEN", "BADGE EXPIRED"]

DELETE_ME = "-- Delete me once you've read me --"

rng = random.SystemRandom()


# --------------------------------------------------------------------------- helpers

def need_root():
    if os.geteuid() != 0:
        sys.exit("Run this as root:  sudo python3 liy.py ...")


class Locked:
    """Exclusive lock so serve, board and reset don't trip over each other."""

    def __enter__(self):
        os.makedirs(STATE_DIR, mode=0o700, exist_ok=True)
        self.fd = os.open(LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        fcntl.flock(self.fd, fcntl.LOCK_UN)
        os.close(self.fd)


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def save_json(path, data):
    os.makedirs(STATE_DIR, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=STATE_DIR)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=1)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def load_game():
    return load_json(GAME_FILE, {"players": {}, "parents": []})


def remove_path(p):
    """Delete a file/dir/symlink without ever following a symlink."""
    if os.path.islink(p) or (os.path.lexists(p) and not os.path.isdir(p)):
        os.unlink(p)
    elif os.path.isdir(p):
        shutil.rmtree(p)


def ensure_root_parents(parent):
    """Create root-owned parent dirs (e.g. /srv/maintenance). Refuses anything user-controlled."""
    created = []
    path = "/"
    for part in parent.strip("/").split("/"):
        path = os.path.join(path, part)
        if os.path.lexists(path):
            st = os.lstat(path)
            if os.path.islink(path) or st.st_uid != 0:
                raise RuntimeError(f"Refusing to use {path}: not a root-owned directory")
        else:
            os.mkdir(path, 0o755)
            os.chmod(path, 0o755)
            created.append(path)
    return created


def norm_code(name):
    n = name.strip().lower()
    if n.endswith(".txt"):
        n = n[:-4]
    return n


def fmt_time(ts):
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S") if ts else ""


# --------------------------------------------------------------------------- building a player's game

class Plan:
    def __init__(self, user, home):
        self.user = user
        self.home = home
        self.entries = []
        self.tiers = {t: {"code": None, "cleanup": [], "roots": [], "home": []} for t in TIERS}

    def h(self, rel):
        return os.path.join(self.home, rel)

    def root(self, tier, path):
        self.entries.append({"tier": tier, "kind": "root", "path": path})
        self.tiers[tier]["roots"].append(path)

    def dir(self, tier, path, mode=0o755):
        self.entries.append({"tier": tier, "kind": "dir", "path": path, "mode": mode})

    def file(self, tier, path, content, mtime=None, hint=False):
        self.entries.append({"tier": tier, "kind": "file", "path": path, "content": content,
                             "mode": 0o644, "mtime": mtime, "hint": hint})

    def home_file(self, tier, rel, content, hint=False):
        self.file(tier, self.h(rel), content, hint=hint)
        if tier != "common":
            self.tiers[tier]["home"].append(rel)

    def as_dict(self):
        return {"entries": self.entries, "tiers": self.tiers}


def make_code(used):
    while True:
        c = f"{rng.choice(WORDS)}-{rng.randint(1000, 9999)}"
        if c not in used:
            used.add(c)
            return c


def log_line(t, door, badge, event):
    return f"{t:%Y-%m-%d %H:%M:%S}  door={door:<13} badge={badge:<12} {event}"


def random_log(n, badges, month=None):
    rows = []
    for _ in range(n):
        m = month or rng.randint(1, 12)
        t = datetime(2025, m, rng.randint(1, 28), rng.randint(6, 22), rng.randint(0, 59), rng.randint(0, 59))
        rows.append((t, log_line(t, rng.choice(DOORS), rng.choice(badges), rng.choice(EVENTS))))
    return rows


def build_plan(user, home):
    p = Plan(user, home)
    used = set()

    # ---- common ------------------------------------------------------------
    p.dir("common", p.h("briefings"))
    p.dir("common", p.h("deaddrop"))
    p.dir("common", p.h("hints"))
    p.home_file("common", "README.txt", README_TXT)

    # ---- bronze: Infiltration ------------------------------------------------
    t = p.tiers["bronze"]
    t["code"] = make_code(used)
    note = rng.choice([".contact", ".memo", ".handover", ".note-from-k"])
    records = os.path.join(rng.choice(["/srv/maintenance", "/var/tmp/facilities", "/opt/halcyon/records"]), user)
    sub = rng.choice(["archive", "old", "2025", "filed"])
    names = rng.sample(list(BRONZE_DECOYS), len(BRONZE_DECOYS))
    moved_name = rng.choice(["facilities-memo.txt", "notice.txt", "memo.txt"])
    top_names, sub_names = names[:rng.randint(3, 4)] + [moved_name], names[6:8]
    hidden = rng.random() < 0.5
    if hidden:
        real_name = rng.choice([".current-access.txt", ".access-sheet.txt", ".keycard-codes.txt"])
    else:
        real_name = rng.choice(["access-sheet.txt", "keycard-codes.txt", "door-codes.txt"])
    real_path = os.path.join(records, sub, real_name)
    expired = make_code(used)

    p.root("bronze", records)
    rng.shuffle(top_names)
    for n in top_names:
        text = BRONZE_MOVED.format(sub=sub) if n == moved_name else BRONZE_DECOYS[n]
        p.file("bronze", os.path.join(records, n), text + "\n")
    p.dir("bronze", os.path.join(records, sub))
    p.file("bronze", os.path.join(records, sub, "old-access-codes.txt"),
           f"OLD ACCESS CODE: {expired}\n(EXPIRED. Changed last month. Do not use.)\n")
    for i, n in enumerate(sub_names):
        text = BRONZE_HIDDEN_HINT if (hidden and i == 0) else BRONZE_DECOYS[n]
        p.file("bronze", os.path.join(records, sub, n), text + "\n")
    p.file("bronze", real_path, BRONZE_REAL.format(code=t["code"], real=real_path, note=note))
    p.home_file("bronze", note, BRONZE_NOTE.format(records=records))
    p.home_file("bronze", "briefings/1-bronze.txt", BRIEF_BRONZE)
    p.home_file("bronze", "hints/bronze-1.txt", HINT_BRONZE_1, hint=True)
    p.home_file("bronze", "hints/bronze-2.txt", HINT_BRONZE_2.format(note=note, records=records), hint=True)
    t["cleanup"] = [p.h(note), real_path]

    # ---- silver: The Logs ----------------------------------------------------
    t = p.tiers["silver"]
    t["code"] = make_code(used)
    logs = f"/var/log/halcyon/{user}"
    culprit = rng.choice(STAFF)
    others = [s for s in STAFF if s != culprit]
    mi = rng.randrange(12)
    month = MONTHS[mi]
    hide_dir = os.path.join(logs, "archive", "2025", month)
    original = os.path.join(hide_dir, "original-access.log")

    rows = random_log(399, STAFF)
    tt = datetime(2025, mi + 1, rng.randint(1, 28), 23, rng.randint(0, 59), rng.randint(0, 59))
    rows.append((tt, log_line(tt, "SERVER-ROOM", culprit, f"TAMPERED -- originals moved to {hide_dir}/")))
    rows.sort()
    access = "\n".join(r for _, r in rows) + "\n"

    orig = random_log(199, others, month=mi + 1)
    ot = datetime(2025, mi + 1, rng.randint(1, 28), 2, rng.randint(0, 59), rng.randint(0, 59))
    orig.append((ot, log_line(ot, "SERVER-ROOM", culprit, f"OVERRIDE CODE: {t['code']}")))
    orig.sort()
    original_text = "\n".join(r for _, r in orig) + "\n"

    p.root("silver", logs)
    p.file("silver", os.path.join(logs, "access.log"), access)
    p.file("silver", os.path.join(logs, "system.log"),
           "\n".join(f"2025-0{rng.randint(1, 9)}-1{rng.randint(0, 9)} 0{rng.randint(0, 9)}:00:00  backup completed OK"
                     for _ in range(40)) + "\n")
    p.dir("silver", os.path.join(logs, "archive"))
    p.dir("silver", os.path.join(logs, "archive", "2024"))
    p.file("silver", os.path.join(logs, "archive", "2024", "access-2024.log"),
           "\n".join(r for _, r in random_log(60, others)).replace("2025", "2024") + "\n")
    p.dir("silver", os.path.join(logs, "archive", "2025"))
    for m in rng.sample([x for x in MONTHS if x != month], 2):
        d = os.path.join(logs, "archive", "2025", m)
        p.dir("silver", d)
        p.file("silver", os.path.join(d, "access.log"),
               "\n".join(r for _, r in random_log(50, others, month=MONTHS.index(m) + 1)) + "\n")
    p.dir("silver", hide_dir)
    p.file("silver", original, original_text)
    p.file("silver", os.path.join(hide_dir, "backup-access.log"),
           "\n".join(r for _, r in random_log(80, others, month=mi + 1)) + "\n")
    p.home_file("silver", "briefings/2-silver.txt", BRIEF_SILVER.format(logs=logs))
    p.home_file("silver", "hints/silver-1.txt", HINT_SILVER_1.format(logs=logs), hint=True)
    p.home_file("silver", "hints/silver-2.txt", HINT_SILVER_2, hint=True)
    t["cleanup"] = [os.path.join(logs, "access.log"), original]

    # ---- gold: The Shredded File ---------------------------------------------
    t = p.tiers["gold"]
    words = rng.sample([w for w in WORDS if not any(w in c for c in used)], 3)
    t["code"] = "-".join(words)
    bin_root = f"/srv/recycling/{user}"
    bins = [f"bin-{i}" for i in range(1, 5)]
    b_shallow, b_deep, b_hidden, b_spare = rng.sample(bins, 4)
    days = rng.sample(["monday", "tuesday", "wednesday", "thursday", "friday"], 2)
    deep_month = rng.choice(MONTHS)
    locations = [
        os.path.join(bin_root, b_shallow, days[0]),
        os.path.join(bin_root, b_deep, "archive", "2025", deep_month),
        os.path.join(bin_root, b_hidden, ".overflow"),
    ]
    order = [0, 1, 2]
    rng.shuffle(order)  # which piece number lives where
    scraps = rng.sample(range(1000, 9999), 40)
    junk = list(GOLD_JUNK)

    def scrap():
        return f"scrap-{scraps.pop()}.txt"

    p.root("gold", bin_root)
    for b in bins:
        p.dir("gold", os.path.join(bin_root, b))
        for _ in range(rng.randint(1, 2)):
            p.file("gold", os.path.join(bin_root, b, scrap()), rng.choice(junk) + "\n")
    for d in [os.path.join(bin_root, b_shallow, days[0]), os.path.join(bin_root, b_shallow, days[1]),
              os.path.join(bin_root, b_deep, "archive"), os.path.join(bin_root, b_deep, "archive", "2024"),
              os.path.join(bin_root, b_deep, "archive", "2025"), locations[1],
              os.path.join(bin_root, b_hidden, ".overflow"), os.path.join(bin_root, b_spare, "misc")]:
        p.dir("gold", d)
    for d in [os.path.join(bin_root, b_shallow, days[1]), os.path.join(bin_root, b_deep, "archive", "2024"),
              os.path.join(bin_root, b_spare, "misc"), locations[1]]:
        p.file("gold", os.path.join(d, scrap()), rng.choice(junk) + "\n")
    pieces = []
    for piece_no, loc_i in enumerate(order, start=1):
        path = os.path.join(locations[loc_i], scrap())
        p.file("gold", path, GOLD_PIECE.format(n=piece_no, word=words[piece_no - 1]))
        pieces.append(path)
    p.home_file("gold", "briefings/3-gold.txt", BRIEF_GOLD.format(bins=bin_root))
    p.home_file("gold", "hints/gold-1.txt", HINT_GOLD_1, hint=True)
    p.home_file("gold", "hints/gold-2.txt", HINT_GOLD_2, hint=True)
    t["cleanup"] = pieces

    # ---- platinum: The Payload -----------------------------------------------
    t = p.tiers["platinum"]
    cnames = rng.sample(CODENAMES, 4)
    codes = [f"{c}-{rng.randint(1000, 9999)}" for c in cnames]
    t["code"] = codes[0]
    staff_root = f"/srv/halcyon/{user}"
    staff_dir = os.path.join(staff_root, "staff")
    proj_root = f"/opt/halcyon/projects/{user}"
    internal = os.path.join(proj_root, ".internal")
    people = rng.sample(STAFF, 8)
    sysadmin = people[0]
    roles = rng.sample(ROLES, 7)
    staff_roles = {sysadmin: "Systems Administrator"}
    staff_roles.update({person: roles[i] for i, person in enumerate(people[1:])})
    listing = sorted(people)

    projects = [f"prj-{n}" for n in rng.sample(range(100, 999), 12)]
    approved = projects[0]
    rng.shuffle(projects)
    # midday, so a timezone difference can never push the date shown by `ls -l` onto another day
    dates = rng.sample([datetime(2026, m, d, 12, rng.randint(0, 59))
                        for m in range(1, 9) for d in (3, 9, 14, 21, 27)], 4)
    real_date = dates[0]
    sheet_names = [f"codename-v{i}.txt" for i in range(1, 5)]
    rng.shuffle(sheet_names)
    history = os.path.join(staff_dir, sysadmin, ".bash_history")

    p.root("platinum", staff_root)
    p.dir("platinum", staff_dir)
    p.file("platinum", os.path.join(staff_dir, "staff-list.txt"),
           "HALCYON SYSTEMS -- STAFF DIRECTORY\n\nNAME          ROLE\n" +
           "".join(f"{n:<13} {staff_roles[n]}\n" for n in listing))
    for person in people:
        d = os.path.join(staff_dir, person)
        p.dir("platinum", d)
        p.file("platinum", os.path.join(d, "todo.txt"), rng.choice(STAFF_TODOS) + "\n")
        if person == sysadmin:
            p.file("platinum", history, SYSADMIN_HISTORY.format(
                internal=internal, date=f"{real_date.day} {real_date:%B}"))
        else:
            p.file("platinum", os.path.join(d, ".bash_history"),
                   "\n".join(rng.sample(BORING_HISTORY, 7)) + "\n")
    p.root("platinum", proj_root)
    p.dir("platinum", internal)
    for prj in projects:
        d = os.path.join(internal, prj)
        p.dir("platinum", d)
        if prj == approved:
            p.file("platinum", os.path.join(d, "README.txt"), PRJ_APPROVED.format(prj=prj, lead=sysadmin))
            p.dir("platinum", os.path.join(d, "docs"))
            for i, sheet in enumerate(sheet_names):
                p.file("platinum", os.path.join(d, "docs", sheet), PLAT_SHEET.format(code=codes[i]),
                       mtime=dates[i].timestamp())
        else:
            p.file("platinum", os.path.join(d, "README.txt"), PRJ_DENIED.format(
                prj=prj, lead=rng.choice(people[1:]),
                status=rng.choice(["Archived", "Cancelled", "On hold", "Proposed"])))
    real_sheet = os.path.join(internal, approved, "docs", sheet_names[0])
    p.home_file("platinum", "briefings/4-platinum.txt", BRIEF_PLATINUM.format(staff=staff_dir))
    p.home_file("platinum", "hints/platinum-1.txt", HINT_PLATINUM_1, hint=True)
    t["cleanup"] = [real_sheet, history]

    return p


def apply_entries(entries, user):
    pw = pwd.getpwnam(user)
    uid, gid = pw.pw_uid, pw.pw_gid
    created = []
    for e in entries:
        path = e["path"]
        if e["kind"] == "root":
            created += ensure_root_parents(os.path.dirname(path))
            remove_path(path)
            os.mkdir(path, 0o700)
            os.chown(path, uid, gid)
            os.chmod(path, 0o700)
        elif e["kind"] == "dir":
            if os.path.lexists(path) and (os.path.islink(path) or not os.path.isdir(path)):
                remove_path(path)
            if not os.path.isdir(path):
                os.mkdir(path, e["mode"])
            os.chown(path, uid, gid, follow_symlinks=False)
            os.chmod(path, e["mode"])
        else:
            remove_path(path)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(e["content"])
                os.fchown(f.fileno(), uid, gid)
                os.fchmod(f.fileno(), e["mode"])
            if e.get("mtime"):
                os.utime(path, (e["mtime"], e["mtime"]), follow_symlinks=False)
            if e.get("hint"):
                # atime older than mtime => the first `cat` bumps atime, so we know it was read
                st = os.lstat(path)
                os.utime(path, (st.st_mtime - 3600, st.st_mtime), follow_symlinks=False)
    return created


# --------------------------------------------------------------------------- scanning progress

def scan_player(user, info):
    home = info["home"]
    tiers = info["plan"]["tiers"]
    out = {"tiers": {}, "unknown": [], "hints": [], "deaddrop_ok": True}

    drop = os.path.join(home, "deaddrop")
    entries = []
    if os.path.isdir(drop) and not os.path.islink(drop):
        try:
            entries = os.listdir(drop)
        except OSError:
            pass
    else:
        out["deaddrop_ok"] = False
    codes = {tiers[t]["code"].lower(): t for t in TIERS}
    found = set()
    for name in entries:
        n = norm_code(name)
        if n in codes:
            found.add(codes[n])
        else:
            out["unknown"].append(name)

    for t in TIERS:
        left = [c for c in tiers[t]["cleanup"] if os.path.lexists(c)]
        out["tiers"][t] = {"done": t in found, "left": left}

    for t in TIERS:
        for i in range(1, HINTS_PER_TIER[t] + 1):
            hp = os.path.join(home, "hints", f"{t}-{i}.txt")
            try:
                st = os.lstat(hp)
                if st.st_atime > st.st_mtime:
                    out["hints"].append(f"{t}-{i}")
            except OSError:
                pass
    return out


def tick():
    """Scan everyone, record first-completion times, return board rows + per-player scans."""
    with Locked():
        game = load_game()
        prog = load_json(PROGRESS_FILE, {})
        now = time.time()
        changed = False
        scans = {}
        for user, info in game["players"].items():
            s = scan_player(user, info)
            scans[user] = s
            pp = prog.setdefault(user, {"times": {}, "hints": {}})
            for t in TIERS:
                if s["tiers"][t]["done"] and t not in pp["times"]:
                    pp["times"][t] = now
                    changed = True
            for h in s["hints"]:
                if h not in pp["hints"]:
                    pp["hints"][h] = now
                    changed = True
        if changed:
            save_json(PROGRESS_FILE, prog)

    rows = []
    for user, s in scans.items():
        pp = prog.get(user, {"times": {}, "hints": {}})
        done = [t for t in TIERS if s["tiers"][t]["done"]]
        best = max((TIER_NUM[t] for t in done), default=0)
        best_tier = TIERS[best - 1] if best else None
        rows.append({
            "user": user,
            "done": done,
            "best": best_tier,
            "best_time": pp["times"].get(best_tier) if best_tier else None,
            "messy": any(s["tiers"][t]["left"] for t in done),
            "hints": len(pp["hints"]),
        })
    rows.sort(key=lambda r: (-(TIER_NUM[r["best"]] if r["best"] else 0),
                             r["best_time"] or float("inf"), r["user"]))
    rank = 0
    for r in rows:
        if r["best"]:
            rank += 1
            r["rank"] = rank
        else:
            r["rank"] = None
    return rows, scans, game


def status_text(user, s, game):
    lines = [f"HALCYON MISSION STATUS -- agent {user}",
             f"(updates every few seconds, last update {datetime.now():%H:%M:%S})", ""]
    if not s["deaddrop_ok"]:
        lines += ["!! Your dead drop folder is missing. Make it again:  mkdir ~/deaddrop", ""]
    for t in TIERS:
        ts = s["tiers"][t]
        label = f"{t.upper():<9}"
        if not ts["done"]:
            lines.append(f"{label}[ ] not done yet. No correct code in the dead drop.")
        elif ts["left"]:
            lines.append(f"{label}[x] code accepted   !! MESSY AGENT: {len(ts['left'])} file(s) left behind:")
            lines += [f"{'':13}{p}" for p in ts["left"]]
        else:
            lines.append(f"{label}[x] code accepted, no trace left. Clean work, agent.")
    if s["unknown"]:
        lines += ["", "These don't match any code (check for typos, then remove them):"]
        lines += [f"    rm ~/deaddrop/{n}" for n in s["unknown"]]
    lines += ["", "Briefings: ~/briefings/    Hints: ~/hints/    Submit: touch ~/deaddrop/<code>", ""]
    return "\n".join(lines)


def write_status(user, info, text):
    home = info["home"]
    if not os.path.isdir(home):
        return
    try:
        fd, tmp = tempfile.mkstemp(dir=home, prefix=".status-")
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, os.path.join(home, "STATUS.txt"))  # replaces a planted symlink, never follows it
    except OSError:
        pass


def update_all_status(scans, game):
    for user, s in scans.items():
        write_status(user, game["players"][user], status_text(user, s, game))


# --------------------------------------------------------------------------- commands

def read_names(path):
    names = []
    if path:
        if not os.path.isfile(path):
            sys.exit(f"File not found: {path}")
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    names.append(line)
    else:
        print("Type usernames one at a time (lowercase, no spaces). Blank line to finish.")
        while True:
            try:
                n = input("username: ").strip()
            except EOFError:
                break
            if not n:
                break
            names.append(n)
    return names


def cmd_setup(args):
    need_root()
    names = read_names(args.file)
    if not names:
        sys.exit("No names given.")
    try:
        out = subprocess.run(["sshd", "-T"], capture_output=True, text=True).stdout.lower()
        if "passwordauthentication no" in out:
            print("WARNING: SSH password login is OFF on this server. Players log in with passwords.\n")
    except FileNotFoundError:
        pass

    made = []
    for name in names:
        if not USERNAME_RE.match(name):
            print(f"skip: '{name}' isn't a valid username (lowercase letters, numbers, - and _)")
            continue
        with Locked():
            game = load_game()
        if name in game["players"]:
            print(f"skip: {name} is already playing (use reset to start them again)")
            continue
        try:
            pwd.getpwnam(name)
            print(f"skip: {name} is an existing account on this server, not touching it")
            continue
        except KeyError:
            pass

        password = f"{rng.choice(WORDS)}{rng.randint(10, 99)}"
        subprocess.run(["useradd", "-m", "-s", "/bin/bash", name], check=True)
        subprocess.run(["chpasswd"], input=f"{name}:{password}\n", text=True, check=True)
        home = pwd.getpwnam(name).pw_dir
        os.chmod(home, 0o700)

        plan = build_plan(name, home)
        created = apply_entries(plan.entries, name)
        with Locked():
            game = load_game()
            game["players"][name] = {"password": password, "home": home, "plan": plan.as_dict(),
                                     "created": time.time()}
            game["parents"] = sorted(set(game["parents"]) | set(created))
            save_json(GAME_FILE, game)
        made.append(name)
        print(f"created: {name}")

    print()
    cmd_creds(args)


def cmd_creds(args):
    need_root()
    game = load_game()
    if not game["players"]:
        print("No players yet.")
        return
    print(f"{'USERNAME':<16} PASSWORD")
    for u, info in sorted(game["players"].items()):
        print(f"{u:<16} {info['password']}")


def cmd_reset(args):
    need_root()
    with Locked():
        game = load_game()
        if args.user not in game["players"]:
            sys.exit(f"{args.user} isn't a player.")
        info = game["players"][args.user]
        plan = info["plan"]
        home = info["home"]
        tiers = [args.tier] if args.tier else TIERS

        if not args.tier:
            for rel in ["README.txt", "briefings", "deaddrop", "hints", "STATUS.txt"]:
                remove_path(os.path.join(home, rel))
        for t in tiers:
            for root in plan["tiers"][t]["roots"]:
                remove_path(root)
            for rel in plan["tiers"][t]["home"]:
                remove_path(os.path.join(home, rel))
        # make sure the shared home folders exist (also fixes symlink tricks)
        apply_entries([e for e in plan["entries"] if e["tier"] == "common" and e["kind"] == "dir"], args.user)
        if args.tier:
            code = plan["tiers"][args.tier]["code"].lower()
            drop = os.path.join(home, "deaddrop")
            for n in os.listdir(drop):
                if norm_code(n) == code:
                    remove_path(os.path.join(drop, n))
            apply_entries([e for e in plan["entries"] if e["tier"] == args.tier], args.user)
            # put back any briefings/README/hints they deleted (other tiers' files are left alone if present)
            home_files = [e for e in plan["entries"] if e["kind"] == "file" and e["tier"] != args.tier
                          and e["path"].startswith(os.path.join(home, ""))
                          and os.path.relpath(e["path"], home).split("/")[0] in ("README.txt", "briefings", "hints")
                          and not os.path.lexists(e["path"])]
            apply_entries(home_files, args.user)
        else:
            apply_entries(plan["entries"], args.user)

        prog = load_json(PROGRESS_FILE, {})
        pp = prog.setdefault(args.user, {"times": {}, "hints": {}})
        for t in tiers:
            pp["times"].pop(t, None)
            pp["hints"] = {h: v for h, v in pp["hints"].items() if not h.startswith(t + "-")}
        save_json(PROGRESS_FILE, prog)
    print(f"Reset {args.user}: {args.tier or 'everything'} is back to the start.")


def cmd_debug(args):
    need_root()
    rows, scans, game = tick()
    if args.user not in game["players"]:
        sys.exit(f"{args.user} isn't a player.")
    info = game["players"][args.user]
    s = scans[args.user]
    prog = load_json(PROGRESS_FILE, {}).get(args.user, {"times": {}, "hints": {}})
    print(f"== {args.user}  (password: {info['password']}, home: {info['home']})\n")
    for t in TIERS:
        ti = info["plan"]["tiers"][t]
        ts = s["tiers"][t]
        done = "DONE " + fmt_time(prog["times"].get(t)) if ts["done"] else "not done"
        print(f"{t.upper():<9} code: {ti['code']:<26} {done}")
        for c in ti["cleanup"]:
            print(f"{'':10}{'LEFT ' if c in ts['left'] else 'gone '} {c}")
    print(f"\ndead drop: {os.listdir(os.path.join(info['home'], 'deaddrop')) if s['deaddrop_ok'] else 'MISSING'}")
    if s["unknown"]:
        print(f"unrecognised: {s['unknown']}")
    print(f"hints read: {', '.join(prog['hints']) or 'none'}")


def board_rows_text(rows):
    G, Y, D, B, C, R = "\033[32m", "\033[33m", "\033[2m", "\033[1m", "\033[36m", "\033[0m"
    colour = {"bronze": "\033[38;5;173m", "silver": "\033[38;5;250m",
              "gold": "\033[38;5;220m", "platinum": "\033[38;5;153m"}
    out = [f"{B}{C}  OPERATION HALCYON{R}   {D}{datetime.now():%H:%M:%S}{R}", ""]
    counts = {t: sum(1 for r in rows if t in r["done"]) for t in TIERS}
    out.append("  " + "   ".join(f"{colour[t]}{t.upper()} {counts[t]}{R}" for t in TIERS) + "\n")
    out.append(f"  {B}{'#':<4}{'AGENT':<16}{'MISSIONS':<22}{'TIME':<10}NOTES{R}")
    out.append("  " + "-" * 64)
    for r in rows:
        if r["best"]:
            badges = " ".join(f"{colour[t]}{t[0].upper()}{R}" if t in r["done"] else f"{D}.{R}" for t in TIERS)
            pad = " " * (22 - 7)
            notes = []
            if r["messy"]:
                notes.append(f"{Y}messy agent{R}")
            if r["hints"]:
                notes.append(f"{D}{r['hints']} hint{'s' if r['hints'] != 1 else ''}{R}")
            out.append(f"  {r['rank']:<4}{r['user']:<16}{badges}{pad}{fmt_time(r['best_time']):<10}{', '.join(notes)}")
        else:
            out.append(f"  {D}{'-':<4}{r['user']:<16}searching...{R}")
    return "\n".join(out)


def cmd_board(args):
    need_root()
    sys.stdout.write("\033[?25l")
    try:
        while True:
            rows, scans, game = tick()
            update_all_status(scans, game)
            sys.stdout.write("\033[H\033[2J" + board_rows_text(rows) + "\n")
            sys.stdout.flush()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write("\033[?25h\n")


def cmd_serve(args):
    need_root()
    state = {"rows": [], "updated": 0}

    def loop():
        while True:
            try:
                rows, scans, game = tick()
                update_all_status(scans, game)
                state["rows"], state["updated"] = rows, time.time()
            except Exception as e:  # keep serving even if one scan fails
                print(f"scan error: {e}", file=sys.stderr)
            time.sleep(args.interval)

    threading.Thread(target=loop, daemon=True).start()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/api"):
                body = json.dumps({"rows": [{**r, "best_time": fmt_time(r["best_time"])} for r in state["rows"]],
                                   "updated": fmt_time(state["updated"])}).encode()
                ctype = "application/json"
            else:
                body = BOARD_HTML.replace("__REFRESH__", str(args.interval * 1000)).encode()
                ctype = "text/html; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    print(f"STATUS.txt files update every {args.interval}s.")
    print(f"Leaderboard: http://<this-server-ip>:{args.port}   (Ctrl+C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print()


def cmd_teardown(args):
    need_root()
    game = load_game()
    users = sorted(game["players"])
    if not users:
        print("No players.")
    else:
        print("This deletes these players, their home folders and every game file:")
        print("  " + ", ".join(users))
        if input("Type 'yes' to continue: ").strip() != "yes":
            sys.exit("Cancelled.")
    for u in users:
        info = game["players"][u]
        subprocess.run(["pkill", "-KILL", "-u", u], capture_output=True)
        r = subprocess.run(["userdel", "-r", u], capture_output=True, text=True)
        print(f"removed: {u}" if r.returncode == 0 else f"couldn't remove {u}: {r.stderr.strip()}")
        for t in TIERS:
            for root in info["plan"]["tiers"][t]["roots"]:
                remove_path(root)
    for d in sorted(game["parents"], key=len, reverse=True):
        try:
            os.rmdir(d)
        except OSError:
            pass
    shutil.rmtree(STATE_DIR, ignore_errors=True)
    print("Done.")


# --------------------------------------------------------------------------- text

README_TXT = """\
Welcome, agent.

Your briefings are in ~/briefings/. Start with 1-bronze, or jump straight
to whichever mission you like. Each one stands on its own.

    cat ~/briefings/1-bronze.txt

Submit a code:       touch ~/deaddrop/<code>
Wrong code?          rm ~/deaddrop/<wrong-code>
Check your progress: cat ~/STATUS.txt      (updates every few seconds)
Stuck?               ~/hints/              (no penalty... but we'll know)
"""

BRIEF_BRONZE = """\
BRIEFING 1 of 4: INFILTRATION

Agent,

Your target is Halcyon Systems. Before anything else, we need
their building access code.

Your inside contact left a note in your quarters (your home folder).
Not where you'd normally look. Our people keep things out of sight.

When you find the code, leave it in the dead drop:
    touch ~/deaddrop/<code>

Good luck.
"""

BRONZE_NOTE = """\
Agent, it's your contact. Glad you found this. Be quick.

The building access code is filed somewhere in the records here:
    {records}/

Not everything in there is accurate. Read carefully.

""" + DELETE_ME + "\n"

BRONZE_DECOYS = {
    "roster.txt": "Shift roster, week 14. Nothing to see here.",
    "invoice-0412.txt": "Invoice #0412: 40x replacement ceiling tiles. Paid.",
    "fire-drill.txt": "Fire drill: Thursday 10am. Assemble in the car park.",
    "inventory.txt": "Inventory: 3 mops, 2 buckets, 1 very old vacuum.",
    "todo.txt": "TODO: fix the flickering light on level 3. Buy more coffee.",
    "parking.txt": "Parking permits are renewed every January. Don't forget.",
    "cleaning-schedule.txt": "Cleaning schedule: kitchens daily, carpets monthly.",
    "visitor-log.txt": "Visitor log: no visitors this week.",
    "lost-property.txt": "Lost property: one umbrella, one sad sandwich.",
    "kitchen-rules.txt": "Kitchen rules: label your food. Yes, Gary, that means you.",
}
BRONZE_MOVED = "Facilities memo: to tidy up, older records have been moved into the {sub}/ folder."
BRONZE_HIDDEN_HINT = "Whoever filed these hid the current one. It won't show up with a plain ls."

BRONZE_REAL = """\
HALCYON SYSTEMS -- BUILDING ACCESS
Current access code: {code}

You're in, agent.

1. Leave the code in your dead drop:
       touch ~/deaddrop/{code}

2. Leave no trace. Delete:
       this file:            {real}
       your contact's note:  ~/{note}

MISSION COMPLETE.

...unless you want to know what they're hiding.
Next: ~/briefings/2-silver.txt

""" + DELETE_ME + "\n"

BRIEF_SILVER = """\
BRIEFING 2 of 4: THE LOGS

Previously: you got into Halcyon's building.

Someone inside has been editing the access logs to cover their tracks.
The log is here:
    {logs}/access.log

It's 400 lines long. Don't read it all. grep finds the lines that contain a word:
    grep WORD FILE

1. Find the entry marked TAMPERED. It shows who did it (their badge name)
   and where they moved the original log.
2. Go there and find that person's entry in the original log.
   It holds the override code.
3. Leave the code in the dead drop:   touch ~/deaddrop/<code>
4. Leave no trace: delete access.log, and the original log you found.

Next: ~/briefings/3-gold.txt
"""

BRIEF_GOLD = """\
BRIEFING 3 of 4: THE SHREDDED FILE

Previously: you got inside and found the tampered logs.

Before we could act, someone shredded a key document and dumped it here:
    {bins}/

It was torn into 3 pieces. Each one is marked "PIECE x of 3" and holds one word.
Shredders don't sort neatly: check every bin, and every folder inside every bin.
Even the ones you can't see at first glance.

Put the words in order (1, 2, 3) and join them with dashes:   word-word-word
Leave it in the dead drop:   touch ~/deaddrop/<code>
Then leave no trace: delete all 3 pieces.

Next: ~/briefings/4-platinum.txt
"""

GOLD_PIECE = """\
PIECE {n} of 3:  {word}

Collect all 3, put the words in order, join them with dashes:  word-word-word

""" + DELETE_ME + "\n"

GOLD_JUNK = [
    "Shredded receipt. Not a piece. Keep digging.",
    "Half a cafeteria menu. Not a piece.",
    "Torn sticky note: 'buy milk'. Not a piece.",
    "Shredded memo about the printer (again). Not a piece.",
    "Confetti from someone's birthday. Not a piece.",
    "A very crumpled parking ticket. Not a piece.",
]

BRIEF_PLATINUM = """\
BRIEFING 4 of 4: THE PAYLOAD

You got in. You saw the logs. You put the shredded file back together.
Now for what we actually came for.

Halcyon is running a project they don't want anyone to know about.
We need its codename.

There's no map for this one. All we know is that staff files are kept here:
    {staff}/

People are careless. They leave habits, histories, schedules.
Whoever runs the servers knows where the project lives.

Leave the codename in the dead drop:   touch ~/deaddrop/<codename>
Then leave no trace.

You're on your own.
"""

STAFF_TODOS = [
    "Book meeting room for Thursday.",
    "Reply to Sam about the budget.",
    "Bring biscuits for the 3pm meeting.",
    "Renew parking permit.",
    "Finish the quarterly report.",
    "Ask IT why my mouse keeps disconnecting.",
]
BORING_HISTORY = [
    "ls", "cd Documents", "nano todo.txt", "cat todo.txt", "cd ~", "clear",
    "ls -la", "pwd", "cd ..", "cat notes.txt", "history", "exit",
]
SYSADMIN_HISTORY = """\
ls
cd {internal}
ls
ls -la
grep -r "approval" .
clear
# reminder: the real codename sheet is the one I updated on {date}. the rest are old drafts
cd ~
exit
"""
PRJ_DENIED = """\
PROJECT {prj}
Lead:    {lead}
Status:  {status}
Board approval: DENIED
"""
PRJ_APPROVED = """\
PROJECT {prj}
Lead:    {lead}
Status:  ACTIVE -- RESTRICTED
Board approval: CONFIRMED

The codename sheet is in docs/. There are some old drafts in there too.
Only one is current.
"""
PLAT_SHEET = """\
PROJECT CODENAME:  {code}

If this is the current sheet:
  1. Leave the codename in the dead drop.
  2. Leave no trace: delete this file, and the sysadmin's .bash_history.

""" + DELETE_ME + "\n"

HINT_BRONZE_1 = """\
HINT (bronze 1 of 2)
Your contact's note is in your home folder, but it's hidden.
Hidden files start with a dot. Plain ls won't show them. Try:  ls -la ~
"""
HINT_BRONZE_2 = """\
HINT (bronze 2 of 2)
The note is ~/{note}   (cat it)
It sends you to {records}/
cat every file there. One tells you which folder the records moved to.
In that folder, ls -la. The real one might be hidden. And watch out for expired codes.
"""
HINT_SILVER_1 = """\
HINT (silver 1 of 2)
grep shows you only the lines that contain a word:
    grep TAMPERED {logs}/access.log
"""
HINT_SILVER_2 = """\
HINT (silver 2 of 2)
The TAMPERED line tells you two things: a badge name (badge=...) and a folder.
cd into that folder, then grep the original log for that badge name:
    grep <badge-name> original-access.log
"""
HINT_GOLD_1 = """\
HINT (gold 1 of 2)
Check every bin, and every folder inside every bin.
Use ls -la, not just ls. Hidden folders start with a dot.
"""
HINT_GOLD_2 = """\
HINT (gold 2 of 2)
One piece is 2 folders deep. One is 4 folders deep. One is inside a hidden folder.
Junk says "Not a piece". A real piece says "PIECE x of 3" at the top.
"""
HINT_PLATINUM_1 = """\
HINT (platinum, the only one you get)
The staff list tells you who runs the servers.
A person's command history lives in a hidden file in their folder.
ls -l shows when a file was last changed.
"""

BOARD_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Operation Halcyon</title>
<style>
:root{--bg:#0b0f14;--panel:#121821;--line:#1f2a36;--text:#e6edf3;--dim:#6b7a8c;
--bronze:#d08b55;--silver:#c9d1d9;--gold:#f2c94c;--platinum:#9fd8ff;--warn:#f0b429}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);
font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;padding:4vh 4vw}
header{display:flex;justify-content:space-between;align-items:baseline;gap:2rem;flex-wrap:wrap;
border-bottom:2px solid var(--line);padding-bottom:1.5vh;margin-bottom:2vh}
h1{font-size:clamp(28px,4.2vw,64px);letter-spacing:.18em;margin:0;font-weight:700}
h1 span{color:var(--dim);font-weight:400}
.counts{display:flex;gap:2.2vw;font-size:clamp(14px,1.5vw,24px)}
.counts b{font-size:1.4em;margin-left:.3em}
table{width:100%;border-collapse:collapse;font-size:clamp(16px,2vw,34px)}
th{text-align:left;color:var(--dim);font-weight:400;font-size:.55em;letter-spacing:.2em;
padding:.6em .5em;border-bottom:1px solid var(--line)}
td{padding:.45em .5em;border-bottom:1px solid var(--line)}
td.rank{color:var(--dim);width:3em}
.chip{display:inline-block;min-width:1.8em;text-align:center;border-radius:.3em;margin-right:.25em;
font-weight:700;font-size:.75em;padding:.1em .3em;border:2px solid currentColor}
.chip.off{color:#2c3a4a}
.bronze{color:var(--bronze)}.silver{color:var(--silver)}.gold{color:var(--gold)}.platinum{color:var(--platinum)}
.best{font-size:.6em;letter-spacing:.15em;margin-left:.6em}
.notes{font-size:.55em;color:var(--dim)}
.messy{color:var(--warn)}
tr.searching td{color:var(--dim)}
footer{margin-top:2vh;color:var(--dim);font-size:clamp(12px,1vw,16px)}
</style></head><body>
<header><h1>OPERATION <span>HALCYON</span></h1><div class="counts" id="counts"></div></header>
<table><thead><tr><th>#</th><th>AGENT</th><th>MISSIONS</th><th>TIME</th><th></th></tr></thead>
<tbody id="rows"></tbody></table>
<footer id="foot">connecting...</footer>
<script>
const T=["bronze","silver","gold","platinum"],L={bronze:"COMPLETED",silver:"SILVER",gold:"GOLD",platinum:"PLATINUM"};
const esc=s=>String(s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
async function load(){
 try{
  const d=await (await fetch("/api",{cache:"no-store"})).json();
  document.getElementById("counts").innerHTML=T.map(t=>`<span class="${t}">${t.toUpperCase()}<b>${d.rows.filter(r=>r.done.includes(t)).length}</b></span>`).join("");
  document.getElementById("rows").innerHTML=d.rows.map(r=>{
   if(!r.best)return `<tr class="searching"><td class="rank">-</td><td>${esc(r.user)}</td><td>searching...</td><td></td><td></td></tr>`;
   const chips=T.map(t=>`<span class="chip ${r.done.includes(t)?t:"off"}">${t[0].toUpperCase()}</span>`).join("");
   const notes=[r.messy?'<span class="messy">messy agent</span>':"",r.hints?`${r.hints} hint${r.hints>1?"s":""}`:""].filter(Boolean).join(" &middot; ");
   return `<tr><td class="rank">${r.rank}</td><td>${esc(r.user)}</td><td>${chips}<span class="best ${r.best}">${L[r.best]}</span></td><td>${r.best_time}</td><td class="notes">${notes}</td></tr>`;
  }).join("");
  document.getElementById("foot").textContent="updated "+d.updated;
 }catch(e){document.getElementById("foot").textContent="can't reach the server, retrying...";}
}
load();setInterval(load,__REFRESH__);
</script></body></html>
"""


# --------------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="Operation Halcyon -- LIY flag hunt")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("setup", help="create players")
    s.add_argument("file", nargs="?")
    sub.add_parser("creds", help="reprint logins")
    s = sub.add_parser("serve", help="STATUS.txt updater + web leaderboard")
    s.add_argument("--port", type=int, default=8080)
    s.add_argument("--interval", type=int, default=10)
    s = sub.add_parser("board", help="terminal leaderboard")
    s.add_argument("--interval", type=int, default=10)
    s = sub.add_parser("debug", help="show a player's progress")
    s.add_argument("user")
    s = sub.add_parser("reset", help="reset a player, or one tier")
    s.add_argument("user")
    s.add_argument("tier", nargs="?", choices=TIERS)
    sub.add_parser("teardown", help="delete everything")
    args = ap.parse_args()
    {"setup": cmd_setup, "creds": cmd_creds, "serve": cmd_serve, "board": cmd_board,
     "debug": cmd_debug, "reset": cmd_reset, "teardown": cmd_teardown}[args.cmd](args)


if __name__ == "__main__":
    main()

# Lesson 1: Linux Basics

Missed the session? Work through this and you'll be caught up.

**Covers:** an Ubuntu Server VM, connecting over SSH, and the everyday commands for moving around and managing files.

**You need:**
- VirtualBox
- The [Ubuntu Server ISO](https://ubuntu.com/download/server)

---

## 1. Create the VM

1. VirtualBox → **New**
2. Name it **Ubuntu Server** and select the ISO
3. **Untick** *Proceed with Unattended Installation*
   - We're doing the install manually so you see every option
4. Set resources: **4096MB** RAM / **4** CPUs / **64GB** disk
   - 2048 / 2 / 25 is fine on a weaker laptop. Server has no GUI, so it's light.
5. Finish, then open the VM's **Settings → Network**
6. **Adapter 1:** leave it as **NAT**
   - This gives the VM internet, through your laptop
7. **Adapter 2:** tick *Enable*, set it to **Host-only Adapter**
   - This creates a private network between your laptop and the VM, so you can SSH in
   - NAT alone won't let you reach the VM; host-only alone has no internet. Together you get both.
8. **Start** the VM

---

## 2. Install Ubuntu Server

1. Select *Try or Install Ubuntu Server* and wait for it to load
2. Language: **English**
3. Keyboard: leave the default
4. Install type: **Ubuntu Server**
   - Not *minimized*, which strips out tools we'll want
5. Network: both adapters should pick up an address automatically (DHCP). Continue.
6. Proxy: leave blank
7. Mirror: wait for the test to pass
   - This is where it downloads packages from
8. Storage: accept the **guided layout**, then the **summary**, then **confirm**
   - "Destructive" only means the VM's virtual disk
9. Profile: name, server name, username and password
   - Remember the username and password. You'll use them constantly.
10. Ubuntu Pro: **skip**
11. SSH setup:
    - Tick **Install OpenSSH server**. This is what lets you connect remotely.
    - Don't import keys
    - **Allow password authentication** (we'll swap to keys in Lesson 2)
12. Featured snaps: select none
13. Wait for it to finish, then **Reboot**
14. Press Enter at *remove installation medium*

---

## 3. First login and finding the IP

You're on the VM's console, like sitting at the server with a keyboard plugged in.

1. Log in with your username and password
   - The password shows nothing as you type. Normal for Linux.
2. Show the network adapters:
   ```
   ip a
   ```
3. Find your IP. You'll see three entries:
   - `lo` (`127.0.0.1`): loopback, the machine talking to itself. Ignore it.
   - `enp0s3` (`10.0.2.15`): the NAT adapter. You can't reach this from your laptop.
   - `enp0s8` (`192.168.56.x`): the **host-only** adapter. **This is the one you want.**
   - The IP is the bit after `inet` and before the `/`
4. Write it down
5. Log out:
   ```
   exit
   ```

**No address on `enp0s8`?** In VirtualBox go to **File → Tools → Network Manager** and check the host-only network has DHCP enabled. Then reboot the VM.

---

## 4. SSH in

From now on you'll work from your laptop's terminal, not the VM window. That's how real servers are managed.

1. Open **PowerShell** (Windows) or **Terminal** (Mac/Linux)
2. Connect:
   ```
   ssh username@192.168.56.x
   ```
3. It asks whether you trust the server's **fingerprint**. Type `yes`.
4. Enter your password
5. You're in. Everything you type now runs on the server.

### What was the fingerprint?

- Every SSH server has a **host key**, its unique ID, generated at install
- The fingerprint (`SHA256:...`) is a short hash of that key
- First time connecting, your laptop has never seen it, so it asks if you trust it
- `yes` saves it to `~/.ssh/known_hosts` on your laptop, and it's checked automatically every time after that
- If it ever changes, SSH refuses to connect: `REMOTE HOST IDENTIFICATION HAS CHANGED`
  - That's what a man-in-the-middle attack would look like, so SSH stops you

**You'll hit this if you rebuild your VM** (new host key, same IP). Clear the old entry, then reconnect:
```
ssh-keygen -R 192.168.56.x
```

---

## 5. Navigating

The goal here is understanding where you are in the filesystem and how to move around it.

1. Check where you are:
   ```
   pwd
   ```
   - *Print working directory*. You should be in `/home/username`, your home folder.
2. List what's here:
   ```
   ls
   ```
   - Empty, it's a fresh install
3. Make a folder:
   ```
   mkdir tutorial
   ```
4. Confirm it's there:
   ```
   ls
   ```
5. Go into it. Type `cd tut` and press **Tab**:
   ```
   cd tutorial/
   ```
   - Tab autocompletes names. If Tab won't complete something, it doesn't exist or you've misspelled it.
6. Go up one folder, then check where you are:
   ```
   cd ..
   pwd
   ```
   - `..` means the parent folder. You're back in `/home/username`.
7. Do it again:
   ```
   cd ..
   pwd
   ```
   - Now you're in `/home`, which holds every user's home folder
8. And once more:
   ```
   cd ..
   pwd
   ```
   - Now you're in `/`, the **root** of the whole filesystem. There's nothing above it.
9. Jump straight back to your folder from anywhere (Tab again):
   ```
   cd ~/tutorial/
   ```
   - `~` is shorthand for your home folder

**Key ideas:**
- `~` = your home. `/` = the top of everything. Don't mix them up.
- Paths starting with `/` or `~` are **absolute** and work from anywhere
- Anything else is **relative** to where you currently are
- Your prompt shows where you are too: `username@server:~/tutorial$`

---

## 6. Files

Make a file, rename it, read it, copy it, delete it.

1. Create and open a file in nano (the typo is on purpose):
   ```
   nano hello-wrld.txt
   ```
2. Type `Hello World!`
3. Save with **Ctrl+O**, then **Enter** to confirm the filename
4. Exit with **Ctrl+X**
   - On a Mac it's still **Ctrl**, not Cmd
   - `^` in nano's bottom bar means Ctrl
5. Check it exists:
   ```
   ls
   ```
6. Fix the typo by renaming it:
   ```
   mv hello-wrld.txt hello-world.txt
   ```
   - `mv` moves files. Moving to a new name in the same folder *is* renaming; there's no separate rename command.
7. Print its contents:
   ```
   cat hello-world.txt
   ```
   - Quick way to read a file without opening an editor
8. Make a copy:
   ```
   cp hello-world.txt hello2.txt
   ```
9. Check you've got both:
   ```
   ls
   ```
10. Delete the original:
    ```
    rm hello-world.txt
    ```
    - **Permanent.** No bin, no undo, no confirmation.
11. Check it's gone:
    ```
    ls
    ```

---

## 7. Folders and hidden files

Same operations, but on whole folders, then a look at hidden files.

1. Go up out of `tutorial`:
   ```
   cd ..
   ```
2. Copy the whole folder:
   ```
   cp -r tutorial/ tutorial2/
   ```
   - `-r` = recursive: the folder *and everything inside it*. Without it, `cp` refuses to copy folders.
3. Check it's there:
   ```
   ls
   ```
4. Look inside the copy:
   ```
   cd tutorial2/
   ls
   ```
   - Your files came along with it
5. Go back up:
   ```
   cd ..
   ```
6. Delete the copy (again, `-r` for folders):
   ```
   rm -r tutorial2/
   ```
   - Careful: `rm -r` on the wrong folder is how people lose work
7. Check it's gone:
   ```
   ls
   ```
8. Make a hidden file:
   ```
   touch .secret
   ```
   - `touch` creates an empty file
   - Any name starting with `.` is hidden. That's the whole mechanism; there's no special setting.
9. Look for it:
   ```
   ls
   ```
   - It's not there
10. Now list everything:
    ```
    ls -la
    ```
    - `-l` = long format (permissions, owner, size, date)
    - `-a` = all, including hidden files
    - There's `.secret`

**What else is in that list:**
- `.` = this folder, `..` = the parent. That's what `cd ..` has been using all along.
- `.bashrc`, `.profile`: config files for your shell. Linux keeps config in hidden "dotfiles" so they don't clutter things up.
- `.ssh/` might be there too. It comes back in Lesson 2.
- The `-rw-r--r--` column is **permissions**: who can read, write or run each file. Also Lesson 2.

---

## Extras

- **↑** arrow: scroll back through previous commands
- `history`: list everything you've typed
- `clear`: clear the screen

---

## Cheat sheet

| Command | Does |
|---|---|
| `pwd` | Where am I |
| `ls` / `ls -la` | List / list all, including hidden |
| `cd dir` / `cd ..` / `cd ~` | Enter / up one / home |
| `mkdir name` | New folder |
| `touch name` | New empty file |
| `nano name` | Edit a file |
| `cat name` | Print a file |
| `cp a b` | Copy (`-r` for folders) |
| `mv a b` | Move / rename |
| `rm name` | Delete (`-r` for folders) |
| `ip a` | Show IPs |
| `exit` | Log out |

**Next up:** SSH hardening. Keys instead of passwords, and sudo.

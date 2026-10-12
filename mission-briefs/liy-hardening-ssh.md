# Lesson 2: SSH Hardening

Missed the session? Work through this on your Lesson 1 VM.

**Covers:** swapping password login for SSH keys, using sudo, and locking down the SSH server.

**You need:**
- Your Ubuntu Server VM from Lesson 1, with Adapter 1 on NAT so it has internet
- A GitHub account

⚠️ **Keep your current SSH session open until the very end.** If you lock yourself out, that session (or the VM console) is how you get back in.

---

## 1. Make a key (on your laptop)

```
ssh-keygen -t ed25519
```
- Press Enter through the prompts. A passphrase is optional but recommended.
- It creates two files in `~/.ssh/`:
  - `id_ed25519`: **private**. Never share it or upload it anywhere.
  - `id_ed25519.pub`: **public**. Safe to hand out.
- Think of the public key as a lock you put on the server, and the private key as the only key that opens it.

---

## 2. Add it to GitHub

Print your **public** key:
- Mac/Linux: `cat ~/.ssh/id_ed25519.pub`
- Windows: `type $env:USERPROFILE\.ssh\id_ed25519.pub`

Then on GitHub:
- **Settings → SSH and GPG keys → New SSH key**
- Paste it, key type: **Authentication Key**
- Check it's there: open `https://github.com/<your-username>.keys` in a browser

The `.keys` page is public, and that's fine; public keys are meant to be. It's what your server will pull from.

---

## 3. Import it on the VM

In your SSH session:
```
ssh-import-id gh:<your-username>
cat ~/.ssh/authorized_keys
```
- `authorized_keys` is the list of keys allowed to log in as you. Yours is in there now.

<details>
<summary>No GitHub / no internet? Do it manually instead</summary>

Mac/Linux:
```
ssh-copy-id username@192.168.56.x
```
Windows (PowerShell, one line):
```
type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh username@192.168.56.x "mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
```
</details>

---

## 4. Test key login

Open a **new** terminal:
```
ssh username@192.168.56.x
```
- You should get in **without a password** (or it asks for your key's passphrase)
- **Don't go any further until this works**

Still asking for your password? Check:
- you uploaded the `.pub` file
- your GitHub username is spelled right
- `~/.ssh/authorized_keys` isn't empty
- `ssh -v` shows which keys your laptop tried

---

## 5. Sudo

```
groups
sudo whoami
```
- `groups` should include `sudo`
- `sudo whoami` should print `root`

**sudo** runs one command as root (the admin account).
- It asks for **your** password, not root's. Ubuntu doesn't give root a usable password by default.
- It beats logging in as root: every sudo command is logged, and you have to deliberately ask for the power each time.

---

## 6. Lock down SSH

Try editing the SSH config **without** sudo:
```
nano /etc/ssh/sshd_config.d/00-hardening.conf
```
- Type anything → **Ctrl+O** → *Permission denied*. Regular users can't touch system config.
- **Ctrl+X** to exit

Now properly:
```
sudo nano /etc/ssh/sshd_config.d/00-hardening.conf
```
Add:
```
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
MaxAuthTries 3
```
Save and exit.

| Setting | Does |
|---|---|
| `PermitRootLogin no` | Nobody can SSH in as root directly |
| `PasswordAuthentication no` | Keys only |
| `KbdInteractiveAuthentication no` | Closes the other password-style login route |
| `PubkeyAuthentication yes` | Allows key login (default, but explicit is better) |
| `MaxAuthTries 3` | Limits guesses per connection |

**Why a new file instead of editing `sshd_config`?**
- SSH reads everything in `sshd_config.d/` first, and for each setting **the first value it sees wins**.
- The installer probably left a file in there with `PasswordAuthentication yes`. See for yourself:
  ```
  ls /etc/ssh/sshd_config.d/
  ```
- Starting our file with `00-` makes it load first, so our settings win.

---

## 7. Test the config

```
sudo sshd -t
```
- No output = no errors
- Errors? Fix them **before** restarting. A broken config means SSH won't come back up.

---

## 8. Restart and verify

```
sudo systemctl restart ssh
sudo sshd -T | grep -Ei 'passwordauth|permitroot|kbdinter'
```
- All three should say `no`
- `sshd -T` shows the **actual** config in effect after all the files are merged, so this proves your file won.

---

## 9. Prove it

From a **new** terminal on your laptop:
```
ssh username@192.168.56.x
```
- Still works with your key

Now try forcing a password login:
```
ssh -o PubkeyAuthentication=no username@192.168.56.x
```
- `Permission denied (publickey)`

Your password is now useless for SSH. Without your private key, nobody gets in.

---

## 10. Firewall (optional)

```
sudo ufw allow OpenSSH
sudo ufw enable
sudo ufw status
```
- **Allow SSH before you enable it**, or you'll block yourself out.
- By default ufw blocks all incoming connections except what you've allowed.

---

## Bonus

```
ssh -T git@github.com
```
GitHub greets you by name. That same key now works for cloning and pushing repos over SSH.

---

## Cheat sheet

| Command | Does |
|---|---|
| `ssh-keygen -t ed25519` | Make a key pair |
| `ssh-import-id gh:user` | Pull your GitHub keys onto a server |
| `sudo <command>` | Run one command as root |
| `sudo sshd -t` | Check SSH config for errors |
| `sudo sshd -T` | Show the SSH config actually in effect |
| `sudo systemctl restart ssh` | Restart SSH |
| `sudo ufw status` | Firewall status |
| `ssh-keygen -R <ip>` | Forget an old host fingerprint |

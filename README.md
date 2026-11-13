# walink: re-link WhatsApp Business linked devices from a phone kept in a drawer

Automates the phone side of "Link with phone number": removes a linked device, enters the
8-character code shown by a workstation, names the new device, and verifies it in the list.
Built and tested on **WhatsApp Business 2.26.43.72, English UI, Huawei MED-LX9N, Android 10, no PIN**.

The account is limited to **4 linked devices**, so `link` always **deletes the chosen device first, then adds a new one**.

## Setup
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
sudo pacman -S android-tools            # or your distro's adb
```
On the phone: Developer options > **USB debugging** on. Plug in, accept the RSA prompt, check `adb devices`.

**Install the uiautomator2 agent (one time):**
```bash
.venv/bin/python -m uiautomator2 init
```
This pushes `u2.jar` to `/data/local/tmp` and installs the AdbKeyboard IME APK. If "Monitor apps installed by ADB" is on, **tap Install on the phone** (it took ~70 s the first time). It does **not** change the default keyboard. walink never uses the IME.

### Wi-Fi ADB (optional, so the phone needs no cable)
Android 10 needs USB once:
```bash
adb tcpip 5555 && adb connect PHONE_IP:5555     # then unplug; set "serial: PHONE_IP:5555" in config.yaml
```
It resets on reboot. Keep it connected with a cron/timer line such as `* * * * * adb connect PHONE_IP:5555`.
Give the phone a DHCP reservation. Keep it charging and on Wi-Fi: with no SIM it relies on Wi-Fi.

## Usage
```bash
python walink.py                              # the TUI (needs an interactive terminal)
python walink.py status                       # model, Android version, screen, W4B installed
python walink.py list [--json]
python walink.py link ABCD-EFGH --replace "Hotel Rex"   # asks "Are you sure you want to delete Hotel Rex?"
python walink.py link ABCD-EFGH --name "Desk 2"         # only when a slot is free
python walink.py unlink "Hotel Rex" [--yes]
python walink.py last "PayPal" [--json]           # newest message of a chat (opens it: marks it READ)
python walink.py keepalive
python walink.py reset                         # force-stop + relaunch WhatsApp, go to the launcher (recovery)
python walink.py --dry-run link ABCD-EFGH --replace "Hotel Rex"   # goes as far as possible, changes nothing
```
The TUI and the commands are the same program with the same rules (`walink/actions.py`); see **TUI** below.
- stdout carries only the result (text, or JSON with `--json`), so `walink.py list --json | jq` works. stderr shows the
  steps in plain words (`40% Entering the code`); technical log lines only with `--debug`. Every run also writes
  `logs/<timestamp>/walink.log`.
- Code input is normalized (dashes/spaces dropped, uppercased, 8 alphanumerics required).
- Every removal (`unlink`, or the removal half of `link --replace`) asks `Are you sure you want to delete <NAME>?`
  before the phone is touched. Scripts pass `--yes`; without a terminal and without `--yes` the command is refused.
  Names in `protected_devices` are always refused, by every path.
- Exit codes: `0` ok, `2` failed or usage error, `4` refused (protected, not confirmed, unknown device, list full), `5` phone busy.
- Every run is written to `logs/audit.log` (who, what, outcome; never message content). `notify_cmd` (optional) gets
  link, remove, last-message and failure events.
- Success = the new name appears in the list and is not "Logged out". Anything else exits non-zero.
- `last` finds the chat via search by its **whole** name (case does not matter: `paypal` finds "PayPal"), checks the chat title, jumps to the newest
  message and prints `[day time] sender: text` (`me` for outgoing; `(non-text message)` for a photo/voice note without caption).
  Opening the chat **sends read receipts and clears the unread badge**. Message text is never written to `logs/`
  (but a failure screenshot can show the chat). Takes ~26 s. Very long messages may be cut at WhatsApp's "Read more".
- Every run starts from "step 0": wake/unlock (wake key, then `cmd window dismiss-keyguard`, which works only because the
  phone has no PIN; it falls back to a swipe; ~1.9 s), bring WhatsApp Business to the front and back out to the chat list, like a
  person reopening the app. If that does not reach the chat list, it falls back to force-stop + relaunch (no data cleared).
  Failure recovery and `keepalive` always use the force-stop + relaunch.
- A run leaves WhatsApp on the screen it ended on (the Linked devices list, or the chat it read) and remembers it in
  `state/screen.json`. The next run within **5 minutes** checks that the phone still shows that screen and continues from
  there (a second `list` takes ~5 s instead of ~10 s); otherwise, or after a failure, keepalive or reset, it starts
  fresh. **Every run (except `status`) still ends by turning the screen off, which locks the phone**
  (`lock_screen_when_done: false` in `config.yaml` to disable).
- Codes expire within about a minute: start the command right after the workstation shows the code.
  A full link takes about 55 s; the code is entered ~15 s after you launch it.

**On failure** (any error, expected or not): a screenshot and UI dump go to `logs/<timestamp>/`, the exit code is non-zero,
and the phone is left on the WhatsApp home screen. **`logs/` can contain chat names and messages. Treat it as sensitive** and
purge it regularly.

**`state/` is not logs: never purge it while walink can run.** It holds `phone.lock`: deleting a held lock lets a second run
drive the phone at the same time.

## TUI
Run `python walink.py` (or log in over SSH, below). It opens on a menu; the phone is only used once you pick something:
- **Read the last message of a chat** (`m`): pick one of `quick_chats` (`config.yaml`) or type any chat name.
  No device list is read for this.
- **Link a computer** (`l`): the linked devices are read from the phone, you pick the one to replace (or `(free slot)`),
  then enter the code and the name (pre-filled when replacing). Show the code on the computer first: it expires in
  about a minute.
- **Remove a linked device** (`d`), **Quit** (`q`). With `--debug` also **Show the linked devices** (`r`), **Keepalive** (`k`)
  and **Phone status** (`s`); the CLI always has them (`list`, `keepalive`, `status`).
- While the phone works, a dialog shows the current step in plain words, a progress bar and the elapsed seconds.
  Results and errors appear in a dialog until you close it.
- `[locked]` devices are protected: they cannot be picked for removal or replacement. Any removal asks
  "Are you sure you want to delete <NAME>?", starting on **No**.
- `q` during an action asks to quit after it finishes. If the SSH connection drops mid-action, the action still
  finishes and the phone is locked.
- `--dry-run` (shown in the header) changes nothing; `--debug` adds a pane with the technical log lines.

## Human-like pacing (ban avoidance)
All input goes through `walink/device.py` (the only module that touches uiautomator2/adb) and `walink/humanize.py`.
Gaps are drawn from a normal distribution: same-type characters 70-200 ms, letter<->digit 130-320 ms,
layout taps 300-650 ms. Edit `timing:` in `config.yaml`. Time spent in the phone call itself counts toward the gap.
Each tap finds the element once, waits the human pause, then taps a slightly random point near its centre (within the
middle 60%), never the same pixel twice in a row. Launch/Home use `am start` (~0.1 s) instead of `input keyevent`/`monkey`
(~0.9 s each), so a `last` run takes ~14 s instead of ~31 s with the same pause ranges.

## Keepalive (prevents the 14-day primary-inactivity logout)
```cron
# every 3 days at 09:10
10 9 */3 * * cd /path/to/whatsapp-pair && .venv/bin/python walink.py keepalive >> logs/keepalive.log 2>&1
```
systemd alternative: a `walink-keepalive.service` running the same command and a `.timer` with `OnCalendar=*-*-1/3 09:10`.
On Windows Task Scheduler, run `python walink.py keepalive` on a schedule from the checkout directory.

## Remote access over SSH (workstations)
walink is an internal tool: an authorized workstation key can do everything walink can do. The guards are against
mistakes (protected devices, "Are you sure?"), not against key holders. Setup is manual:
- **One key per workstation, with a forced command.** In `~walink/.ssh/authorized_keys`, one line each:
  ```
  restrict,pty,command="/path/.venv/bin/python /path/walink_remote.py alice-pc" ssh-ed25519 AAAA... alice-pc
  ```
  `restrict` disables shell, port forwarding and agent; `pty` gives the TUI a terminal. The label (`alice-pc`) names the
  key in `logs/audit.log`. Revoke a workstation by deleting its line.
- `ssh -i <key> walink@controller` opens the **TUI** (Windows: use Windows Terminal with the built-in OpenSSH client).
- `ssh -i <key> walink@controller "<command>"` runs **one** walink command, same syntax as `walink.py`
  (`"list --json"`, `"unlink 'Sara' --yes"`, `"last 'PayPal'"`); output streams back live, the exit code is passed through.
  Without a terminal, removals need `--yes`. The older form `relink "<NAME>" <CODE>` still works
  (= `link CODE --replace NAME --yes`).
- Everything shares **one lock** with the CLI, other TUIs and cron (a second request gets "busy", exit 5); a TUI holds it
  only while an action runs. If a one-shot command exceeds `remote.timeout_s` (240 s), it is stopped and
  `walink.py reset` puts the phone back on the launcher and locks it.
- Run sshd behind WireGuard/Tailscale if you can, on a non-default port, with `PasswordAuthentication no`.
- Scripts (Windows 10/11 built-in OpenSSH; edit `$Server` and `$Key`):
  `windows/walink-relink.ps1 -Name "Hotel Rex" -Code "ABCD-EFGH"` and `windows/walink-last.ps1 -Chat "PayPal"`.
- There is no way to get a shell or raw adb access over SSH.

## Running the controller on Windows instead of Linux
The code is cross-platform (adb, uiautomator2, the phone lock and the SSH wrapper all work on Windows); only the setup differs.
**Not yet tested on a real Windows controller.**
1. **Python + adb.** Install Python 3.11+ and Android **platform-tools**; put the platform-tools folder on the system `PATH`
   (walink runs `adb` from `PATH`). Use only that one adb: a second adb of a different version (Android Studio, HiSuite)
   keeps killing the server ("adb server version doesn't match").
   ```powershell
   cd C:\walink; py -m venv .venv; .venv\Scripts\pip install -r requirements.txt
   .venv\Scripts\python -m uiautomator2 init
   .venv\Scripts\python walink.py status
   ```
2. **USB driver.** Windows needs a driver for the Huawei's adb interface (Google USB Driver or Huawei's HiSuite driver).
   Check with `adb devices` and accept the RSA prompt on the phone.
3. **Keep it awake.** Power plan: never sleep; Device Manager > USB Root Hub > Power Management: untick "allow the computer
   to turn off this device" (or disable USB selective suspend). Set Windows Update active hours so a reboot does not land
   during a relink.
4. **Schedules (Task Scheduler instead of cron).** Run them as the `walink` user, "whether user is logged on or not":
   ```powershell
   $py = "C:\walink\.venv\Scripts\python.exe"
   $t = New-ScheduledTaskTrigger -Daily -DaysInterval 3 -At 09:10
   Register-ScheduledTask walink-keepalive -Trigger $t -User walink -Password (Read-Host) `
     -Action (New-ScheduledTaskAction -Execute $py -Argument "walink.py keepalive" -WorkingDirectory C:\walink)
   ```
   For Wi-Fi ADB, add a task that runs `adb connect PHONE_IP:5555` at startup and every few minutes. The adb server on
   the PC forgets the connection after a PC reboot; the phone forgets tcpip mode after a phone reboot (USB needed again).
5. **SSH server (remote trigger).** `Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0`, then
   `Set-Service sshd -StartupType Automatic; Start-Service sshd`. Create a **standard (non-admin) local user** `walink`;
   for members of Administrators sshd ignores the user's own file and reads `C:\ProgramData\ssh\administrators_authorized_keys`.
   In `C:\Users\walink\.ssh\authorized_keys` (path without spaces; only `walink`, SYSTEM and Administrators may access the file):
   ```
   restrict,pty,command="C:\walink\.venv\Scripts\python.exe C:\walink\walink_remote.py alice-pc" ssh-ed25519 AAAA... alice-pc
   ```
   In `C:\ProgramData\ssh\sshd_config` set `PasswordAuthentication no` and `AllowUsers walink`, then restart sshd.
   The client scripts in `windows/` are unchanged; only `$Server` points at the new machine.
6. **Protect `logs/` and `state/`** (logs can hold chat screenshots; state holds the phone lock): run
   `icacls C:\walink\logs /inheritance:r /grant:r walink:(OI)(CI)F Administrators:(OI)(CI)F SYSTEM:(OI)(CI)F` and the same for `C:\walink\state`.
   `notify_cmd` runs through `cmd.exe` on Windows. All text I/O is UTF-8 (emoji and accented chat names are fine).
7. Run the tests there: `.venv\Scripts\python -m pytest -q tests`, then `walink.py list` and a `last` on the phone.

## Running the controller in WSL2 on Windows
An alternative to the native Windows setup: the Linux instructions above apply unchanged inside WSL2, plus three extra
pieces (USB passthrough, keeping WSL alive, inbound SSH). **Not yet tested on a real controller.**
1. **USB (usbipd-win).** WSL2 cannot see USB devices by itself. Install [usbipd-win](https://github.com/dorssel/usbipd-win), then:
   ```powershell
   usbipd list                                     # find the phone's BUSID
   usbipd bind --busid 2-3                         # once, as admin
   usbipd attach --wsl --busid 2-3 --auto-attach   # stays in the foreground; re-attaches after replug/reboot
   ```
   Run the `attach` line as a startup task so it survives reboots. Inside WSL, install platform-tools and check
   `adb devices`. Only one adb may own the phone: no adb server on the Windows side (Android Studio, HiSuite).
   Wi-Fi ADB (`adb connect PHONE_IP:5555` from WSL) avoids usbipd, but the phone forgets tcpip mode on reboot.
2. **Keep WSL running.** WSL stops a distro shortly after its last process exits, so cron and sshd inside it would stop too.
   Enable systemd in `/etc/wsl.conf` (`[boot]` / `systemd=true`), then add a Task Scheduler task, "at startup",
   "whether user is logged on or not", that runs `wsl.exe -d Ubuntu -- sleep infinity`. Keepalive then uses cron or a
   systemd timer inside WSL as on Linux. The Windows power and Windows Update notes above still apply.
3. **Inbound SSH.** WSL2 is behind NAT by default. On Windows 11 set this in `%USERPROFILE%\.wslconfig`, then run `wsl --shutdown`:
   ```ini
   [wsl2]
   networkingMode=mirrored
   ```
   Run sshd inside WSL and set it up as in "Remote access over SSH". If Windows OpenSSH also runs, give the WSL sshd
   another port. Allow that port in the Windows firewall, then in the `windows/` scripts point `$Server` at the Windows host and add `-p PORT` to the `ssh` line.
4. Keep the checkout in the WSL filesystem (`~/whatsapp-pair`), not under `/mnt/c`, which is slow and has loose permissions.

## When WhatsApp changes its UI
1. Run `python walink.py list` (or the failing command). The failure folder has `screen.png` and `hierarchy.xml`.
2. Find the new resource-id / text in `hierarchy.xml` and edit **`walink/selectors.py`** (all selectors live there; prefer resource-id, then content-desc, then text).
3. Screen swipes are fractions of the screen size (`SCROLL_SWIPE`, `UNLOCK_SWIPE` in `walink/device.py`).
Known traps: the menu item with text "Linked devices" has id `menuitem_scan_qr`; the dialog title id is in the app namespace
(`com.whatsapp.w4b:id/alertTitle`) while its buttons are `android:id/button1`.

## Quirks learned on this phone
- uiautomator2's `d.screenshot()` fails here; walink uses `adb exec-out screencap`.
- With 4/4 devices, "Link a device" shows "To link more devices, subscribe to Meta One".
- The 8th code box **submits automatically**. Code boxes are filled with per-box `set_text` (raw key presses were
  unreliable: SwiftKey hides and re-attaches on every focus change), the first 7 are read back from one dump, and only then is the 8th filled.
- The device name field accepts raw key presses (falls back to one `set_text` if the readback differs).
- Naming happens inside the link flow (after "Logging in…"); there is no separate rename step.
- A rejected code shows an "Incorrect code" dialog and clears the boxes; walink aborts instead of retrying.

## Tests
`.venv/bin/python -m pytest -q tests`

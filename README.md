# NetHarness

Remote-control a real Amiga over TCP/IP. Run commands, capture the screen,
inject mouse and keyboard input, and reboot it — all from another machine, with
nobody sitting at the Amiga.

```
$ nhctl.py --host 192.168.1.32 EXEC version
rc=0
Kickstart 47.115, Workbench 47.5

$ nhctl.py --host 192.168.1.32 SCREENSHOT desktop.png
OK desktop.png 1024x768x24
```

It exists because testing Amiga software normally means physically sitting at
the machine: clicking through a GUI, squinting at a screen, and power-cycling
by hand. NetHarness turns that into something a script can drive.

## What it does

| Command | |
|---|---|
| `EXEC <command>` | Run any AmigaDOS command; returns its exit code **and its output** |
| `SCREENSHOT [file]` | Capture the front screen as a PNG |
| `CLICK x y`, `MOVETO`, `MOVE`, `BUTTON` | Mouse, injected at hardware level |
| `TYPE <text>`, `PRESSKEY`, `KEY`, `CLEARFIELD` | Keyboard, with shift handled |
| `RESETINPUT` | Release any stuck buttons or qualifiers |
| `REBOOT` | Cold-reboot the machine (flushes disks first) |
| `PING` | Liveness check |

Input is injected through `input.device` (`IND_WRITEEVENT`), the same path real
hardware uses, so it exercises window activation, GadTools, menus — everything
a genuine click would.

### New in 1.3 — stop guessing pixels

| Command | |
|---|---|
| `UITREE` | The front screen's windows and gadgets: id, kind, bounds, label, string contents |
| `UICLICK <text>` / `UICLICKID <id>` | Click a gadget **by identity**, not coordinates |
| `MENUS` / `MENUSEL <menu> <item>` | Enumerate the menu strip; pick an item **by name** |
| `POINTER` | Where the pointer actually is (it's a hardware sprite — invisible in screenshots) |
| `SHOTREGION x y w h` | Capture just a region — far cheaper than a full frame |
| `REGIONSUM x y w h` | 4-byte checksum: "has this redrawn yet?" |
| `WAITCHANGE x y w h` | Block until a region actually changes, instead of guessing a delay |
| `GETFILE` / `PUTFILE` | Binary-safe file transfer — deploy a build through the harness itself |
| `SCREENS` | List open screens, front to back |
| `netharness [port]` | Listen port is an argument, so a new build can be tested beside the running one |

**New in 1.4:**

| Command | |
|---|---|
| `RELOAD` | Apply a staged `C:netharness.new` and **restart the harness in place - no machine reboot** |

**New in 1.5:**

- Plays fair with the TCP/IP stack's own shutdown: the harness blocks in
  `WaitSelect` on socket **and** break signal, so a stack shutdown (Roadshow's
  `NetShutdown`) sees it release `bsdsocket.library` immediately instead of
  timing out against a blocked `accept()`/`recv()` and deferring the teardown.

**New in 1.6:**

- Send-stall guard: a controller that vanishes mid-transfer no longer freezes
  the harness in a blocking `send()` - every send waits for writability first
  (20 s cap), then the dead client is dropped.
- Idle-client reaper: a silent client is disconnected after 600 s, so a
  controller that died without closing can't hold the single client slot.

**New in 1.7:**

- Bring-up retries forever instead of giving up after 60 s: 2 s apart for the
  first minute, then every 10 s (CTRL-C aborts the wait). The harness now
  self-connects whenever its network path appears late - a companion Pi still
  booting, WiFi rejoining, or the stack restarting - with no manual run needed
  at the machine.

**New in 1.8:**

- Client allowlist: only controllers listed in `ENV:NetHarness.allow` may
  connect. Entries are exact IPv4 addresses (`192.168.50.101`), prefixes ending
  in a dot (`192.168.50.`) or `*`, separated by spaces or newlines; loopback is
  always allowed. The file is re-read on every connect. Put it in `ENVARC:` too
  so it survives a reboot:
  `echo "192.168.50.101" >ENVARC:NetHarness.allow` + `copy ENVARC:NetHarness.allow ENV:`.
  With no file the harness still accepts anyone (so an upgrade can't lock you
  out) and logs a warning to `T:netharness.log` on each connect.
- Length-field hardening: a GETFILE/PUTFILE/EXEC length too big for the
  command buffer now drops the client instead of wrapping negative and walking
  the parser out of its buffer (stray bytes could run as commands).

`UITREE` + `UICLICK` are the headline: drive the GUI by *what things are*.

```
$ nhctl.py UITREE
W 0 0 11 332 199 "Time Preferences"
G 16 STRING 58 29 44 8 "" "2026"
G 15 GADGET 128 146 192 10 "Hours" ""
```

**Honest limitation:** some gadgets expose no text to Intuition — OS 3.2 Prefs'
Save/Use/Cancel buttons report empty labels because those apps draw the text
themselves. Slider labels and string-gadget contents do come through. Use
`UICLICKID <id>` for such buttons, `UICLICK <text>` everywhere else.

Every input command is acknowledged **after injection**, so `OK` means the
Amiga really did it — not merely that a packet was sent.

## Why EXEC matters

Driving a GUI blind is miserable: you guess coordinates, click, screenshot,
and hope. `EXEC` avoids most of that — anything expressible as a Shell command
is one call, with output returned to you. Combined with ARexx it reaches inside
applications too:

```
nhctl.py EXEC 'rx "address IBROWSE; ''GOTOURL https://aminet.net''"'
```

## Getting started

**On the Amiga** (needs a TCP/IP stack — Roadshow, AmiTCP, Miami, a314bsd…):

```
Execute Install
Run >NIL: C:netharness
```

It listens on TCP port **7800**. To start it at every boot, add that `Run` line
to the end of `S:User-Startup`, after your TCP/IP stack comes up.

One addressing nuance: with a **proxy-style stack** such as a314bsd (where the
sockets actually live on a Raspberry Pi), connect to the *proxy host's* IP —
the Amiga itself has no address of its own.

Use the installer rather than copying by hand, especially if you unpacked the
**.zip**: ZIP archives cannot carry AmigaDOS protection bits, so `netharness`
arrives without its `e` (executable) flag and the Shell refuses to run it. The
installer sets the flag for you; to do it manually:

```
Copy netharness C:
Protect C:netharness +e
```

The `.lha` archive preserves the flag, so it needs no such fixup.

**On the controlling machine** (Python 3, plus Pillow for screenshots):

```
python3 nhctl.py --host <amiga-ip> PING
python3 nhctl.py --host <amiga-ip> EXEC list SYS:
python3 nhctl.py --host <amiga-ip> --batch < commands.txt
```

`--batch` sends many commands over one connection, which is much quicker than
one invocation each.

## Screenshots

Screen capture handles the awkward cases real Amigas actually present:

- **Interleaved bitmaps**, which AGA Workbench screens normally use
- **True-colour RTG screens** (Picasso96/CyberGraphX) via `ReadPixelArray`
- **Planar screens** of any depth, with the screen's real palette

The mouse pointer is a hardware sprite and never appears in a capture — verify
mouse actions by their effect (a window activating, a gadget highlighting),
not by looking for the cursor.

## Notes worth knowing

- **Every input command is acknowledged** by the Amiga after injection, so
  `OK` means *delivered and injected*, not merely *sent*. Without that, a
  command that lands on nothing looks exactly like one that worked.
- **Prefer `RELOAD` over `REBOOT` for updates.** `REBOOT` calls `ColdReboot()`,
  and not every machine survives a warm CPU reset - one of the test machines
  here (an A2000) lands on a grey screen and needs a power cycle. `RELOAD` applies a staged `C:netharness.new` and restarts the
  harness in place instead, which is what rebooting was being used for anyway.
  It applies the update *while still serving*, so a failed copy leaves the
  working build running rather than bricking your remote access.
- **`REBOOT` flushes filesystems first.** `ColdReboot()` resets instantly, and
  without an explicit flush any file written moments earlier is quietly lost —
  which looks uncannily like the file "reverting" after a reboot.
- **DOS requesters are suppressed** (`pr_WindowPtr = -1`). A command touching a
  missing volume would otherwise raise "Please insert volume…" and block the
  single-threaded harness with no way to dismiss it remotely.
- **`EXEC` is synchronous.** A command that never returns holds the connection,
  so start long-lived programs with `EXEC run >NIL: <program>`.
- There is **no authentication**. It executes commands as sent — use it on a
  network you trust, not a public one.

## Which version is installed?

The binary carries a standard AmigaDOS version cookie:

```
$ nhctl.py --host <ip> EXEC "version C:netharness full"
netharness 1.4 (08/12/26)
```

## Building

```
cd amiga && make        # needs the bebbo amiga-gcc cross-compiler
```

The Amiga side is a single C file using only standard `bsdsocket.library`
calls, built `-m68020` — change that to `-m68000` in `amiga/Makefile` if you
need it to run on a stock 68000 machine.

## Licence

MIT — see [LICENSE](LICENSE).

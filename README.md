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
| `VERSION` | The harness version (1.10+) |

Input is injected through `input.device` (`IND_WRITEEVENT`), the same path real
hardware uses, so it exercises window activation, GadTools, menus — everything
a genuine click would.

## What's new (newest first)

**New in 1.17** - the custom chips, as the OS sees them:

- `CHIP` - the chipset (OCS/ECS/AGA, PAL/NTSC), which DMA channels are on (bitplanes, copper,
  blitter, sprites, disk and the four audio channels), the interrupts enabled and pending, audio
  modulation, the beam line, and the front screen's size, depth and mode ID.
- `COPPER` - the copper lists on screen, decoded: graphics.library's start-up list (display
  window, sprite pointers) and then the frame list (the screens' colours, bitplane pointers and
  splits), with register names and the pointers put back together. `COPPER SHF` shows an
  interlaced display's short frame.
- `SPRITES` - the eight hardware sprites: where their data is, position, height, whether they are
  attached, and who has them (sprite 0 is the mouse pointer; graphics parks unused ones on an
  empty sprite).
- `PALETTE` - the front screen's colours (24-bit on AGA).
- Most custom-chip registers are write-only, so these come from graphics.library's own records
  and from chip RAM; only the registers that can be read without side effects are read
  (DMACONR, VPOSR, VHPOSR, ADKCONR, INTENAR, INTREQR, DENISEID). Audio is therefore which channels
  are playing and their interrupts - Paula's period, volume and sample pointers can't be read back.

**New in 1.16** - find memory leaks:

- `LEAK START <program>`, then run the program. Every `AllocMem` and `AllocVec` it makes is
  recorded with its size and where in the program it came from; a `FreeMem` or `FreeVec` by any
  task removes the entry. `LEAK` shows what is left - once the program has ended, that is what
  it leaked:

      the program has ended, so everything below was LEAKED (5 blocks, 3151 bytes known):
        #20   AllocMem      100 bytes at 0x08210dd8  from leaktest seg 0 +0x144
        #23   AllocVec     2000 bytes at 0x0821795c  from leaktest seg 0 +0x162
        #31   AllocVec       52 bytes at 0x0820d7e4  from ROM 0xf96848  <- called from leaktest seg 0 +0x1c8

  The last line is a file that was never closed: dos allocated its handle, and the report still
  points at the `Open()` in the program, by finding the first real return address into the
  program's code on its stack.
- `LEAK STOP` stops recording and keeps the report; `LEAK OFF` also gives exec back its own
  functions.
- How it works: small stubs for the four exec functions go into memory of their own that is
  never freed, and always do the real work; they only call the tracker while a harness has it
  switched on, and a harness that exits waits until no task is inside it. So stopping or
  updating the harness mid-run is safe. The tracker never allocates and uses very little stack -
  it runs on whichever task allocates.
- `leaktest` (in `amiga/`) leaks on purpose, to try it.

**New in 1.15** - catch crashes:

- `CRASH ON` watches every task that has no trap handler of its own. When one crashes (an
  illegal instruction, divide by zero, an address error, line-A/F, `TRAP #n`, ...) the harness
  records the exception, all 16 registers, the CPU's exception frame with its fault address,
  the top of the task's stack and its name.
- Then it **suspends the program** - leaves it asleep, holding what it had - so the machine
  carries on. (A crashing task normally gets a dead-end alert: a guru, then a reboot, which
  also loses the evidence.) Crashes that can't be resumed that way - bus-error frames, a
  68000's bus/address errors, crashes in supervisor mode - are recorded and passed on as
  before. `CRASH PASS` records every crash and always passes it on.
- `CRASH` alone shows the last four, newest first: what happened, the PC (and, for a divide by
  zero, the instruction that did it), which program and offset it was in, the registers,
  the stack, and a disassembly with the faulting instruction marked. `TASKS` marks the
  suspended programs. `CRASH OFF` gives the OS back its own handler; `CRASH CLEAR` empties
  the record.
- The catcher lives in memory of its own that is never freed, so a harness restart or
  `UPDATE` keeps both it and the records (tasks point at it, so it must outlive the harness).
  It is 68000 code and reads a task's stack only when the stack pointer is inside that task's
  own stack.
- `crashtest illegal|div0|odd|trap|linef` (in `amiga/`) crashes on purpose, to try it.

**New in 1.14** - look inside the machine:

- `PEEK <addr> <len> [file]` dumps memory and `POKE <addr> <hexbytes>` writes it. Reads
  only touch memory exec knows about (any RAM, chip RAM from address 0) and the Kickstart
  ROM, and writes only RAM: a read of an unmapped address can hang real hardware, and some
  CIA and custom-chip registers change state when they are read, so those are refused.
- `TASKS` lists every task and process: priority, state, the signals it waits for, its
  stack size and how much is in use, and where its code segments sit in memory.
- `LIBS`, `DEVS` and `RESOURCES` list name, version, open count and base address.
- `DISASM <addr> [count] [--a6 <library>]` disassembles 68k code on the PC (Capstone) and
  names the library calls. It follows `a6`: `movea.l 4.w,a6` is exec, and when code loads
  a6 from a variable (`movea.l _DOSBase,a6`) it reads that variable and matches the base
  against the running libraries, so `jsr -$1e(a6)` comes out as `dos/Open`. The names come
  from the NDK's .fd files, read where your NDK is (set `NH_FD_DIR`, or it looks in the
  usual amiga-gcc place); they are not shipped with NetHarness.
- `WHERE <addr>` says which running program's code an address is in, and the offset.
- Needs `pip install capstone` on the PC for DISASM; everything else needs nothing new.

**New in 1.13** (the Amiga program):

- The harness survives its TCP/IP stack going away. When the stack's errors
  say it is gone (ENETDOWN and the like - a stack whose core crashed, or one
  stopped without telling its clients), the harness closes its sockets and
  bsdsocket.library and waits for the stack to come back, as it does at boot.
  Before, a failing WaitSelect() was retried at once, forever, at priority
  20: the mouse still moved but the harness never answered again, and the
  machine needed a reboot. Ctrl-C still ends the harness.

**New in 1.12** (the PC window only - the Amiga program is unchanged and still
reports 1.11):

- A click in `nhgui.py` is sent at once, ahead of any picture still being
  fetched, and the window waits a moment before fetching the next one so that
  a double-click is not split by a picture in between. Clicks felt hit-and-miss
  on a real A4000 before: they queued behind slow pictures.
- The status line says "Click sent..." when a click goes out, and a second
  picture follows a few seconds later, when a program it started has had time
  to open its window.
- The second click of a double-click goes straight out, without moving the
  pointer first: moving it took long enough on a 68060 for Workbench to see
  two single clicks.
- `test_dblclick.py` double-clicks the way a hand does - uneven gaps, a few
  pixels of wobble, at any moment of the refresh - and counts how many open
  the drawer.

**New in 1.11:**

- `EXEC` with a task priority: `nhctl.py --pri N EXEC ...` (-20..19). The
  commands the harness starts used to run at priority 0, behind any
  full-screen game that never waits; `--pri 5` gets a grab or a `Status` in
  ahead of it.
- `nhctl.py STATUS` and `WAITUP [secs]`: is the Amiga up, and wait until it
  is (see "Is it up?" below).
- `nhgui.py`: a window on the PC that shows the Amiga's front screen and
  passes on clicks, drags, menu picks and typing (see below).
- Screenshots and pointer positions are of the **front** screen - what the
  monitor shows. Before, they were of the active screen: with a game's
  screen active behind the Workbench the picture went on showing the game.

**New in 1.10:**

- `EXEC` can no longer wedge the harness: the command runs in a helper
  process with a time limit (`nhctl.py --timeout SECS`, default 120). When it
  runs out the command gets Ctrl-C and you get its output so far with
  `rc=TIMEOUT`; one that ignores Ctrl-C is left running and named in the reply,
  and the harness carries on serving.
- One harness per port: a second copy on a port that already has a live
  harness exits at once (a doubled line in `S:User-Startup` is harmless).
- `RELOAD` updates and restarts the binary the harness was started from, on
  its own port - before, it assumed `C:netharness` on 7800. `nhctl.py UPDATE
  <file>` stages the new build in the right place and reloads in one step.
- `nhctl.py VERSION` asks the harness its version, path and port. Older
  harnesses still work with the new `nhctl.py`.

**New in 1.9:**

- Injected keys carry every qualifier, not only Shift: `KEY` presses of Ctrl
  (0x63), Alt (0x64/0x65) and Amiga (0x66/0x67) now reach the following keys
  as combinations. So Amiga-key menu shortcuts (e.g. Right Amiga + E opens
  Workbench's Execute Command), Alt-key hotkeys and Ctrl-key commands can be
  tested: `KEY 0x67 1`, `PRESSKEY 0x12`, `KEY 0x67 0`. The host tool now takes key
  codes in decimal or `0x` hex.

**New in 1.8:**

- Client allowlist: only controllers listed in `ENV:NetHarness.allow` may
  connect. Entries are exact IPv4 addresses (`192.168.1.10`), prefixes ending
  in a dot (`192.168.1.`) or `*`, separated by spaces or newlines; loopback is
  always allowed. The file is re-read on every connect. Put it in `ENVARC:` too
  so it survives a reboot:
  `echo "192.168.1.10" >ENVARC:NetHarness.allow` + `copy ENVARC:NetHarness.allow ENV:`.
  With no file the harness still accepts anyone (so an upgrade can't lock you
  out) and logs a warning to `T:netharness.log` on each connect.
- Length-field hardening: a GETFILE/PUTFILE/EXEC length too big for the
  command buffer now drops the client instead of wrapping negative and walking
  the parser out of its buffer (stray bytes could run as commands).

**New in 1.7:**

- Bring-up retries forever instead of giving up after 60 s: 2 s apart for the
  first minute, then every 10 s (CTRL-C aborts the wait). The harness now
  self-connects whenever its network path appears late - a companion Pi still
  booting, WiFi rejoining, or the stack restarting - with no manual run needed
  at the machine.

**New in 1.6:**

- Send-stall guard: a controller that vanishes mid-transfer no longer freezes
  the harness in a blocking `send()` - every send waits for writability first
  (20 s cap), then the dead client is dropped.
- Idle-client reaper: a silent client is disconnected after 600 s, so a
  controller that died without closing can't hold the single client slot.

**New in 1.5:**

- Plays fair with the TCP/IP stack's own shutdown: the harness blocks in
  `WaitSelect` on socket **and** break signal, so a stack shutdown (Roadshow's
  `NetShutdown`) sees it release `bsdsocket.library` immediately instead of
  timing out against a blocked `accept()`/`recv()` and deferring the teardown.

**New in 1.4:**

| Command | |
|---|---|
| `RELOAD` | Apply a staged `C:netharness.new` and **restart the harness in place - no machine reboot** |

**New in 1.3** — stop guessing pixels:

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

**Requirements:** a 68020 or better (the release binary is built `-m68020`),
AmigaOS 3.0 or newer (screen capture uses graphics.library V39), and any TCP/IP
stack providing `bsdsocket.library` — Roadshow, AmiTCP, Miami, a314bsd…

**On the Amiga**, double-click the `Install` icon in the NetHarness drawer, or
from a Shell in that drawer:

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

## A remote-control window (`nhgui.py`)

![nhgui showing a Workbench screen](docs/nhgui.png)

`python nhgui.py [name | host[:port]]` (or double-click `nhgui.cmd` on
Windows) opens a window that shows the Amiga's front screen and passes on
what you do to it:

- **left button** - click, double-click, drag (a red ring shows where the
  click went: the Amiga's own pointer is a sprite and is not in the picture)
- **right button** - hold it and move, as on the Amiga: the menus drop down,
  the picture follows, release over an item to pick it
- **keyboard** - click the picture, then type: letters, Return, Esc, Tab,
  Backspace, Del, cursor keys, F1-F10 (F12 is Help)

Along the top: which Amiga (pick it by name, or type a name or an address and
press Return; "Name it..." keeps an address under a name), Refresh, automatic
refresh every few seconds, Workbench (left Amiga + N), Next screen (left
Amiga + M), Status, Save picture, Release keys, Reboot. Along the bottom: an
AmigaDOS command line, a priority for the command, and its output.

It is not a replacement for VNC or a remote desktop: there is no live view,
just a fresh picture after each thing you do and every few seconds (about a
quarter of a second for a 320x200 screen, two to three for 800x600 in true
colour from a 68060). It is for looking in on an Amiga in another room and
working it now and then - starting a program, answering a requester, seeing
what a test left on the screen.

`python test_nhgui.py host[:port] [x y]` drives the window's own mouse and
keyboard handlers against a live Amiga (a double-click on the RAM Disk icon
at x, y; a window drag; a menu pick with the right button; typing into a
Shell) and checks the result on the Amiga.

Each action is one short connection, so `nhctl.py` and scripts can use the
same Amiga in between. Names for your machines go in `~/.nhgui.json`:

```json
{"machines": {"A4000": "192.168.1.32:7800", "A1200": "192.168.1.33:7800"}}
```

It needs Python 3 with Tk and Pillow.

## Is it up? (`STATUS`, `WAITUP`) - 1.11

`nhctl.py STATUS` tells apart the three things a silent Amiga can be:
nothing listening on the port (booting, harness not started), connected but
no reply (busy with a command that has taken the machine over, or frozen)
and unreachable. `WAITUP [secs]` waits for it to answer, e.g. after `REBOOT`.
A ping is no substitute: a network card with its own processor can go on
answering pings while AmigaOS sits at the insert-disk screen.

`--pri N` (harness 1.11+) gives the `EXEC`'d command a task priority. A
full-screen game that never waits shares the processor with everything at
priority 0; `--pri 5 EXEC ...` gets a command in ahead of it.

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
  here (an A2000) lands on a grey screen and needs a power cycle. `RELOAD` applies a staged `<harness path>.new`
  (`C:netharness.new` before 1.10) and restarts the harness in place instead, which is what rebooting was
  being used for anyway; `nhctl.py UPDATE <file>` stages and reloads in one go.
  It applies the update *while still serving*, so a failed copy leaves the
  working build running rather than bricking your remote access.
- **`REBOOT` flushes filesystems first.** `ColdReboot()` resets instantly, and
  without an explicit flush any file written moments earlier is quietly lost —
  which looks uncannily like the file "reverting" after a reboot.
- **DOS requesters are suppressed** (`pr_WindowPtr = -1`). A command touching a
  missing volume would otherwise raise "Please insert volume…" and block the
  single-threaded harness with no way to dismiss it remotely.
- **`EXEC` has a time limit (1.10).** The command runs in a helper process and
  the harness waits at most the limit (`nhctl.py --timeout SECS`, default 120;
  a 1.9-style `EXEC` gets 10 minutes). When it runs out, the command is sent
  Ctrl-C and you get its output so far with `rc=TIMEOUT`; one that ignores
  Ctrl-C is left running (the note names it) and the harness carries on.
  Before 1.10 a hung command wedged the harness until someone reached the
  machine. Long-lived programs still belong in `EXEC run >NIL: <program>`.
- **One harness per port (1.10).** A second copy started on a port that
  already has a live harness exits at once instead of retrying forever, so a
  doubled line in `S:User-Startup` costs nothing. `RELOAD` hands the port over
  as before; a copy on a spare port (`netharness 7810`) runs alongside.
- **`nhctl.py VERSION`** asks a 1.10+ harness its version (`HELLO`); an older
  one ignores the question and answers `PING`, which is how the client knows
  whether to use the time-limited `EXEC`.
- There is **no authentication**. It executes commands as sent — use it on a
  network you trust, not a public one.

## Which version is installed?

The binary carries a standard AmigaDOS version cookie:

```
$ nhctl.py --host <ip> EXEC "version C:netharness full"
netharness 1.11 (5.10.2026)
```

(The 1.12 package carries the 1.11 binary: only the PC window changed. The
1.10.1 package carried the 1.10 binary: it only added the installer and drawer
icons.)

## What is in a release

- `NetHarness-<version>.lha` - for the Amiga: the `NetHarness` drawer with the
  harness, its installer and this README (and `nhctl.py`, for reference).
- `NetHarness-<version>.zip` - the same drawer as a zip, for unpacking on a PC.
- `NetHarness-<version>-PC.zip` - the PC side on its own: `nhctl.py`,
  `nhgui.py` (+ `nhgui.cmd`), `test_nhgui.py`, `test_dblclick.py`, `nhmcp.py`, this README and
  the licence. It needs Python 3; the window also needs Tk and Pillow
  (`pip install pillow`).

## Building

```
cd amiga && make        # needs the bebbo amiga-gcc cross-compiler
```

The Amiga side is a single C file using only standard `bsdsocket.library`
calls, built `-m68020` — change that to `-m68000` in `amiga/Makefile` if you
need it to run on a stock 68000 machine.

## Licence

MIT — see [LICENSE](LICENSE).

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
Copy netharness C:
Run >NIL: C:netharness
```

It listens on TCP port **7800**. To start it at every boot, add that `Run` line
to the end of `S:User-Startup`, after your TCP/IP stack comes up.

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

## Building

```
cd amiga && make        # needs the bebbo amiga-gcc cross-compiler
```

The Amiga side is a single C file using only standard `bsdsocket.library`
calls, built `-m68020`.

## Licence

MIT — see [LICENSE](LICENSE).

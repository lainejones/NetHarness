# NetHarness — TCP test/remote-control harness for the A4000 (Roadshow)

Port of [A314TestHarness](../A314TestHarness) from the A314 packet link to plain
TCP over the resident `bsdsocket.library`. No Pi middleman: `amiga/netharness`
LISTENS on TCP port **7800**; `host/nhctl.py` connects directly from any box.

Built because the A4000 has no A314 board (its TF4060 a314 bridge — see the
`a4060` project — is unreleased/unfinished), but it *does* have Ethernet+Roadshow.

## What it does (over the A314 harness)
- Same input injection (`input.device IND_WRITEEVENT`, qualifier tracking) and
  planar screenshot streaming, same byte-stream reassembly. Proven code, carved out.
- **Every input command is ACKed after injection** — `nhctl` waits for the ack, so
  its `OK` means *delivered and injected* (the A1200 harness's biggest blind spot,
  fixed by design here).
- **`EXEC <AmigaDOS command>`** — runs via `SystemTags()` (child at pri 0, output
  captured to `T:netharness.out`) and returns rc + output. Replaces all the
  "drive the Execute-Command requester with synthetic clicks" fragility. ARexx
  scripting rides on it: `EXEC rx "ADDRESS IBROWSE.1; GOTOURL https://..."`.
- **`CMD_PING`** liveness check.
- Screenshot decode (host side) handles **interleaved bitmaps** (AGA 3.2 Workbench:
  `bpr == row_bytes*depth`, `Planes[p] = base + p*row_bytes` — planes[0] carries the
  whole frame) and deep screens (grayscale LUT = reversed bit order so low pens are
  distinguishable). The A314 harness never needed either.

## Usage
```
python3 host/nhctl.py [--host 192.168.50.32] [--port 7800] COMMAND [args]
  PING | HOME | MOVE dx dy | MOVETO x y | BUTTON b s | CLICK x y [b]
  KEY c s | PRESSKEY c | TYPE text | CLEARFIELD [n] | RESETINPUT
  SCREENSHOT [out.png] | EXEC cmd... | REBOOT | --batch (stdin lines, one conn)
v1.3 — stop guessing pixels:
  POINTER | UITREE | MENUS | SCREENS
  UICLICK <text> | UICLICKID <id> | MENUSEL <menu> <item>
  SHOTREGION x y w h [out] | REGIONSUM x y w h | WAITCHANGE x y w h [secs]
  GETFILE <amiga> <local> | PUTFILE <local> <amiga>
```

## v1.3 — the semantic layer (2026-08, all verified on the real A4000)
Closes the gap against `thomas-luebker/amimcp`; **drive the GUI by what things
ARE, and verify what actually happened**, instead of guessing coordinates.
- **`POINTER`** — read the pointer back (the sprite is invisible in
  screenshots, so this was the single biggest blind spot). **`MOVETO`/`CLICK`
  are now CLOSED-LOOP**: hop, read back, correct, land exactly.
- **`UITREE`** — the front screen's windows+gadgets: id, kind, SCREEN-ABSOLUTE
  bounds, label (IntuiText chain), and string-gadget contents.
  **`UICLICK <text>`** clicks by label/contents substring, **`UICLICKID <id>`**
  by GadgetID. **`MENUS`** dumps the menu strip; **`MENUSEL <menu> <item>`**
  picks an item by name via its keyboard shortcut.
  **Limit (measured, not theoretical):** some gadgets expose no text to
  Intuition at all — OS 3.2 Prefs' Save/Use/Cancel buttons report an empty
  label whether `GadgetText` is read as an `IntuiText` *or* as a
  `GFLG_LABELSTRING` string, because those apps draw the text themselves.
  Slider labels ("Hours") and string-gadget contents ("2026") do come through.
  **So: `UICLICKID <id>` for buttons, `UICLICK <text>` where text exists.**
  amimcp has the same wall — it defers custom-drawn content to screenshots.
- **`SHOTREGION`/`REGIONSUM`/`WAITCHANGE`** — a region grab is ~8x smaller than
  a full frame (4.7 KB vs 38 KB here), and a 4-byte checksum answers "has it
  redrawn?" so waits are *observed* instead of a blind 150 ms guess.
- **`GETFILE`/`PUTFILE`** — binary-safe file transfer, so a freshly built
  binary deploys through the harness itself (no NAS:/share in the middle).
  PUTFILE streams: the payload is far bigger than the 1 KB command buffer.
- **`netharness [port]`** — the listen port is now an argument. **Test a new
  build on a spare port next to the running one**; never hot-swap the harness
  carrying your only remote control.

### Mouse acceleration: the trap this all exposed
Intuition **scales** any mouse delta of **4 or more** (~4x on this A4000);
deltas **<= 3 pass through 1:1**. Consequences, both hit for real:
- The old `HOME` sent a single `-16384` delta. With acceleration ON that
  overflows the signed 16-bit maths and the pointer slams into the **bottom
  right** corner instead of the top left — after which every click lands
  somewhere random and nothing looks broken. `do_home_mouse()` now walks in
  40 x `-1000` steps, which cannot overflow however they are scaled.
- One big positive delta overshoots the same way, so `move_to()` is a
  two-phase controller: coarse hops of `remaining / gain` where **`gain` is
  learned from how far the pointer actually moved** (starts at 4, measures 1
  when acceleration is off), then <= 3 px steps to land exactly. Verified
  pixel-exact with acceleration **on and off**.
Sequential request/response per command; `EXEC` timeout 120s (a hung synchronous
command hangs the harness — launch long-lived programs with `EXEC run >NIL: ...`).

## Deploy (real A4000)
- **Lives at `SYS:NetHarness/netharness` on the A4000** (NOT `SYS:netharness` —
  AmigaDOS is case-insensitive and `SYS:` already has a `NetHarness` *drawer*,
  so `copy SYS:netharness ...` silently copies the DRAWER).

### THE LIVE BINARY IS `C:netharness` — installed via `C:nhboot`
Corrected 2026-08-12 after nearly updating the wrong file. Both the A4000 and
the A2000 autostart from `S:User-Startup` → `execute C:nhboot`, and nhboot is a
**self-updating** script:
```
if exists C:netharness.new
  copy C:netharness.new C:netharness / delete the .new / protect +e
endif
run >NIL: C:netharness
```
So **the deploy is: put the new build at `C:netharness.new`, then `REBOOT`** —
the swap happens at boot, which sidesteps the fact that a harness blocked in
`recv()` cannot be killed remotely. `SYS:NetHarness/` is only a staging/backup
drawer (it still holds an older copy + `netharness.v12bak`); **editing it does
nothing**, because nhboot never looks there.

### Which build is on a machine?
The binary carries a standard version cookie (`NH_VERSION`/`NH_VERDATE` in
netharness.c), so this is a one-liner — no more guessing from file size:
```
nhctl.py --host <ip> EXEC "version C:netharness full"   ->  netharness 1.3 (08/11/26)
```
The startup line in `T:netharness.log` carries it too, which tells you what is
actually *running* rather than what is on disk. **Bump `NH_VERSION` whenever the
wire protocol or command set changes.**

### Safe update procedure (do NOT skip the spare port)
The harness takes an optional port argument, so a new build can be proven while
the live one keeps serving:
```
nhctl.py --host <ip> PUTFILE <newbuild> RAM:nhtest
nhctl.py --host <ip> EXEC "protect RAM:nhtest +e"
nhctl.py --host <ip> EXEC "run >NIL: RAM:nhtest 7801"
nhctl.py --host <ip> --port 7801 PING / POINTER / UITREE / ...   # prove it
nhctl.py --host <ip> EXEC "copy RAM:nhtest C:netharness.new"     # then commit
nhctl.py --host <ip> REBOOT
```
Learned the hard way: an unverified build was made the autostart binary and the
machine had to be recovered by hand. Verify on 7801 first, every time.

- Transfer routes if `PUTFILE` is not available (old harness): the `NAS:` SMB
  share (`Z:\` on this PC = `\\SynologyNAS\Storage`), or `C:wget`. If `NAS:` is
  not mounted, mount it **without touching their config** by synthesising a
  mountlist from the existing DOSDriver:
  `Echo >RAM:m "NAS:"` / `Type SYS:SMB >>RAM:m` / `Echo >>RAM:m "#"` /
  `Mount NAS: FROM RAM:m`  (`Mount NAS: FROM SYS:SMB` fails — DOSDriver format).
- Then drive from the PC: `python3 host/nhctl.py PING` (default host .32).

## The fleet (all on netharness 1.3, verified 2026-08-12)
| Machine | IP | Screen | Notes |
|---|---|---|---|
| A4000 | 192.168.50.32 | 1024x768x24 RTG | 68040, ZZ9000 |
| A2000 | 192.168.50.31 | 800x600x16 RTG (Picasso96) | 68030, X-Surf; OS **3.2.3**, not the 3.1/ECS the old notes claimed |
| A500  | 192.168.50.36 | - | powered off; **needs a `-m68000` build** (the Makefile is `-m68020`) |
Screenshots need Pillow: WSL's python3 here has no pip/PIL, so run nhctl under
**Windows python** for `SCREENSHOT` (input/EXEC/PING are fine under either).

## WinUAE test bench (validated 2026-07-25)
Config `a4knew323.uae` (A4000 KS3.2.3, 68040, DH0: = `C:\Amiga\A4k323`) +
`-s bsdsocket_emu=true` override:
```
winuae64.exe -f "C:\...\Configurations\a4knew323.uae" -s bsdsocket_emu=true -s use_gui=no
```
`netharness` lives at `WorkBench:netharness` in that bench and is launched from its
`S:User-Startup` (line at the end; backup at `S:User-Startup.nh-backup`). With
bsdsocket_emu the port appears on the HOST at `127.0.0.1:7800` — full loop
(PING/EXEC/CLICK/TYPE/SCREENSHOT) verified there, including `echo` round-trip
read back off a screenshot. NOTE: launching the config WITHOUT the bsdsocket_emu
override makes netharness retry for 60s and exit silently — harmless.
- WinUAE `-f` path with spaces: quote the path INSIDE the argument when using
  PowerShell Start-Process, or the config silently fails to load (boots KS1.3
  default quickstart instead — check the window title shows `[config.uae]`).

## Gotchas / conventions inherited from A314TestHarness
- Menu navigation: rest on the title (`MOVETO x 2`), then relative `MOVE 0 dy`,
  then release — never single-jump below the menu bar.
- Mouse pointer is a hardware sprite: never visible in screenshots.
- String gadgets: `CLEARFIELD` before typing (cursor lands where you click).
- 150ms settle delay before screenshots (Intuition redraw race) — built into nhctl.

## Build
`cd amiga && make` under WSL (bebbo amiga-gcc on PATH). Uses a314bsd's
`include/netinclude` + `inline/bsdsocket.h` (AmiTCP-standard LVOs — Roadshow OK).

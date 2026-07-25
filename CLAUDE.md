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
```
Sequential request/response per command; `EXEC` timeout 120s (a hung synchronous
command hangs the harness — launch long-lived programs with `EXEC run >NIL: ...`).

## Deploy (real A4000)
- Staged at `Z:\Amiga\NetHarness\` (netharness + nhctl.py).
- On the A4000: `copy <share>/NetHarness/netharness SYS:` then
  `run >NIL: SYS:netharness`. For reboot-survival add that `run` line to the END
  of `S:User-Startup` (after Roadshow's network start; the 30×2s bring-up retry
  covers ordering anyway).
- Then drive from the PC: `python3 host/nhctl.py PING` (default host .32).

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

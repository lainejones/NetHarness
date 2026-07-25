# NetHarness — TCP test harness for the A4000 (Roadshow).
# Build under WSL with the bebbo amiga-gcc toolchain on PATH.
# Reuses a314bsd's AmiTCP-compatible netinclude/ + inline/bsdsocket.h
# (standard bsdsocket LVOs — works against Roadshow or any AmiTCP-API stack).

CC      = m68k-amigaos-gcc
CFLAGS  = -m68020 -O2 -noixemul -Wall -fomit-frame-pointer \
          -I../../a314bsd/include \
          -I../../a314bsd/include/netinclude

netharness: netharness.c
	$(CC) $(CFLAGS) -o $@ $< -lamiga

clean:
	rm -f netharness

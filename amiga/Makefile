# NetHarness — remote-control harness for AmigaOS over TCP.
# Build under WSL with the bebbo amiga-gcc toolchain on PATH.
# Self-contained: uses only the NDK's own AmiTCP/bsdsocket headers, so it
# builds against any stack providing bsdsocket.library (Roadshow, AmiTCP,
# Miami, a314bsd).

CC      = m68k-amigaos-gcc
CFLAGS  = -m68020 -O2 -noixemul -Wall -fomit-frame-pointer \

netharness: netharness.c
	$(CC) $(CFLAGS) -o $@ $< -lamiga

clean:
	rm -f netharness

# Release archives.  Staged under dist/ because a case-insensitive build host
# collides "NetHarness" with the "netharness" binary.  The .lha is what Amiga
# users expect; note the common Linux "lha" (lhasa) only EXTRACTS, so the
# release .lha is built on an Amiga:  LhA -r a NetHarness-1.0.lha NetHarness
VERSION  = 1.0
DISTFILE = NetHarness-$(VERSION)

dist: netharness
	rm -rf dist $(DISTFILE).zip
	mkdir -p dist/NetHarness
	cp netharness dist/NetHarness/netharness
	cp ../Install ../README.md ../LICENSE dist/NetHarness/
	cp ../host/nhctl.py dist/NetHarness/
	cd dist && zip -qr ../$(DISTFILE).zip NetHarness || \
		echo "no zip here - build the archive on the host instead"
	rm -rf dist

.PHONY: dist

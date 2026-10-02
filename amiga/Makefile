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
# release .lha is built on an Amiga from the same staging:
#     LhA -r a NetHarness-<version>.lha NetHarness NetHarness.info
# VERSION is the package version; the binary's own version is NH_VERSION in
# netharness.c (the 1.10.1 package carries the 1.10 binary).
VERSION  = 1.10.1
DISTFILE = NetHarness-$(VERSION)

dist: netharness
	rm -rf dist $(DISTFILE).zip
	mkdir -p dist/NetHarness
	cp netharness dist/NetHarness/netharness
	cp ../Install ../Install.info ../README.md ../LICENSE dist/NetHarness/
	cp ../host/nhctl.py dist/NetHarness/
	@# the drawer's own icon goes BESIDE it, or the unpacked drawer is invisible on Workbench
	cp ../drawer.info dist/NetHarness.info
	cd dist && zip -qr ../$(DISTFILE).zip NetHarness NetHarness.info || \
		echo "no zip here - build the archive on the host instead"
	rm -rf dist

.PHONY: dist

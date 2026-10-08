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
# netharness.c.
VERSION  = 1.12
DISTFILE = NetHarness-$(VERSION)

# "make dist" leaves, in ../dist:
#   NetHarness-<v>.zip      the Amiga drawer (binary, installer, README, nhctl.py)
#   NetHarness-<v>-PC.zip   the PC side: nhctl.py, nhgui.py (+ .cmd), the two tests, nhmcp.py
#   stage/                  the Amiga drawer unpacked, to pack with LhA on an Amiga
dist: netharness
	rm -rf ../dist/stage ../dist/$(DISTFILE).zip ../dist/$(DISTFILE)-PC.zip
	mkdir -p ../dist/stage/NetHarness ../dist/stage-pc/NetHarness-PC/docs
	cp netharness ../dist/stage/NetHarness/netharness
	cp ../Install ../Install.info ../README.md ../LICENSE ../dist/stage/NetHarness/
	cp ../host/nhctl.py ../dist/stage/NetHarness/
	@# the drawer's own icon goes BESIDE it, or the unpacked drawer is invisible on Workbench
	cp ../drawer.info ../dist/stage/NetHarness.info
	cd ../dist/stage && python3 -m zipfile -c ../$(DISTFILE).zip NetHarness NetHarness.info
	cp ../host/nhctl.py ../host/nhgui.py ../host/nhgui.cmd ../host/test_nhgui.py ../host/test_dblclick.py ../host/nhmcp.py \
	   ../README.md ../LICENSE ../dist/stage-pc/NetHarness-PC/
	cp ../docs/nhgui.png ../dist/stage-pc/NetHarness-PC/docs/
	cd ../dist/stage-pc && python3 -m zipfile -c ../$(DISTFILE)-PC.zip NetHarness-PC
	rm -rf ../dist/stage-pc

.PHONY: dist

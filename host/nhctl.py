#!/usr/bin/env python3
"""nhctl.py - controller for NetHarness (TCP test harness on the A4000).

Direct TCP client - no Pi middleman.  One command per invocation, or
--batch to read newline-separated commands from stdin over one connection.

  python3 nhctl.py [--host 192.168.50.32] [--port 7800] COMMAND [args...]

Commands (same verbs as the A314 harness ctl.py, plus EXEC):
  PING                      liveness check (waits for the Amiga's ack)
  HOME                      pointer to (0,0)
  MOVE dx dy                relative pointer move
  MOVETO x y                absolute move (home + one delta)
  BUTTON b state            b: 0=L 1=R 2=M   state: 1=down 0=up
  CLICK x y [b]             moveto + press + release
  KEY code state            raw Amiga keycode
  PRESSKEY code             press + release
  TYPE text...              ASCII -> raw keycodes (shift handled)
  CLEARFIELD [maxlen]       right-arrow to end, then backspace it all
  SCREENSHOT [out.png]      capture the front screen (default nh_shot.png)
  EXEC command...           run an AmigaDOS command, print rc + output
  RESETINPUT                release any held buttons/qualifiers
  REBOOT                    ColdReboot() - connection drops, machine restarts

v1.3 - stop guessing pixels:
  POINTER                   where is the pointer really? (x y on WxH)
  UITREE                    dump windows + gadgets of the front screen
  MENUS                     dump the front window's menu strip
  SCREENS                   list open screens, front to back
  UICLICK <text>            click the gadget whose label/contents match <text>
                            (substring, case-insensitive) - no coordinates
  UICLICKID <id>            click the gadget with that GadgetID
  MENUSEL <menu> <item>     pick a menu item by NAME (substring match)
  SHOTREGION x y w h [out]  capture just a region
  REGIONSUM x y w h         4-byte checksum of a region
  WAITCHANGE x y w h [secs] block until that region's checksum changes
  GETFILE <amiga> <local>   copy a file off the Amiga
  PUTFILE <local> <amiga>   copy a file onto the Amiga (deploy a binary!)
  RELOAD                    apply C:netharness.new + restart in place
                            (no reboot; REBOOT greyscreens some machines)

Every input command waits for the Amiga's RESP_ACK, so "OK" here means
DELIVERED AND INJECTED (the fix for the A1200 harness's biggest blind spot).
MOVETO/CLICK are CLOSED-LOOP: after the jump we read the pointer back and
correct it, because Intuition's mouse acceleration mangles a one-shot relative
delta - the root cause of countless "the click landed a few pixels off and you
can't see why" failures (the pointer is an invisible hardware sprite).
"""

import socket
import struct
import sys
import time

DEFAULT_HOST = '192.168.1.32'       # set to your Amiga's IP, or use --host
DEFAULT_PORT = 7800

CMD_MOUSE_MOVE, CMD_MOUSE_BUTTON, CMD_KEY, CMD_HOME_MOUSE = 1, 2, 3, 4
CMD_SCREENSHOT, CMD_REBOOT, CMD_RESET_INPUT, CMD_EXEC, CMD_PING = 5, 6, 7, 8, 9
CMD_POINTER, CMD_UITREE, CMD_MENUS, CMD_SCREENS = 10, 11, 12, 13
CMD_REGION_SUM, CMD_SHOT_REGION, CMD_GETFILE, CMD_PUTFILE = 14, 15, 16, 17
CMD_RELOAD = 18
RESP_SCREENSHOT_HDR, RESP_ACK, RESP_EXEC = 0x81, 0x82, 0x83
RESP_POINTER, RESP_TEXT, RESP_FILE, RESP_SUM = 0x84, 0x85, 0x86, 0x87

SHIFT_CODE = 0x60
RAWKEY_RIGHT, RAWKEY_BACKSPACE, RAWKEY_RETURN = 0x4E, 0x41, 0x44
RAWKEY_RAMIGA = 0x67        # right Amiga = the menu-shortcut qualifier

# Raw Amiga USA keymap (char -> (code, needs_shift)) - ported verbatim from
# A314TestHarness/pi/testharness.py.
KEYMAP = {
    'a': (0x20, False), 'b': (0x35, False), 'c': (0x33, False), 'd': (0x22, False),
    'e': (0x12, False), 'f': (0x23, False), 'g': (0x24, False), 'h': (0x25, False),
    'i': (0x17, False), 'j': (0x26, False), 'k': (0x27, False), 'l': (0x28, False),
    'm': (0x37, False), 'n': (0x36, False), 'o': (0x18, False), 'p': (0x19, False),
    'q': (0x10, False), 'r': (0x13, False), 's': (0x21, False), 't': (0x14, False),
    'u': (0x16, False), 'v': (0x34, False), 'w': (0x11, False), 'x': (0x32, False),
    'y': (0x15, False), 'z': (0x31, False),
    '1': (0x01, False), '2': (0x02, False), '3': (0x03, False), '4': (0x04, False),
    '5': (0x05, False), '6': (0x06, False), '7': (0x07, False), '8': (0x08, False),
    '9': (0x09, False), '0': (0x0A, False),
    ' ': (0x40, False), '\n': (0x44, False), '\r': (0x44, False),
    ':': (0x29, True), ';': (0x29, False),
    '/': (0x3A, False), '?': (0x3A, True),
    '.': (0x39, False), '>': (0x39, True),
    ',': (0x38, False), '<': (0x38, True),
    '-': (0x0B, False), '_': (0x0B, True),
    '=': (0x0C, False), '+': (0x0C, True),
    '!': (0x01, True), '@': (0x02, True), '#': (0x03, True), '$': (0x04, True),
    '%': (0x05, True), '^': (0x06, True), '&': (0x07, True), '*': (0x08, True),
    '(': (0x09, True), ')': (0x0A, True),
    '"': (0x2A, True), "'": (0x2A, False),
}
for _c in 'abcdefghijklmnopqrstuvwxyz':
    KEYMAP[_c.upper()] = (KEYMAP[_c][0], True)


class NetHarness:
    def __init__(self, host, port, timeout=30):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)

    # ---- low level -------------------------------------------------------

    def _recv_exactly(self, n):
        buf = b''
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError('Amiga closed the connection')
            buf += chunk
        return buf

    def _input_cmd(self, payload):
        """Send one input command and wait for its RESP_ACK - sequential
        request/response, so OK really means injected."""
        self.sock.sendall(payload)
        b = self._recv_exactly(1)
        if b[0] != RESP_ACK:
            raise ConnectionError(f'expected ACK, got 0x{b[0]:02x}')

    # ---- input primitives --------------------------------------------------

    def ping(self):
        self._input_cmd(bytes([CMD_PING]))

    def home(self):
        self._input_cmd(bytes([CMD_HOME_MOUSE]))

    def move(self, dx, dy):
        self._input_cmd(bytes([CMD_MOUSE_MOVE]) + struct.pack('>hh', dx, dy))

    def pointer(self):
        """Actual pointer position -> (x, y, screen_w, screen_h)."""
        self.sock.sendall(bytes([CMD_POINTER]))
        r = self._recv_exactly(9)
        if r[0] != RESP_POINTER:
            raise ConnectionError(f'expected pointer response, got 0x{r[0]:02x}')
        return struct.unpack('>hhhh', r[1:9])

    # Intuition's mouse acceleration multiplies any delta of 4 or more (~4x on
    # a default A4000 setup); deltas of 3 or less pass through UNSCALED.  Both
    # facts are load-bearing below - measured, not assumed.
    ACCEL_FREE = 3          # largest delta that is never accelerated
    MOVE_CAP   = 2000       # never request more than this (overflow guard)

    def move_to(self, x, y, rounds=60):
        """CLOSED-LOOP absolute move, correct with acceleration on or off.

        Two phases:
          coarse - request (remaining / gain) where `gain` is LEARNED from how
                   far the pointer actually moved last hop.  Starts at 4 (the
                   typical accelerated ratio); with acceleration off it
                   measures 1 after one hop and converges immediately.
          fine   - once within ACCEL_FREE px, step by <=3 at a time, which
                   Intuition passes through 1:1, so we land exactly.

        Why this matters: a single large delta is scaled AND can overflow the
        signed 16-bit maths, flinging the pointer into the opposite corner -
        after which every click silently lands in the wrong place.  Reading the
        pointer back after each hop makes that impossible to miss."""
        self.home()
        px, py = self.pointer()[:2]
        gain = 4.0
        for _ in range(rounds):
            dx, dy = x - px, y - py
            if dx == 0 and dy == 0:
                return True
            if abs(dx) <= self.ACCEL_FREE and abs(dy) <= self.ACCEL_FREE:
                rx, ry = dx, dy                       # fine: 1:1, lands exact
            else:
                rx = int(dx / gain) or (1 if dx > 0 else -1 if dx else 0)
                ry = int(dy / gain) or (1 if dy > 0 else -1 if dy else 0)
                rx = max(-self.MOVE_CAP, min(self.MOVE_CAP, rx))
                ry = max(-self.MOVE_CAP, min(self.MOVE_CAP, ry))
            self.move(rx, ry)
            nx, ny, _w, _h = self.pointer()
            moved, asked = abs(nx - px) + abs(ny - py), abs(rx) + abs(ry)
            # learn the real gain from a coarse hop that wasn't edge-clamped
            if asked > self.ACCEL_FREE and moved:
                g = moved / asked
                if 0.2 <= g <= 16:
                    gain = (gain + g) / 2.0
            if (nx, ny) == (px, py) and asked:
                break                                  # clamped at an edge
            px, py = nx, ny
        return (px, py) == (x, y)

    def button(self, b, down):
        self._input_cmd(bytes([CMD_MOUSE_BUTTON, b, 1 if down else 0]))

    def click(self, x, y, b=0):
        self.move_to(x, y)
        self.button(b, True)
        self.button(b, False)

    def key(self, code, down):
        self._input_cmd(bytes([CMD_KEY, code, 1 if down else 0]))

    def press_key(self, code):
        self.key(code, True)
        self.key(code, False)

    def type_text(self, text):
        for ch in text:
            entry = KEYMAP.get(ch)
            if entry is None:
                print(f'  (no keymap entry for {ch!r}, skipped)', file=sys.stderr)
                continue
            code, shift = entry
            if shift:
                self.key(SHIFT_CODE, True)
            self.press_key(code)
            if shift:
                self.key(SHIFT_CODE, False)

    def clear_field(self, max_len=64):
        for _ in range(max_len):
            self.press_key(RAWKEY_RIGHT)
        for _ in range(max_len):
            self.press_key(RAWKEY_BACKSPACE)

    def reset_input(self):
        self._input_cmd(bytes([CMD_RESET_INPUT]))

    # ---- semantic layer: address things by WHAT THEY ARE ----------------------

    def _text_cmd(self, cmd):
        self.sock.sendall(bytes([cmd]))
        hdr = self._recv_exactly(5)
        if hdr[0] != RESP_TEXT:
            raise ConnectionError(f'expected text response, got 0x{hdr[0]:02x}')
        n = struct.unpack('>I', hdr[1:5])[0]
        return self._recv_exactly(n).decode('latin-1', 'replace') if n else ''

    def ui_tree(self):
        return self._text_cmd(CMD_UITREE)

    def menus(self):
        return self._text_cmd(CMD_MENUS)

    def screens(self):
        return self._text_cmd(CMD_SCREENS)

    @staticmethod
    def _split_record(line):
        """Split a UITREE line into fields, keeping "quoted strings" whole."""
        out, cur, inq = [], '', False
        for ch in line:
            if ch == '"':
                inq = not inq
                if not inq:
                    out.append(cur); cur = ''
            elif inq:
                cur += ch
            elif ch == ' ':
                if cur:
                    out.append(cur); cur = ''
            else:
                cur += ch
        if cur:
            out.append(cur)
        return out

    def gadgets(self):
        """Parse UITREE into [{id,kind,x,y,w,h,label,text}] (screen coords)."""
        found = []
        for line in self.ui_tree().splitlines():
            if not line.startswith('G '):
                continue
            f = self._split_record(line[2:])
            if len(f) < 6:
                continue
            try:
                gid, kind = int(f[0]), f[1]
                x, y, w, h = (int(f[2]), int(f[3]), int(f[4]), int(f[5]))
            except ValueError:
                continue
            label = f[6] if len(f) > 6 else ''
            text = f[7] if len(f) > 7 else ''
            found.append(dict(id=gid, kind=kind, x=x, y=y, w=w, h=h,
                              label=label, text=text))
        return found

    def find_gadget(self, needle=None, gid=None):
        want = (needle or '').strip().lower()
        for g in self.gadgets():
            if gid is not None:
                if g['id'] == gid:
                    return g
            elif want and (want in g['label'].lower() or want in g['text'].lower()):
                return g
        return None

    def ui_click(self, needle=None, gid=None, button=0):
        """Click a gadget by identity instead of guessing pixels."""
        g = self.find_gadget(needle, gid)
        if not g:
            return None
        cx, cy = g['x'] + g['w'] // 2, g['y'] + g['h'] // 2
        self.click(cx, cy, button)
        return g

    def menu_select(self, menu_name, item_name):
        """Pick a menu item by NAME.  Uses its keyboard shortcut when it has
        one (far more reliable than driving the dropdown with the pointer)."""
        mtitle, want_m, want_i = None, menu_name.lower(), item_name.lower()
        for line in self.menus().splitlines():
            if line.startswith('M '):
                f = self._split_record(line[2:])
                mtitle = f[1] if len(f) > 1 else ''
            elif line.startswith('I ') and mtitle and want_m in mtitle.lower():
                f = self._split_record(line[2:])
                if len(f) < 3:
                    continue
                itext = f[2]
                short = f[3] if len(f) > 3 else ''
                if want_i in itext.lower():
                    if short:
                        # RIGHT Amiga (0x67) is the menu-shortcut qualifier;
                        # 0x66 is LEFT Amiga and does nothing here.
                        self.key(RAWKEY_RAMIGA, True)
                        entry = KEYMAP.get(short.lower())
                        if entry:
                            self.press_key(entry[0])
                        self.key(RAWKEY_RAMIGA, False)
                        return itext
                    return None      # no shortcut: caller must drive the menu
        return None

    # ---- regions: cheap "has it redrawn yet?" --------------------------------

    def region_sum(self, x, y, w, h):
        self.sock.sendall(bytes([CMD_REGION_SUM]) + struct.pack('>hhhh', x, y, w, h))
        r = self._recv_exactly(5)
        if r[0] != RESP_SUM:
            raise ConnectionError(f'expected sum, got 0x{r[0]:02x}')
        return struct.unpack('>I', r[1:5])[0]

    def wait_change(self, x, y, w, h, timeout=10.0, poll=0.15):
        """Block until the region's checksum changes.  Replaces the blind
        fixed settle delay with an actual observation."""
        start = self.region_sum(x, y, w, h)
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(poll)
            if self.region_sum(x, y, w, h) != start:
                return True
        return False

    # ---- file transfer -------------------------------------------------------

    def get_file(self, amiga_path, local_path):
        p = amiga_path.encode('latin-1')
        self.sock.sendall(bytes([CMD_GETFILE]) + struct.pack('>H', len(p)) + p)
        hdr = self._recv_exactly(9)
        if hdr[0] != RESP_FILE:
            raise ConnectionError(f'expected file response, got 0x{hdr[0]:02x}')
        status, n = struct.unpack('>iI', hdr[1:9])
        if status != 0:
            raise RuntimeError(f'Amiga could not open {amiga_path} (status {status})')
        data = self._recv_exactly(n) if n else b''
        with open(local_path, 'wb') as f:
            f.write(data)
        return n

    def reload(self):
        """Apply a staged C:netharness.new and restart the harness IN PLACE.
        No machine reboot - ColdReboot() leaves some machines (the A2000) on a
        grey screen. Returns True if it handed off, False if the update could
        not be applied (in which case the OLD harness is still running)."""
        self.sock.sendall(bytes([CMD_RELOAD]))
        tag = self._recv_exactly(1)
        if not tag or tag[0] != RESP_ACK:
            raise RuntimeError('reload: no ack')
        return self._recv_exactly(1)[0] == 1

    def put_file(self, local_path, amiga_path, timeout=180):
        with open(local_path, 'rb') as f:
            data = f.read()
        p = amiga_path.encode('latin-1')
        old = self.sock.gettimeout()
        self.sock.settimeout(timeout)
        self.sock.sendall(bytes([CMD_PUTFILE]) + struct.pack('>H', len(p)) + p +
                          struct.pack('>I', len(data)) + data)
        hdr = self._recv_exactly(9)
        self.sock.settimeout(old)
        if hdr[0] != RESP_FILE:
            raise ConnectionError(f'expected file response, got 0x{hdr[0]:02x}')
        status, _ = struct.unpack('>iI', hdr[1:9])
        if status != 0:
            raise RuntimeError(f'Amiga could not write {amiga_path} (status {status})')
        return len(data)

    # ---- screenshot ----------------------------------------------------------

    def screenshot(self, path='nh_shot.png', settle=0.15, region=None):
        if settle:
            time.sleep(settle)   # let Intuition's async redraw finish
        if region:
            x, y, w, h = region
            self.sock.sendall(bytes([CMD_SHOT_REGION]) + struct.pack('>hhhh', x, y, w, h))
        else:
            self.sock.sendall(bytes([CMD_SCREENSHOT]))
        hdr = self._recv_exactly(8)
        if hdr[0] != RESP_SCREENSHOT_HDR:
            raise ConnectionError(f'expected screenshot hdr, got 0x{hdr[0]:02x}')
        width, height, depth, bpr = struct.unpack('>HHBH', hdr[1:8])

        if depth == 0xFE:
            raise RuntimeError('capture unsupported (no cybergraphics.library / no memory)')

        if depth == 0xFD:
            # True-colour RTG: raw RGB24, width*height*3, no padding.
            from PIL import Image
            data = self._recv_exactly(width * height * 3)
            Image.frombytes('RGB', (width, height), data).save(path)
            return path, width, height, 24

        if depth == 0xFF:
            # v1.2 chunky format: bpr = stride; then ncolors2 + palette + pens
            stride = bpr
            ncol = struct.unpack('>H', self._recv_exactly(2))[0]
            palette = self._recv_exactly(ncol * 3)
            data = self._recv_exactly(stride * height)
            from PIL import Image
            img = Image.frombytes('P', (stride, height), data)
            img.putpalette(palette + bytes(3) * (256 - ncol))
            img = img.crop((0, 0, width, height)).convert('RGB')
            img.save(path)
            return path, width, height, ncol

        planes = [self._recv_exactly(bpr * height) for _ in range(depth)]

        # planar -> chunky -> grayscale PNG.  Two subtleties:
        #  - AGA Workbench screens are usually INTERLEAVED bitmaps: BytesPerRow
        #    covers ALL planes of one display row (bpr == row_bytes*depth) and
        #    Planes[p] = base + p*row_bytes.  In that case the Amiga's per-plane
        #    reads overlap, and planes[0] alone contains the full interleaved
        #    frame - decode from it directly.
        #  - Palette is unknown; map pen->gray by REVERSED bit order so the
        #    low pens (where all the UI lives on a deep screen) get spread
        #    across the brightness range instead of all landing near black.
        from PIL import Image
        row_bytes = (width + 7) // 8
        interleaved = depth > 1 and bpr >= row_bytes * depth

        def gray(pen):
            v = 0
            for b in range(depth):
                if pen & (1 << b):
                    v |= 1 << (7 - b)
            return v

        lut = [gray(p) for p in range(1 << depth)]
        img = Image.new('L', (width, height))
        px = img.load()
        for y in range(height):
            base = y * bpr
            for x in range(width):
                byte_off, bit = (x >> 3), 7 - (x & 7)
                pen = 0
                if interleaved:
                    d = planes[0]
                    for p in range(depth):
                        pen |= ((d[base + p * row_bytes + byte_off] >> bit) & 1) << p
                else:
                    for p in range(depth):
                        pen |= ((planes[p][base + byte_off] >> bit) & 1) << p
                px[x, y] = lut[pen]
        img.save(path)
        return path, width, height, depth

    # ---- EXEC ------------------------------------------------------------------

    def exec_cmd(self, cmdline, timeout=120):
        data = cmdline.encode('latin-1')
        self.sock.settimeout(timeout)
        self.sock.sendall(bytes([CMD_EXEC]) + struct.pack('>H', len(data)) + data)
        hdr = self._recv_exactly(9)
        if hdr[0] != RESP_EXEC:
            raise ConnectionError(f'expected EXEC response, got 0x{hdr[0]:02x}')
        rc, outlen = struct.unpack('>iI', hdr[1:9])
        out = self._recv_exactly(outlen) if outlen else b''
        return rc, out.decode('latin-1', 'replace')

    def reboot(self):
        self.sock.sendall(bytes([CMD_REBOOT]))
        # no response - the machine is resetting


def run_command(nh, argv):
    cmd, args = argv[0].upper(), argv[1:]
    if cmd == 'PING':
        nh.ping(); print('OK connected')
    elif cmd == 'HOME':
        nh.home(); print('OK')
    elif cmd == 'MOVE':
        nh.move(int(args[0]), int(args[1])); print('OK')
    elif cmd == 'MOVETO':
        x, y = int(args[0]), int(args[1])
        if nh.move_to(x, y):
            print('OK')
        else:
            px, py, _w, _h = nh.pointer()
            print(f'FAILED: wanted {x},{y} but pointer is {px},{py}')
            return 1
    elif cmd == 'BUTTON':
        nh.button(int(args[0]), bool(int(args[1]))); print('OK')
    elif cmd == 'CLICK':
        nh.click(int(args[0]), int(args[1]), int(args[2]) if len(args) > 2 else 0); print('OK')
    elif cmd == 'KEY':
        nh.key(int(args[0]), bool(int(args[1]))); print('OK')
    elif cmd == 'PRESSKEY':
        nh.press_key(int(args[0])); print('OK')
    elif cmd == 'TYPE':
        nh.type_text(' '.join(args)); print('OK')
    elif cmd == 'CLEARFIELD':
        nh.clear_field(int(args[0]) if args else 64); print('OK')
    elif cmd == 'RESETINPUT':
        nh.reset_input(); print('OK')
    elif cmd == 'SCREENSHOT':
        path, w, h, d = nh.screenshot(args[0] if args else 'nh_shot.png')
        print(f'OK {path} {w}x{h}x{d}')
    elif cmd == 'POINTER':
        x, y, w, h = nh.pointer(); print(f'OK pointer {x} {y} on {w}x{h}')
    elif cmd == 'UITREE':
        print(nh.ui_tree(), end='')
    elif cmd == 'MENUS':
        print(nh.menus(), end='')
    elif cmd == 'SCREENS':
        print(nh.screens(), end='')
    elif cmd == 'UICLICK':
        g = nh.ui_click(needle=' '.join(args))
        print(f'OK clicked id={g["id"]} {g["kind"]} "{g["label"]}" at {g["x"]},{g["y"]}'
              if g else 'FAILED: no gadget matched')
        return 0 if g else 1
    elif cmd == 'UICLICKID':
        g = nh.ui_click(gid=int(args[0]))
        print(f'OK clicked id={g["id"]} {g["kind"]} "{g["label"]}"'
              if g else 'FAILED: no gadget with that id')
        return 0 if g else 1
    elif cmd == 'MENUSEL':
        it = nh.menu_select(args[0], ' '.join(args[1:]))
        print(f'OK selected "{it}"' if it else 'FAILED: no match (or item has no shortcut)')
        return 0 if it else 1
    elif cmd == 'SHOTREGION':
        x, y, w, h = (int(a) for a in args[:4])
        out = args[4] if len(args) > 4 else 'nh_region.png'
        path, rw, rh, d = nh.screenshot(out, region=(x, y, w, h))
        print(f'OK {path} {rw}x{rh}x{d}')
    elif cmd == 'REGIONSUM':
        x, y, w, h = (int(a) for a in args[:4])
        print(f'OK sum {nh.region_sum(x, y, w, h):08x}')
    elif cmd == 'WAITCHANGE':
        x, y, w, h = (int(a) for a in args[:4])
        secs = float(args[4]) if len(args) > 4 else 10.0
        ok = nh.wait_change(x, y, w, h, timeout=secs)
        print('OK changed' if ok else f'TIMEOUT after {secs}s (no change)')
        return 0 if ok else 1
    elif cmd == 'GETFILE':
        n = nh.get_file(args[0], args[1]); print(f'OK got {n} bytes -> {args[1]}')
    elif cmd == 'RELOAD':
        if nh.reload():
            print('OK reloading (staged update applied if present)')
        else:
            print('REFUSED - could not apply the staged update; old build still running')
            return 1
    elif cmd == 'PUTFILE':
        n = nh.put_file(args[0], args[1]); print(f'OK put {n} bytes -> {args[1]}')
    elif cmd == 'EXEC':
        rc, out = nh.exec_cmd(' '.join(args))
        print(f'rc={rc}')
        if out:
            print(out, end='' if out.endswith('\n') else '\n')
    elif cmd == 'REBOOT':
        nh.reboot(); print('OK (Amiga rebooting)')
    else:
        print(f'unknown command {cmd}', file=sys.stderr)
        return 1
    return 0


def main():
    # Amiga output can contain control/ANSI bytes (LhA's progress bar, for one)
    # that a Windows cp1252 console refuses to encode, which would otherwise
    # crash us AFTER the Amiga command already succeeded.
    try:
        sys.stdout.reconfigure(errors='replace')
        sys.stderr.reconfigure(errors='replace')
    except AttributeError:
        pass
    argv = sys.argv[1:]
    host, port = DEFAULT_HOST, DEFAULT_PORT
    while argv and argv[0].startswith('--'):
        if argv[0] == '--host':
            host = argv[1]; argv = argv[2:]
        elif argv[0] == '--port':
            port = int(argv[1]); argv = argv[2:]
        elif argv[0] == '--batch':
            argv = argv[1:]
            nh = NetHarness(host, port)
            n = 0
            for line in sys.stdin:
                line = line.strip()
                if not line:
                    continue
                n += 1
                print(f'{n}: ', end='')
                run_command(nh, line.split(' '))
            return 0
        else:
            print(f'unknown option {argv[0]}', file=sys.stderr)
            return 1
    if not argv:
        print(__doc__)
        return 1
    nh = NetHarness(host, port)
    return run_command(nh, argv)


if __name__ == '__main__':
    sys.exit(main())

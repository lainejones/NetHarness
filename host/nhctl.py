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

Every input command waits for the Amiga's RESP_ACK, so "OK" here means
DELIVERED AND INJECTED (the fix for the A1200 harness's biggest blind spot).
"""

import socket
import struct
import sys
import time

DEFAULT_HOST = '192.168.50.32'      # the A4000
DEFAULT_PORT = 7800

CMD_MOUSE_MOVE, CMD_MOUSE_BUTTON, CMD_KEY, CMD_HOME_MOUSE = 1, 2, 3, 4
CMD_SCREENSHOT, CMD_REBOOT, CMD_RESET_INPUT, CMD_EXEC, CMD_PING = 5, 6, 7, 8, 9
RESP_SCREENSHOT_HDR, RESP_ACK, RESP_EXEC = 0x81, 0x82, 0x83

SHIFT_CODE = 0x60
RAWKEY_RIGHT, RAWKEY_BACKSPACE, RAWKEY_RETURN = 0x4E, 0x41, 0x44

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

    def move_to(self, x, y):
        self.home()
        self.move(x, y)

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

    # ---- screenshot ----------------------------------------------------------

    def screenshot(self, path='nh_shot.png', settle=0.15):
        if settle:
            time.sleep(settle)   # let Intuition's async redraw finish
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
        nh.move_to(int(args[0]), int(args[1])); print('OK')
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

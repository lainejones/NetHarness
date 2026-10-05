#!/usr/bin/env python3
"""nhgui.py - a remote-control window for NetHarness.

Shows the Amiga's front screen and passes on what you do to it:

  left button      click, double-click, drag (press - move - release)
  right button     hold it and move, as on the Amiga: the menus drop down,
                   the picture follows, release over an item to pick it
  keyboard         typed into the Amiga while the picture has the focus
                   (click it first): letters, Return, Esc, Tab, Backspace,
                   Del, cursor keys, F1-F10, Help = F12

Along the top: which Amiga - pick it by name from the list, or type a name,
an address or host:port and press Return ("Name it..." keeps an address
under a name); Refresh, automatic refresh every few seconds, Workbench (bring the Workbench
screen to the front: left Amiga + N), Next screen (left Amiga + M), Status
(is it up?), Reboot.

It is a way to look in on an Amiga and work it now and then - a picture on
request, not a live view; not a replacement for VNC or a remote desktop.
Along the bottom: an AmigaDOS command to run (with a priority for it), and
its output.

  python nhgui.py [host[:port]]

Machines file: ~/.nhgui.json, e.g.
  {"machines": {"A4000": "192.168.1.32:7800", "A1200": "192.168.1.33:7800"}}

Each action is one short connection to the harness, so nhctl.py and scripts
can use the same Amiga in between.  Needs Pillow (pip install pillow).
"""

import json
import os
import queue
import sys
import tempfile
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from PIL import Image, ImageTk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nhctl  # noqa: E402

CONFIG = os.path.join(os.path.expanduser('~'), '.nhgui.json')

# tk keysym -> raw Amiga key code
SPECIAL = {
    'Return': 0x44, 'KP_Enter': 0x43, 'BackSpace': 0x41, 'Tab': 0x42, 'Escape': 0x45,
    'Delete': 0x46, 'Up': 0x4C, 'Down': 0x4D, 'Right': 0x4E, 'Left': 0x4F,
    'F1': 0x50, 'F2': 0x51, 'F3': 0x52, 'F4': 0x53, 'F5': 0x54, 'F6': 0x55,
    'F7': 0x56, 'F8': 0x57, 'F9': 0x58, 'F10': 0x59, 'F12': 0x5F,
}


def load_machines():
    try:
        with open(CONFIG, encoding='utf-8') as f:
            return dict(json.load(f).get('machines', {}))
    except (OSError, ValueError):
        return {}


def save_machines(machines):
    data = {}
    try:
        with open(CONFIG, encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        pass
    data['machines'] = machines
    with open(CONFIG, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)


class Remote:
    """What the window asks of one Amiga.  Every call opens its own short
    connection; `held` remembers a button that is down between calls."""

    def __init__(self, host, port):
        self.host, self.port = host, port
        self.last = None             # (x, y, when) of the last button press

    def _nh(self, timeout=20):
        return nhctl.NetHarness(self.host, self.port, timeout=timeout)

    @staticmethod
    def _done(nh):
        try:
            nh.sock.close()
        except OSError:
            pass

    def shot(self, path):
        nh = self._nh(60)
        try:
            _p, w, h, d = nh.screenshot(path, settle=0)
            return w, h, d
        finally:
            self._done(nh)

    @staticmethod
    def _glide(nh, x, y, rounds=80):
        """To (x, y) from wherever the pointer is - no trip through the top
        left corner, so it can be used with a button held down.  Closed loop,
        like NetHarness.move_to: small steps pass through unaccelerated."""
        px, py = nh.pointer()[:2]
        gain = 4.0
        for _ in range(rounds):
            dx, dy = x - px, y - py
            if dx == 0 and dy == 0:
                return
            if abs(dx) <= nh.ACCEL_FREE and abs(dy) <= nh.ACCEL_FREE:
                rx, ry = dx, dy
            else:
                rx = int(dx / gain) or (1 if dx > 0 else -1 if dx else 0)
                ry = int(dy / gain) or (1 if dy > 0 else -1 if dy else 0)
            nh.move(rx, ry)
            nx, ny, _w, _h = nh.pointer()
            moved, asked = abs(nx - px) + abs(ny - py), abs(rx) + abs(ry)
            if asked > nh.ACCEL_FREE and moved:
                g = moved / asked
                if 0.2 <= g <= 16:
                    gain = (gain + g) / 2.0
            if (nx, ny) == (px, py):
                return                         # at an edge
            px, py = nx, ny

    def press(self, x, y, b):
        nh = self._nh()
        try:
            # The second click of a double-click: the pointer is still there.
            # Sending it home and back first (move_to) took longer than the
            # Amiga's double-click time on a real machine.
            again = self.last and abs(self.last[0] - x) <= 2 and abs(self.last[1] - y) <= 2                 and time.time() - self.last[2] < 3
            if not again:
                nh.move_to(x, y)
            nh.button(b, True)
            self.last = (x, y, time.time())
        finally:
            self._done(nh)

    def drag(self, x, y):
        nh = self._nh()
        try:
            self._glide(nh, x, y)
        finally:
            self._done(nh)

    def release(self, x, y, b, moved):
        nh = self._nh()
        try:
            if moved:
                self._glide(nh, x, y)
                self.last = None
            nh.button(b, False)
        finally:
            self._done(nh)

    def keys(self, items):
        """items: ('c', char) or ('k', rawcode)"""
        nh = self._nh()
        try:
            for kind, v in items:
                if kind == 'c':
                    nh.type_text(v)
                else:
                    nh.press_key(v)
        finally:
            self._done(nh)

    def run(self, cmd, secs, pri):
        nh = self._nh(secs + 40)
        try:
            return nh.exec_cmd(cmd, secs, pri)
        finally:
            self._done(nh)

    def reset_input(self):
        nh = self._nh()
        try:
            nh.reset_input()
        finally:
            self._done(nh)

    def amiga_key(self, code):
        """left Amiga + a key: N = Workbench to front, M = next screen"""
        nh = self._nh()
        try:
            nh.key(0x66, True)
            nh.press_key(code)
            nh.key(0x66, False)
        finally:
            self._done(nh)

    def reboot(self):
        nh = self._nh()
        try:
            nh.reboot()
        finally:
            self._done(nh)


class App:
    def __init__(self, root, target):
        self.root = root
        self.machines = load_machines()
        self.remote = None
        self.jobs = queue.Queue()
        self.results = queue.Queue()
        self.image = None            # the Amiga's screen, full size
        self.photo = None
        self.scale, self.ox, self.oy = 1.0, 0, 0
        self.held = None             # [button, start x, start y, moved, last x y, press canvas x y]
        self.quiet_until = 0.0       # no picture is fetched before this time
        self.last_click = None       # (amiga x, y, when, canvas x, y) of the last left press
        self.second_timer = None
        self.pending_keys = []
        self.key_timer = None
        self.refresh_timer = None
        self.drag_timer = None
        self.busy = 0
        self.shot_path = os.path.join(tempfile.gettempdir(), 'nhgui_shot.png')

        self.title = 'NetHarness'
        root.title(self.title)
        root.geometry("1060x800")
        top = ttk.Frame(root, padding=4)
        top.pack(fill='x')
        ttk.Label(top, text='Amiga').pack(side='left')
        # pick one by name from the list, or type a name, an address or
        # host:port and press Return
        self.target = tk.StringVar(value=self.entry_for(target) if target else
                                   (self.entries()[0] if self.machines else ''))
        self.combo = ttk.Combobox(top, textvariable=self.target, width=34, values=self.entries())
        self.combo.pack(side='left', padx=4)
        self.combo.bind('<<ComboboxSelected>>', lambda e: self.connect())
        self.combo.bind('<Return>', lambda e: self.connect())
        ttk.Button(top, text='Name it...', width=9, command=self.name_it).pack(side='left')
        ttk.Button(top, text='Refresh', command=self.refresh).pack(side='left')
        self.auto = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text='every', variable=self.auto,
                        command=self.auto_tick).pack(side='left', padx=(8, 0))
        self.every = tk.StringVar(value='5')
        ttk.Spinbox(top, from_=1, to=60, width=3, textvariable=self.every).pack(side='left')
        ttk.Label(top, text='s').pack(side='left')
        ttk.Button(top, text='Workbench', command=lambda: self.flip(0x36)).pack(side='left', padx=(8, 0))
        ttk.Button(top, text='Next screen', command=lambda: self.flip(0x37)).pack(side='left', padx=4)
        ttk.Button(top, text='Status', command=self.status).pack(side='left', padx=(4, 0))
        ttk.Button(top, text='Save picture', command=self.save).pack(side='left', padx=4)
        ttk.Button(top, text='Release keys', command=self.reset_input).pack(side='left')
        ttk.Button(top, text='Reboot', command=self.reboot).pack(side='right')

        self.canvas = tk.Canvas(root, bg='#202020', highlightthickness=2,
                                highlightbackground='#404040', highlightcolor='#3a7bd5',
                                takefocus=1, cursor='crosshair')
        self.canvas.pack(fill='both', expand=True)
        self.canvas.bind('<Configure>', lambda e: self.draw())
        for b, n in ((1, 0), (3, 1), (2, 2)):
            self.canvas.bind(f'<ButtonPress-{b}>', lambda e, n=n: self.on_press(e, n))
            self.canvas.bind(f'<B{b}-Motion>', self.on_motion)
            self.canvas.bind(f'<ButtonRelease-{b}>', lambda e, n=n: self.on_release(e, n))
        self.canvas.bind('<Key>', self.on_key)

        bottom = ttk.Frame(root, padding=4)
        bottom.pack(fill='x')
        ttk.Label(bottom, text='Command').pack(side='left')
        self.cmd = tk.StringVar()
        ent = ttk.Entry(bottom, textvariable=self.cmd)
        ent.pack(side='left', fill='x', expand=True, padx=4)
        ent.bind('<Return>', lambda e: self.run_cmd())
        ttk.Label(bottom, text='priority').pack(side='left')
        self.pri = tk.StringVar(value='0')
        ttk.Spinbox(bottom, from_=-5, to=19, width=3, textvariable=self.pri).pack(side='left')
        ttk.Button(bottom, text='Run', command=self.run_cmd).pack(side='left', padx=4)
        self.out = tk.Text(root, height=7, wrap='none', font=('Consolas', 9))
        self.out.pack(fill='x')
        self.statusbar = ttk.Label(root, text='', anchor='w', padding=(6, 2))
        self.statusbar.pack(fill='x')

        threading.Thread(target=self.worker, daemon=True).start()
        self.poll()
        root.bind('<FocusIn>', self.on_focus)
        self.root.after(3000, self.auto_tick)
        if self.target.get():
            self.connect()
        else:
            self.say(f'Type host:port above and press Return (names can go in {CONFIG})')

    # ---- plumbing -----------------------------------------------------------

    def say(self, text):
        self.statusbar.config(text=text)

    def entries(self):
        return [f'{n}   ({a})' for n, a in self.machines.items()]

    def entry_for(self, text):
        return next((e for e in self.entries() if e.split('   (')[0].lower() == text.lower()), text)

    def address(self):
        """host, port from what the box says: "name   (host:port)", a name
        (any unambiguous start of one), a host or host:port"""
        t = self.target.get().strip()
        if t.endswith(')') and '(' in t:
            t = t[t.rindex('(') + 1:-1]
        else:
            hits = [a for n, a in self.machines.items() if n.lower().startswith(t.lower())]
            exact = [a for n, a in self.machines.items() if n.lower() == t.lower()]
            if exact or len(hits) == 1:
                t = (exact or hits)[0]
        host, _, port = t.partition(':')
        try:
            return host.strip(), int(port or nhctl.DEFAULT_PORT)
        except ValueError:
            return '', 0

    def connect(self):
        host, port = self.address()
        if not host:
            self.say('Pick an Amiga from the list, or type its name or address (host:port)')
            return
        self.remote = Remote(host, port)
        self.target.set(next((e for e in self.entries() if e.endswith(f'({host}:{port})')),
                             f'{host}:{port}'))
        self.title = f'NetHarness - {self.target.get().split("   (")[0]}'
        self.root.title(self.title)
        self.image = None
        self.draw()
        self.refresh()

    def name_it(self):
        """give the Amiga in the box a name and keep it in the machines file"""
        host, port = self.address()
        if not host:
            self.say('Type the address (host:port) in the box first')
            return
        old = next((n for n, a in self.machines.items() if a == f'{host}:{port}'), '')
        name = simpledialog.askstring('Name it', f'Name for {host}:{port}  (empty = forget it)',
                                      initialvalue=old, parent=self.root)
        if name is None:
            return
        if old:
            del self.machines[old]
        if name.strip():
            self.machines[name.strip()] = f'{host}:{port}'
        save_machines(self.machines)
        self.combo.config(values=self.entries())
        self.connect()

    def on_focus(self, e):
        if e.widget is self.root and self.remote and self.busy == 0 and self.held is None:
            self.refresh(quiet=True)

    def submit(self, label, fn, then=None, quiet=False):
        """Run fn() on the worker thread, then then(result) here."""
        if not self.remote:
            self.say('No Amiga chosen')
            return
        self.busy += 1
        if not quiet:
            self.say(label + '...')
        if label != 'Picture':
            # a click must not wait behind pictures that have not started
            # yet (a picture of a big screen takes seconds, and the second
            # click of a double-click has to follow the first at once)
            with self.jobs.mutex:
                waiting = [j for j in self.jobs.queue if j[0] == 'Picture']
                for j in waiting:
                    self.jobs.queue.remove(j)
                self.busy -= len(waiting)
        self.jobs.put((label, fn, then, quiet))

    def worker(self):
        """The network side.  It never touches Tk: results go back through a
        queue that the window's own thread empties (poll)."""
        while True:
            label, fn, then, quiet = self.jobs.get()
            try:
                res, err = fn(), None
            except Exception as e:  # noqa: BLE001 - every network failure ends up here
                res, err = None, e
            self.results.put((label, then, res, err, quiet))

    def poll(self):
        try:
            while True:
                self._finished(*self.results.get_nowait())
        except queue.Empty:
            pass
        self.root.after(30, self.poll)

    def _finished(self, label, then, res, err, quiet):
        self.busy -= 1
        if err is not None:
            what = 'no answer in time' if isinstance(err, (TimeoutError, OSError)) and \
                'timed out' in str(err).lower() else str(err)
            self.say(f'{label}: {what}  (Status tells you more)')
            self.root.title(self.title)
            return
        if not quiet:
            self.say(label + ': done')
        if then:
            then(res)

    # ---- the picture ---------------------------------------------------------

    def refresh(self, quiet=False):
        if self.jobs.qsize() > 2:
            return
        if time.time() < self.quiet_until:        # a click just went: its partner may follow
            self.later_refresh(int((self.quiet_until - time.time()) * 1000) + 50)
            return
        r, path = self.remote, self.shot_path
        self.root.title(self.title + '   (fetching the picture...)')
        self.submit('Picture', lambda: r.shot(path), self.got_shot, quiet)

    def got_shot(self, res):
        w, h, d = res
        try:
            with Image.open(self.shot_path) as im:
                self.image = im.convert('RGB')
        except OSError as e:
            self.say(f'Picture: {e}')
            return
        self.root.title(self.title)
        colours = 'true colour' if d == 24 else f'{d} colours' if d > 8 else f'{1 << d} colours'
        self.say(f'{self.remote.host}:{self.remote.port}   {w} x {h}, {colours}   '
                 f'{time.strftime("%H:%M:%S")}')
        self.draw()

    def draw(self):
        c = self.canvas
        c.delete('all')
        cw, ch = max(c.winfo_width(), 50), max(c.winfo_height(), 50)
        if self.image is None:
            c.create_text(cw // 2, ch // 2, fill='#909090',
                          text='No picture yet' if self.remote else 'No Amiga chosen')
            return
        iw, ih = self.image.size
        s = min(cw / iw, ch / ih)
        if s >= 2:
            s = float(int(s))                       # whole pixels when enlarging
        self.scale = s
        dw, dh = max(1, int(iw * s)), max(1, int(ih * s))
        self.ox, self.oy = (cw - dw) // 2, (ch - dh) // 2
        im = self.image if (dw, dh) == (iw, ih) else self.image.resize(
            (dw, dh), Image.NEAREST if s >= 1 else Image.LANCZOS)
        self.photo = ImageTk.PhotoImage(im)
        c.create_image(self.ox, self.oy, anchor='nw', image=self.photo)

    def to_amiga(self, e):
        if self.image is None:
            return None
        iw, ih = self.image.size
        x, y = int((e.x - self.ox) / self.scale), int((e.y - self.oy) / self.scale)
        return max(0, min(iw - 1, x)), max(0, min(ih - 1, y))

    def later_refresh(self, ms=500):
        if self.refresh_timer:
            self.root.after_cancel(self.refresh_timer)
        self.refresh_timer = self.root.after(ms, self._later)

    def _later(self):
        self.refresh_timer = None
        self.refresh(quiet=True)

    def _second(self):
        self.second_timer = None
        if self.held is None:
            self.refresh(quiet=True)

    def auto_tick(self):
        if not self.auto.get():
            return
        if self.remote and self.busy == 0 and self.held is None:
            self.refresh(quiet=True)
        try:
            secs = max(1, int(self.every.get()))
        except ValueError:
            secs = 3
        self.root.after(secs * 1000, self.auto_tick)

    # ---- mouse ---------------------------------------------------------------

    def on_press(self, e, b):
        self.canvas.focus_set()
        p = self.to_amiga(e)
        if p is None or self.held is not None:
            return
        last = self.last_click
        if b == 0 and last and time.time() - last[2] < 1.0 and \
                abs(e.x - last[3]) + abs(e.y - last[4]) <= 10:
            p = (last[0], last[1])                  # same spot as the first click
        self.last_click = (p[0], p[1], time.time(), e.x, e.y) if b == 0 else None
        if self.refresh_timer:                      # a second click of a double-click:
            self.root.after_cancel(self.refresh_timer)   # no picture in between
            self.refresh_timer = None
        self.held = [b, p[0], p[1], False, p, (e.x, e.y)]
        r = self.remote
        self.mark(e.x, e.y)
        self.say(f'{("Left", "Right", "Middle")[b]} button at {p[0]}, {p[1]}')
        self.submit('Click', lambda: r.press(p[0], p[1], b), quiet=True)
        if b == 1:                                  # menus: show them once they are down
            self.later_refresh(350)

    def mark(self, x, y):
        """a ring where the click went (the Amiga's pointer is not in the picture)"""
        ring = self.canvas.create_oval(x - 9, y - 9, x + 9, y + 9, outline='#ff3030', width=2)
        self.root.after(700, lambda: self.canvas.delete(ring))

    def on_motion(self, e):
        p = self.to_amiga(e)
        if p is None or self.held is None:
            return
        # the hand moves a little during any click: it is a drag only once the
        # mouse has gone a clear distance on OUR screen (with the right
        # button, any movement: that is how a menu is walked)
        cx, cy = self.held[5]
        if abs(e.x - cx) + abs(e.y - cy) > (2 if self.held[0] == 1 else 8):
            self.held[3] = True
        self.held[4] = p
        if self.drag_timer is None:                 # follow, but not every pixel
            self.drag_timer = self.root.after(120, self._drag)

    def _drag(self):
        self.drag_timer = None
        if self.held is None or not self.held[3]:
            return
        p, r = self.held[4], self.remote
        if self.jobs.qsize() < 2:
            self.submit('Move', lambda: r.drag(p[0], p[1]), quiet=True)
            self.later_refresh(300)

    def on_release(self, e, b):
        if self.held is None or self.held[0] != b:
            return
        moved = self.held[3]
        # not a drag: let go exactly where the button went down
        p = (self.to_amiga(e) or self.held[4]) if moved else (self.held[1], self.held[2])
        self.held = None
        r = self.remote
        self.submit('Click', lambda: r.release(p[0], p[1], b, moved), quiet=True)
        self.quiet_until = time.time() + 0.9        # room for a double-click first
        self.later_refresh(950)
        # ...and once more when a program that was started has had time to open
        if self.second_timer:
            self.root.after_cancel(self.second_timer)
        self.second_timer = self.root.after(4000, self._second)
        self.say('%s sent to the Amiga - the picture follows in a moment (a program takes a few '
                 'seconds to open)' % ('Drag' if moved else 'Click'))

    # ---- keyboard ------------------------------------------------------------

    def on_key(self, e):
        if e.keysym in SPECIAL:
            self.pending_keys.append(('k', SPECIAL[e.keysym]))
        elif e.char and e.char in nhctl.KEYMAP:
            self.pending_keys.append(('c', e.char))
        else:
            return None
        if self.key_timer is None:                  # send what was typed in one go
            self.key_timer = self.root.after(60, self._send_keys)
        return 'break'

    def _send_keys(self):
        self.key_timer = None
        items, self.pending_keys = self.pending_keys, []
        r = self.remote
        self.submit('Keys', lambda: r.keys(items), quiet=True)
        self.later_refresh(500)

    # ---- buttons -------------------------------------------------------------

    def status(self):
        r = self.remote
        if not r:
            return
        self.submit('Status', lambda: nhctl.probe(r.host, r.port),
                    lambda res: self.say(f'{r.host}:{r.port}  {res[0].upper()}: {res[1]}'), quiet=True)

    def flip(self, code):
        r = self.remote
        if r:
            self.submit('Screen', lambda: r.amiga_key(code), lambda _: self.later_refresh(400), quiet=True)

    def save(self):
        if self.image is None:
            return
        path = filedialog.asksaveasfilename(defaultextension='.png',
                                            filetypes=[('PNG picture', '*.png')])
        if path:
            self.image.save(path)
            self.say(f'Saved {path}')

    def reset_input(self):
        r = self.remote
        self.held = None
        self.submit('Release keys', r.reset_input)

    def unstick(self):
        """a press whose release never came (the mouse left the window): let go"""
        if self.held is not None and time.time() - self.held_at > 15:
            self.reset_input()

    def reboot(self):
        r = self.remote
        if r and messagebox.askokcancel('Reboot', f'Reboot the Amiga at {r.host}:{r.port}?'):
            self.submit('Reboot', r.reboot, lambda _: self.say(
                'Rebooting - press Status in a while'))

    def run_cmd(self):
        cmd = self.cmd.get().strip()
        if not cmd:
            return
        try:
            pri = int(self.pri.get())
        except ValueError:
            pri = 0
        r = self.remote
        self.out.insert('end', f'> {cmd}\n')
        self.out.see('end')

        def show(res):
            rc, text = res
            self.out.insert('end', text if text.endswith('\n') or not text else text + '\n')
            if rc:
                self.out.insert('end', 'time limit reached\n' if rc == nhctl.EXEC_TIMED_OUT
                                else f'return code {rc}\n')
            self.out.see('end')
            self.later_refresh(300)

        self.submit('Command', lambda: r.run(cmd, 120, pri), show)


def main():
    root = tk.Tk()
    App(root, sys.argv[1] if len(sys.argv) > 1 else None)
    root.mainloop()


if __name__ == '__main__':
    main()

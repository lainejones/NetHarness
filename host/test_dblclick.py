#!/usr/bin/env python3
"""test_dblclick.py host[:port] x y [trials] - double-click the way a hand does.

Unlike test_nhgui.py this does not wait politely between events: the window
runs as in real use (automatic refresh on), and each trial is press, release,
press, release with human gaps and a few pixels of hand movement, at a random
moment relative to the refresh. x y is an icon whose drawer opens as a window
(the RAM Disk icon - the test looks for a window with "RAM Disk" in its
title); each window that opens is closed again by its close gadget.  Prints how
many of the trials opened it.
"""
import random
import sys
import time
import tkinter as tk

import nhgui


def main():
    target, ix, iy = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
    trials = int(sys.argv[4]) if len(sys.argv) > 4 else 6
    root = tk.Tk()
    app = nhgui.App(root, target)
    app.every.set('3')
    r = app.remote

    def pump(secs):
        end = time.time() + secs
        while time.time() < end:
            root.update()
            time.sleep(0.01)

    def wins():
        nh = r._nh()
        try:
            return [l for l in nh.ui_tree().splitlines() if l.startswith('W ')]
        finally:
            r._done(nh)

    def ev(kind, ax, ay, dx=0, dy=0):
        x = int(app.ox + (ax + 0.5) * app.scale) + dx
        y = int(app.oy + (ay + 0.5) * app.scale) + dy
        app.canvas.event_generate(kind, x=x, y=y)

    def is_open():
        return any('RAM Disk' in l for l in wins())

    def close_it():
        nh = r._nh()
        try:
            g = [l for l in nh.ui_tree().splitlines()]
        finally:
            r._done(nh)
        for l in g:                                  # its close gadget is top left of the window
            if l.startswith('W ') and 'RAM Disk' in l:
                f = l.split()
                r.last = None
                r.press(int(f[2]) + 8, int(f[3]) + 5, 0)
                r.release(int(f[2]) + 8, int(f[3]) + 5, 0, False)
                r.last = None
                pump(2.5)
                return

    pump(4)
    if is_open():
        close_it()
    ok = 0
    for t in range(trials):
        pump(random.uniform(0.2, 3.2))               # anywhere in the refresh cycle
        j = lambda: random.randint(-2, 2)            # the hand is never still
        ev('<ButtonPress-1>', ix, iy)
        pump(random.uniform(0.05, 0.11))
        ev('<B1-Motion>', ix, iy, j(), j())
        ev('<ButtonRelease-1>', ix, iy, j(), j())
        pump(random.uniform(0.12, 0.28))
        ev('<ButtonPress-1>', ix, iy, j(), j())
        pump(random.uniform(0.05, 0.11))
        ev('<ButtonRelease-1>', ix, iy, j(), j())
        pump(5)
        while app.busy:
            pump(0.2)
        opened = is_open()
        ok += opened
        print('trial %d: %s' % (t + 1, 'opened' if opened else 'NOT opened'), flush=True)
        if opened:
            close_it()
    print('%d of %d double-clicks opened the drawer' % (ok, trials))
    root.destroy()
    return 0 if ok == trials else 1


if __name__ == '__main__':
    sys.exit(main())

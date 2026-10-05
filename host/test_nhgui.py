#!/usr/bin/env python3
"""test_nhgui.py host[:port] - drive nhgui's own mouse and keyboard handlers
against a live Amiga and check what happened there.

Needs a Workbench screen in front with a "RAM Disk" icon on it and nothing
else open.  It opens the RAM Disk drawer with a double-click, drags its
window, closes it from the Window menu with the right button, and types a
command into a Shell.  Prints PASS/FAIL per step; exit code 0 = all passed.
"""
import sys
import time
import tkinter as tk

import nhctl
import nhgui


def main():
    target = sys.argv[1]
    root = tk.Tk()
    app = nhgui.App(root, target)
    app.auto.set(False)
    r = app.remote
    fails = []

    def pump(secs):
        end = time.time() + secs
        while time.time() < end:
            root.update()
            time.sleep(0.02)

    def idle(secs=0.3):
        pump(secs)
        while app.busy or not app.jobs.empty():
            pump(0.1)
        pump(0.2)

    def tree():
        nh = r._nh()
        try:
            return nh.ui_tree()
        finally:
            r._done(nh)

    def wins():
        return [l for l in tree().splitlines() if l.startswith('W ')]

    def gadget_line(text):
        return next((l for l in tree().splitlines() if text in l), None)

    def canvas_xy(ax, ay):
        return int(app.ox + (ax + 0.5) * app.scale), int(app.oy + (ay + 0.5) * app.scale)

    def press(ax, ay, b=1):
        x, y = canvas_xy(ax, ay)
        app.canvas.event_generate(f'<ButtonPress-{b}>', x=x, y=y)

    def motion(ax, ay, b=1):
        x, y = canvas_xy(ax, ay)
        app.canvas.event_generate(f'<B{b}-Motion>', x=x, y=y)

    def release(ax, ay, b=1):
        x, y = canvas_xy(ax, ay)
        app.canvas.event_generate(f'<ButtonRelease-{b}>', x=x, y=y)

    def check(name, ok, detail=''):
        print(('PASS  ' if ok else 'FAIL  ') + name + (f'   [{detail}]' if detail else ''), flush=True)
        if not ok:
            fails.append(name)

    root.update()
    idle(1.0)
    check('picture arrives', app.image is not None, str(app.image.size if app.image else None))
    if app.image is None:
        return 1

    # where is the RAM Disk icon?  ask Workbench for nothing: find it in the picture's
    # neighbourhood via the harness (icons are not gadgets), so take it from argv or default
    ix, iy = (int(sys.argv[2]), int(sys.argv[3])) if len(sys.argv) > 3 else (40, 205)

    # 1. double-click
    for _ in range(2):
        press(ix, iy); idle(0.05); release(ix, iy); idle(0.05)
    idle(4)
    w = next((l for l in wins() if 'RAM Disk' in l), None)
    check('double-click opens the RAM Disk drawer', w is not None, w or str(wins()))
    if not w:
        return 1
    f = w.split()
    wx, wy, ww = int(f[2]), int(f[3]), int(f[4])

    # 2. drag the window by its title bar, to a place it is not at already
    #    (Workbench remembers where the drawer was, and a window stops at the
    #    screen's edges)
    tx, ty = (60, 60) if (wx, wy) != (60, 60) else (200, 150)
    dx, dy = tx - wx, ty - wy
    sx, sy = wx + ww // 2, wy + 4
    press(sx, sy); idle(0.2)
    for k in range(1, 6):
        motion(sx + dx * k // 5, sy + dy * k // 5); pump(0.25)
    idle(0.5)
    release(sx + dx, sy + dy); idle(2)
    w2 = next((l for l in wins() if 'RAM Disk' in l), '')
    f2 = w2.split()
    at = (int(f2[2]), int(f2[3])) if len(f2) > 3 else None
    # a shrunken picture maps several Amiga pixels onto one of ours: one pixel either way
    check(f'drag moves the window to ({tx}, {ty})',
          at is not None and abs(at[0] - tx) <= 1 and abs(at[1] - ty) <= 1, str(at))

    # 3. right button: Window menu > Close
    nh = r._nh()
    menus = nh.menus()
    r._done(nh)
    titles = [l.split('"')[1] for l in menus.splitlines() if l.startswith('M ')]
    items = [l.split('"')[1] for l in menus.splitlines() if l.startswith('I 1 ')]
    # menu titles sit left to right in the screen font: measure with the same 8-pixel font
    x_window = 8 + sum((len(t) + 2) * 8 for t in titles[:titles.index('Window')]) + 16
    y_close = 11 + items.index('Close') * 9 + 5
    press(x_window, 5, b=3); idle(0.6)
    motion(x_window, 5 + 12, b=3); pump(0.3)
    motion(x_window, y_close, b=3); idle(0.6)
    release(x_window, y_close, b=3); idle(2)
    check('right-button menu: Window > Close closes it',
          not any('RAM Disk' in l for l in wins()), str(wins()))

    # 4. typing
    r.run('NewShell "CON:20/300/600/150/Typing test"', 20, 0)
    idle(2)
    press(200, 380); idle(0.05); release(200, 380); idle(1)
    app.canvas.focus_force()
    for ch in 'echo Typed_42 >RAM:nhgui.txt':
        app.canvas.event_generate('<Key>', keysym=({' ': 'space', '>': 'greater', ':': 'colon',
                                                    '_': 'underscore', '.': 'period'}.get(ch, ch)))
        pump(0.03)
    app.canvas.event_generate('<Key>', keysym='Return')
    idle(2)
    rc, out = r.run('Type RAM:nhgui.txt', 20, 0)
    check('typing reaches a Shell', out.strip() == 'Typed_42', repr(out))
    for ch in 'endcli':
        app.canvas.event_generate('<Key>', keysym=ch)
    app.canvas.event_generate('<Key>', keysym='Return')
    idle(1.5)
    r.run('Delete RAM:nhgui.txt QUIET', 20, 0)

    # 5. the buttons
    app.status(); idle(1)
    check('Status button', ' UP: ' in app.statusbar.cget('text'), app.statusbar.cget('text')[:70])
    state, detail = nhctl.probe(r.host, r.port)
    check('STATUS says up', state == 'up', detail[:60])

    print('ALL PASSED' if not fails else f'{len(fails)} FAILED: {fails}')
    root.destroy()
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())

#!/usr/bin/env python3
"""nhmcp.py - MCP server exposing a NetHarness Amiga as native tools.

Speaks MCP over stdio (JSON-RPC 2.0), wrapping host/nhctl.py so an assistant
can drive the Amiga directly instead of shelling out to the CLI.  Same idea as
thomas-luebker/amimcp, but on top of NetHarness's existing protocol (input ACKs,
EXEC, the v1.3 semantic layer).

Register it (Claude Code):
    claude mcp add amiga -- python3 C:/projects/NetHarness/host/nhmcp.py --host 192.168.1.32

Options:  --host <ip>   (default: nhctl.py's DEFAULT_HOST)
          --port <n>    (default 7800)

Only the standard library is used, except Pillow for screenshots (same as
nhctl).  The connection is opened lazily and reopened if the Amiga drops it,
so the server survives an Amiga reboot.
"""

import base64
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nhctl import NetHarness, DEFAULT_HOST, DEFAULT_PORT   # noqa: E402

HOST, PORT = DEFAULT_HOST, DEFAULT_PORT
_nh = None


def nh():
    """Lazy, self-healing connection: the Amiga may reboot under us."""
    global _nh
    if _nh is None:
        _nh = NetHarness(HOST, PORT)
    return _nh


def with_retry(fn):
    try:
        return fn(nh())
    except (ConnectionError, OSError):
        global _nh
        _nh = None                      # drop it and try once more
        return fn(nh())


def _text(s):
    return {"content": [{"type": "text", "text": s}]}


def _image_png(path, caption):
    with open(path, 'rb') as f:
        b64 = base64.b64encode(f.read()).decode('ascii')
    return {"content": [{"type": "text", "text": caption},
                        {"type": "image", "data": b64, "mimeType": "image/png"}]}


def _shot(region=None):
    tmp = os.path.join(tempfile.gettempdir(), 'nhmcp_shot.png')
    path, w, h, d = with_retry(lambda n: n.screenshot(tmp, region=region))
    return _image_png(path, f'{w}x{h}x{d}')


# ---- tools ---------------------------------------------------------------

TOOLS = [
    ("amiga_shell", "Run an AmigaDOS command; returns exit code and captured output.",
     {"command": {"type": "string", "description": "AmigaDOS command line"}}, ["command"]),
    ("amiga_screenshot", "Capture the Amiga screen as a PNG. Optionally just a region "
     "(far cheaper than a full frame).",
     {"x": {"type": "integer"}, "y": {"type": "integer"},
      "w": {"type": "integer"}, "h": {"type": "integer"}}, []),
    ("amiga_ui_tree", "Read the front screen's windows and gadgets (id, kind, "
     "screen-absolute bounds, label, string contents). Use this instead of guessing "
     "pixel coordinates.", {}, []),
    ("amiga_ui_click", "Click a gadget BY IDENTITY - matching its label/contents "
     "(substring, case-insensitive) or its numeric GadgetID.",
     {"text": {"type": "string", "description": "label/contents substring"},
      "id": {"type": "integer", "description": "GadgetID"}}, []),
    ("amiga_menus", "Enumerate the front window's menu strip with keyboard shortcuts.", {}, []),
    ("amiga_menu_select", "Pick a menu item by NAME (uses its keyboard shortcut).",
     {"menu": {"type": "string"}, "item": {"type": "string"}}, ["menu", "item"]),
    ("amiga_screens", "List open screens, front to back.", {}, []),
    ("amiga_pointer", "Where the mouse pointer actually is (it is an invisible "
     "hardware sprite, so screenshots never show it).", {}, []),
    ("amiga_click", "Click at exact screen coordinates (closed-loop: verified landing).",
     {"x": {"type": "integer"}, "y": {"type": "integer"},
      "button": {"type": "integer", "description": "0=left 1=right 2=middle"}}, ["x", "y"]),
    ("amiga_type", "Type text through the Amiga keymap.",
     {"text": {"type": "string"}}, ["text"]),
    ("amiga_key", "Press a raw Amiga keycode (68=Return, 69=Esc, 65=Backspace).",
     {"code": {"type": "integer"}}, ["code"]),
    ("amiga_region_changed", "Checksum a screen region - cheap way to tell whether "
     "something has redrawn.",
     {"x": {"type": "integer"}, "y": {"type": "integer"},
      "w": {"type": "integer"}, "h": {"type": "integer"}}, ["x", "y", "w", "h"]),
    ("amiga_wait_change", "Block until a screen region changes (observed, not a "
     "guessed delay).",
     {"x": {"type": "integer"}, "y": {"type": "integer"},
      "w": {"type": "integer"}, "h": {"type": "integer"},
      "timeout": {"type": "number"}}, ["x", "y", "w", "h"]),
    ("amiga_read_file", "Copy a file OFF the Amiga to a local path.",
     {"amiga_path": {"type": "string"}, "local_path": {"type": "string"}},
     ["amiga_path", "local_path"]),
    ("amiga_write_file", "Copy a local file ONTO the Amiga (binary safe).",
     {"local_path": {"type": "string"}, "amiga_path": {"type": "string"}},
     ["local_path", "amiga_path"]),
    ("amiga_reset_input", "Release any stuck mouse buttons/qualifiers.", {}, []),
]


def tools_list():
    out = []
    for name, desc, props, req in TOOLS:
        out.append({
            "name": name,
            "description": desc,
            "inputSchema": {"type": "object", "properties": props, "required": req},
        })
    return {"tools": out}


def call_tool(name, a):
    if name == "amiga_shell":
        rc, out = with_retry(lambda n: n.exec_cmd(a["command"]))
        return _text(f'rc={rc}\n{out}')
    if name == "amiga_screenshot":
        if all(k in a for k in ("x", "y", "w", "h")):
            return _shot((a["x"], a["y"], a["w"], a["h"]))
        return _shot()
    if name == "amiga_ui_tree":
        return _text(with_retry(lambda n: n.ui_tree()) or '(no windows)')
    if name == "amiga_ui_click":
        g = with_retry(lambda n: n.ui_click(needle=a.get("text"), gid=a.get("id")))
        return _text(f'clicked id={g["id"]} {g["kind"]} "{g["label"]}" at {g["x"]},{g["y"]}'
                     if g else 'no gadget matched')
    if name == "amiga_menus":
        return _text(with_retry(lambda n: n.menus()) or '(no menu strip)')
    if name == "amiga_menu_select":
        it = with_retry(lambda n: n.menu_select(a["menu"], a["item"]))
        return _text(f'selected "{it}"' if it else 'no match (or item has no shortcut)')
    if name == "amiga_screens":
        return _text(with_retry(lambda n: n.screens()))
    if name == "amiga_pointer":
        x, y, w, h = with_retry(lambda n: n.pointer())
        return _text(f'pointer {x},{y} on {w}x{h}')
    if name == "amiga_click":
        ok = with_retry(lambda n: n.click(a["x"], a["y"], a.get("button", 0)))
        x, y, _w, _h = with_retry(lambda n: n.pointer())
        return _text(f'clicked at {x},{y}' if (x, y) == (a["x"], a["y"])
                     else f'WARNING: asked {a["x"]},{a["y"]} but landed {x},{y}')
    if name == "amiga_type":
        with_retry(lambda n: n.type_text(a["text"]))
        return _text('typed')
    if name == "amiga_key":
        with_retry(lambda n: n.press_key(a["code"]))
        return _text('key sent')
    if name == "amiga_region_changed":
        s = with_retry(lambda n: n.region_sum(a["x"], a["y"], a["w"], a["h"]))
        return _text(f'{s:08x}')
    if name == "amiga_wait_change":
        ok = with_retry(lambda n: n.wait_change(a["x"], a["y"], a["w"], a["h"],
                                                timeout=a.get("timeout", 10.0)))
        return _text('changed' if ok else 'timeout (no change)')
    if name == "amiga_read_file":
        n_ = with_retry(lambda n: n.get_file(a["amiga_path"], a["local_path"]))
        return _text(f'got {n_} bytes -> {a["local_path"]}')
    if name == "amiga_write_file":
        n_ = with_retry(lambda n: n.put_file(a["local_path"], a["amiga_path"]))
        return _text(f'put {n_} bytes -> {a["amiga_path"]}')
    if name == "amiga_reset_input":
        with_retry(lambda n: n.reset_input())
        return _text('input reset')
    raise ValueError(f'unknown tool {name}')


# ---- JSON-RPC over stdio --------------------------------------------------

def serve():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        mid, method, params = req.get("id"), req.get("method"), req.get("params") or {}
        try:
            if method == "initialize":
                result = {"protocolVersion": "2024-11-05",
                          "capabilities": {"tools": {}},
                          "serverInfo": {"name": "netharness-amiga", "version": "1.3"}}
            elif method == "tools/list":
                result = tools_list()
            elif method == "tools/call":
                result = call_tool(params.get("name"), params.get("arguments") or {})
            elif method in ("notifications/initialized", "initialized"):
                continue                     # notification: no reply
            elif method == "ping":
                result = {}
            else:
                if mid is None:
                    continue
                raise ValueError(f'unknown method {method}')
        except Exception as e:                                  # noqa: BLE001
            if mid is not None:
                sys.stdout.write(json.dumps({
                    "jsonrpc": "2.0", "id": mid,
                    "error": {"code": -32000, "message": str(e)}}) + "\n")
                sys.stdout.flush()
            continue
        if mid is not None:
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid,
                                         "result": result}) + "\n")
            sys.stdout.flush()


def main():
    global HOST, PORT
    argv = sys.argv[1:]
    while argv:
        if argv[0] == '--host':
            HOST = argv[1]; argv = argv[2:]
        elif argv[0] == '--port':
            PORT = int(argv[1]); argv = argv[2:]
        else:
            argv = argv[1:]
    serve()


if __name__ == '__main__':
    main()

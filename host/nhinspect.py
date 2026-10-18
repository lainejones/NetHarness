"""nhinspect.py - the PC side of NetHarness's inspection commands (1.14+).

Memory dumps, task and library tables, and a 68k disassembler that names library calls.
Disassembly runs here (Capstone), never on the Amiga: the harness only reads memory.

Library-call names come from the NDK's .fd files, read where they already are -
they are not shipped with NetHarness.  Set NH_FD_DIR to the fd drawer, or it tries
the usual amiga-gcc places (WSL's /opt/amiga/m68k-amigaos/ndk/lib/fd).
"""
import os, re

FD_DIRS = [os.environ.get('NH_FD_DIR', ''),
           r'\\wsl.localhost\Ubuntu\opt\amiga\m68k-amigaos\ndk\lib\fd',
           r'\\wsl$\Ubuntu\opt\amiga\m68k-amigaos\ndk\lib\fd',
           '/opt/amiga/m68k-amigaos/ndk/lib/fd']

_lvo_cache = None


def load_lvos():
    """{library name: {offset (positive, e.g. 552): function name}} from every *_lib.fd."""
    global _lvo_cache
    if _lvo_cache is not None:
        return _lvo_cache
    _lvo_cache = {}
    for d in FD_DIRS:
        if not d or not os.path.isdir(d):
            continue
        for f in os.listdir(d):
            if not f.lower().endswith('_lib.fd'):
                continue
            base = f[:-7].lower()
            table, bias = {}, 30
            for line in open(os.path.join(d, f), encoding='latin-1'):
                line = line.strip()
                if line.startswith('##bias'):
                    bias = int(line.split()[1])
                elif line.startswith('##') or line.startswith('*') or not line:
                    continue
                else:
                    m = re.match(r'([A-Za-z_][A-Za-z0-9_]*)\s*\(', line)
                    if m:
                        table[bias] = m.group(1)
                    bias += 6
            # the same fd serves "x.library", "x.device" or "x.resource" - keep the bare name
            _lvo_cache[base] = table
        break
    return _lvo_cache


def lib_key(name):
    """'dos.library' -> 'dos', 'timer.device' -> 'timer'"""
    n = name.lower()
    for suf in ('.library', '.device', '.resource'):
        if n.endswith(suf):
            return n[:-len(suf)]
    return n


def hexdump(data, addr):
    out = []
    for i in range(0, len(data), 16):
        chunk = data[i:i + 16]
        hx = ' '.join('%02x' % b for b in chunk)
        asc = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)
        out.append('%08x  %-47s  %s' % (addr + i, hx, asc))
    return '\n'.join(out)


def parse_records(text, tag):
    """split 'X a b "quoted words" c' records of one tag into lists"""
    recs = []
    for line in text.splitlines():
        if not line.startswith(tag + ' '):
            continue
        recs.append([m.group(1) if m.group(1) is not None else m.group(2)
                     for m in re.finditer(r'"([^"]*)"|(\S+)', line[len(tag) + 1:])])
    return recs


def format_tasks(text):
    tasks, segs = parse_records(text, 'T'), parse_records(text, 'S')
    out = ['%-10s %-4s %4s %-5s %10s %7s %7s  %s' % ('ADDRESS', 'KIND', 'PRI', 'STATE', 'SIGWAIT', 'STACK', 'IN USE', 'NAME')]
    for t in tasks:
        addr, kind, pri, state, sigw, stack, used, name, cmd = t[:9]
        st, us = int(stack), int(used)
        warn = '  <-- %d%% of its stack' % (us * 100 // st) if st and us * 100 >= st * 80 else ''
        label = name + (' [%s]' % cmd if cmd else '')
        out.append('%-10s %-4s %4s %-5s %10s %7d %7d  %s%s' % (addr, kind, pri, state, sigw, st, us, label, warn))
    if segs:
        out.append('')
        out.append('code segments:')
        names = {t[0]: (t[8] or t[7]) for t in tasks}
        for s in segs:
            out.append('  %-24s seg %s  %s  %s bytes' % (names.get(s[0], s[0]), s[1], s[2], s[3]))
    return '\n'.join(out)


def format_libs(text):
    recs = parse_records(text, 'L')
    out = ['%-10s %-28s %8s %5s  %s' % ('BASE', 'NAME', 'VERSION', 'OPEN', 'ID')]
    for r in recs:
        base, ver, rev, opn, flags, neg, pos, name, ident = r[:9]
        out.append('%-10s %-28s %8s %5s  %s' % (base, name, '%s.%s' % (ver, rev), opn, ident.strip()))
    return '\n'.join(out)


def segment_of(addr, tasks_text):
    """which program's code segment holds addr: (label, seg, offset) or None"""
    tasks = {t[0]: (t[8] or t[7]) for t in parse_records(tasks_text, 'T')}
    for s in parse_records(tasks_text, 'S'):
        start, size = int(s[2], 16), int(s[3])
        if start <= addr < start + size:
            return tasks.get(s[0], s[0]), int(s[1]), addr - start
    return None


def disassemble(data, addr, a6_lib=None, libs_text=None, count=None, peek=None):
    """68k disassembly with library calls named.  a6_lib: a library name, or None to track it:
    'movea.l 4.w,a6' = exec, and 'movea.l <absolute>,a6' is resolved by reading that variable
    (peek(addr, 4)) and matching the base it holds against the LIBS list."""
    import capstone
    md = capstone.Cs(capstone.CS_ARCH_M68K, capstone.CS_MODE_BIG_ENDIAN | capstone.CS_MODE_M68K_040)
    md.skipdata = True                       # data mixed into code: step over it, don't stop
    lvos = load_lvos()
    bases = {}
    if libs_text:
        for r in parse_records(libs_text, 'L'):
            bases[int(r[0], 16)] = lib_key(r[7])
    a6 = lib_key(a6_lib) if a6_lib and not a6_lib.lower().startswith('0x') else None
    if a6_lib and a6_lib.lower().startswith('0x'):
        a6 = bases.get(int(a6_lib, 16))
    out = []
    for n, ins in enumerate(md.disasm(data, addr)):
        if count and n >= count:
            break
        text = ('%s %s' % (ins.mnemonic, ins.op_str)).strip()
        note = ''
        low = text.lower().replace(' ', '')
        if re.search(r'move(a)?\.l\$?\(?\$?4\)?\.w,a6$', low) or re.search(r'move(a)?\.l\(\$4\)\.w,a6$', low):
            a6 = 'exec'; note = 'a6 = exec'
        elif re.search(r'move(a)?\.l.*,a6$', low):
            a6 = None
            ma = re.match(r'move(a)?\.l\(?\$([0-9a-f]+)\)?(\.l)?,a6$', low)
            if ma and peek and bases:
                try:
                    import struct as _st
                    base = _st.unpack('>I', peek(int(ma.group(2), 16), 4))[0]
                    a6 = bases.get(base)
                    note = 'a6 = %s' % (a6 or ('0x%08x (not a library base)' % base))
                except Exception:
                    pass
        m = re.search(r'(jsr|jmp)\s*-\$?([0-9a-f]+)\(a6\)', text.lower())
        if m:
            off = int(m.group(2), 16)
            if a6 and a6 in lvos and off in lvos[a6]:
                note = '%s/%s' % (a6, lvos[a6][off])
            else:
                guesses = ['%s/%s' % (k, v[off]) for k, v in sorted(lvos.items())
                           if k in ('exec', 'dos', 'intuition', 'graphics') and off in v]
                note = 'LVO -%d%s' % (off, (' (if a6 = ' + ' | '.join(guesses) + ')') if guesses else '')
        hx = ' '.join('%02x' % b for b in ins.bytes)
        out.append('%08x  %-20s %-32s%s' % (ins.address, hx, text, ('  ; ' + note) if note else ''))
    if not out:
        out.append('(nothing decoded)')
    return '\n'.join(out)

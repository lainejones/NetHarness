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
        if state == 'WAIT' and int(sigw, 16) == 0:
            warn += '  <-- waits for nothing: suspended (see CRASH)'
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


# ---- CRASH (1.15) -----------------------------------------------------------------------------

TRAPS = {2: 'bus error', 3: 'address error', 4: 'illegal instruction', 5: 'divide by zero',
         6: 'CHK instruction', 7: 'TRAPV', 8: 'privilege violation', 9: 'trace', 10: 'line-A instruction',
         11: 'line-F instruction (no FPU / unknown FPU op)', 14: 'format error'}


def trap_name(n):
    if 32 <= n <= 47:
        return 'TRAP #%d' % (n - 32)
    return TRAPS.get(n, 'exception %d' % n)


def parse_crash(text):
    """-> (state dict, [record dicts]) from a CRASH report"""
    state, recs, cur = {}, [], None
    for line in text.splitlines():
        if line.startswith('STATE '):
            p = line.split()
            kv = dict(zip(p[2::2], p[3::2]))
            state = {'on': p[1] == 'on', 'count': int(kv.get('count', 0)), 'cpu': int(kv.get('cpu', 0)),
                     'mode': kv.get('mode', 'pass')}
        elif line.startswith('ERROR '):
            state['error'] = line[6:]
        elif line.startswith('R '):
            r = parse_records(line, 'R')[0]
            cur = {'seq': int(r[0]), 'trap': int(r[1]), 'task': int(r[2], 16), 'usp': int(r[3], 16), 'name': r[4],
                   'suspended': len(r) > 5 and r[5] == 'suspended'}
            recs.append(cur)
        elif line.startswith('G ') and cur is not None:
            v = [int(x, 16) for x in line.split()[1:]]
            cur['d'], cur['a'] = v[:8], v[8:15]
        elif line.startswith('F ') and cur is not None:
            cur['frame'] = bytes.fromhex(line[2:].strip())
        elif line.startswith('K ') and cur is not None:
            cur['stack'] = bytes.fromhex(line[2:].strip())
    return state, recs


def frame_info(frame, cpu):
    """SR, PC, format and the fault address (if the frame has one) from the CPU's exception frame"""
    import struct
    sr, pc = struct.unpack('>HI', frame[:6])
    info = {'sr': sr, 'pc': pc, 'format': None, 'fault': None}
    if cpu & 1:                                   # AFF_68010: 68010+ frames carry a format word
        fw = struct.unpack('>H', frame[6:8])[0]
        fmt = fw >> 12
        info['format'] = fmt
        if fmt == 2 and len(frame) >= 12:         # address error (040) / trap-after frames: the address
            info['fault'] = struct.unpack('>I', frame[8:12])[0]
        elif fmt == 7 and len(frame) >= 24:       # 68040 access fault: effective address
            info['fault'] = struct.unpack('>I', frame[0x14:0x18])[0]
        elif fmt in (0xA, 0xB) and len(frame) >= 20:   # 68020/030 bus fault: data cycle fault address
            info['fault'] = struct.unpack('>I', frame[0x10:0x14])[0]
    return info


def cpu_name(cpu):
    return ('68060' if cpu & 0x80 else '68040' if cpu & 8 else '68030' if cpu & 4 else
            '68020' if cpu & 2 else '68010' if cpu & 1 else '68000') + (' + FPU' if cpu & 0x30 or cpu & 0x40 else '')


def code_start_before(pc, peek, back=16):
    """a start address up to `back` bytes before pc from which decoding lands exactly ON pc"""
    import capstone
    md = capstone.Cs(capstone.CS_ARCH_M68K, capstone.CS_MODE_BIG_ENDIAN | capstone.CS_MODE_M68K_040)
    for b in range(back, 0, -2):
        try:
            data = peek(pc - b, b + 2)
        except Exception:
            continue
        addrs = [i.address for i in md.disasm(data, pc - b)]
        if pc in addrs:
            return pc - b
    return pc


def format_crash(text, tasks_text='', libs_text='', peek=None):
    state, recs = parse_crash(text)
    out = []
    if state.get('error'):
        out.append('ERROR: ' + state['error'])
    mode = {'suspend': ' (crashed programs are suspended, the machine carries on)',
            'pass': ' (crashes are passed on to the OS as usual)'}.get(state.get('mode'), '')
    out.append('crash capture is %s%s - %d crash%s caught (%s)' %
               ('ON' if state.get('on') else 'OFF', mode if state.get('on') else '', state.get('count', 0),
                '' if state.get('count') == 1 else 'es', cpu_name(state.get('cpu', 0))))
    for r in recs:
        fi = frame_info(r.get('frame', b'\0' * 8), state.get('cpu', 0))
        out.append('')
        out.append('#%d  %s in task "%s" (0x%08x) - %s' % (r['seq'], trap_name(r['trap']), r['name'], r['task'],
                   'suspended by NetHarness' if r.get('suspended') else 'passed on to the OS'))
        line = '    PC 0x%08x  SR 0x%04x' % (fi['pc'], fi['sr'])
        # format 2 holds an address: the access address for an address error, the instruction
        # that caused it for the "after" exceptions (divide by zero, CHK, TRAPV, trace)
        after = fi['format'] == 2 and r['trap'] in (5, 6, 7, 9)
        mark = fi['fault'] if after and fi['fault'] is not None else fi['pc']
        if fi['fault'] is not None:
            line += ('  faulting instruction at 0x%08x' if after else '  fault address 0x%08x') % fi['fault']
        if fi['format'] is not None:
            line += '  (frame format $%X)' % fi['format']
        out.append(line)
        w = segment_of(mark, tasks_text) if tasks_text else None
        out.append('    ' + ('in %s, code segment %d, offset 0x%x' % w if w
                             else 'the PC is not in a running program\'s code (ROM, a library, or the program has gone)'))
        d, a = r.get('d', [0] * 8), r.get('a', [0] * 7)
        out.append('    ' + '  '.join('d%d %08x' % (i, d[i]) for i in range(4)))
        out.append('    ' + '  '.join('d%d %08x' % (i, d[i]) for i in range(4, 8)))
        out.append('    ' + '  '.join('a%d %08x' % (i, a[i]) for i in range(4)))
        out.append('    ' + '  '.join('a%d %08x' % (i, a[i]) for i in range(4, 7)) + '  a7 %08x (usp)' % r['usp'])
        st = r.get('stack', b'')
        if any(st):
            import struct
            longs = struct.unpack('>16I', st[:64])
            out.append('    stack: ' + ' '.join('%08x' % x for x in longs[:8]))
            out.append('           ' + ' '.join('%08x' % x for x in longs[8:]))
        else:
            out.append('    stack: (usp is outside the task\'s own stack - not read)')
        if peek:
            try:
                start = code_start_before(mark, peek)
                code = peek(start, (mark - start) + 24)
                out.append('    code at the PC (>> = the instruction that faulted, or the one after a TRAP):')
                for l in disassemble(code, start, None, libs_text, 10, peek).splitlines():
                    out.append('    %s %s' % ('>>' if l.startswith('%08x' % mark) else '  ', l))
            except Exception as e:
                out.append('    (code at the PC not readable: %s)' % e)
    return '\n'.join(out)


# ---- LEAK (1.16) ------------------------------------------------------------------------------

LEAK_KINDS = {1: 'AllocMem', 2: 'FreeMem', 3: 'AllocVec', 4: 'FreeVec'}


def format_leak(text, libs_text='', running=None):
    """the LEAK report: what the tracked program still holds, oldest first, each placed in its code"""
    head, segs, ents, more = {}, [], [], 0
    for line in text.splitlines():
        if line.startswith('STATE '):
            p = parse_records(line, 'STATE')[0]
            head = {'on': p[0] == 'on'}
            head.update(dict(zip(p[1::2], p[2::2])))
        elif line.startswith('ERROR '):
            head['error'] = line[6:]
        elif line.startswith('S '):
            t, start, size = line.split()[1:4]
            segs.append((int(t, 16), int(start, 16), int(size)))
        elif line.startswith('A '):
            seq, kind, addr, size, pc, pc2, task = line.split()[1:8]
            ents.append({'seq': int(seq), 'kind': int(kind), 'addr': int(addr, 16), 'size': int(size),
                         'pc': int(pc, 16), 'pc2': int(pc2, 16), 'task': int(task, 16)})
        elif line.startswith('MORE '):
            more = int(line.split()[1])
    out = []
    if head.get('error'):
        out.append('ERROR: ' + head['error'])
    name = head.get('name', '')
    out.append('leak tracking is %s for "%s": %s allocations, %s frees, %s still held%s' % (
        'ON' if head.get('on') else 'OFF', name, head.get('allocs', 0), head.get('frees', 0), head.get('live', 0),
        (', %s NOT RECORDED (table full)' % head['dropped']) if head.get('dropped', '0') != '0' else ''))

    def where(a):
        for i, (t, start, size) in enumerate(segs):
            if start <= a < start + size:
                n = sum(1 for x in segs[:i] if x[0] == t)        # segment number within that run
                return '%s seg %d +0x%x' % (name, n, a - start)
        if 0xF80000 <= a < 0x1000000 or 0xE00000 <= a < 0xE80000:
            return 'ROM 0x%x' % a
        return '0x%08x' % a

    # exec's AllocVec is built on AllocMem: hide the AllocMem that sits 4 bytes under an AllocVec
    vecs = {e['addr'] for e in ents if e['kind'] == 3}
    ents = [e for e in ents if not (e['kind'] == 1 and e['addr'] + 4 in vecs)]
    ents.sort(key=lambda e: e['seq'])
    total = sum(e['size'] for e in ents)
    if running is False:
        out.append('the program has ended, so everything below was LEAKED (%d block%s, %d bytes known):' %
                   (len(ents), '' if len(ents) == 1 else 's', total))
    elif ents:
        out.append('still held (the program may still be running, so not all of this is a leak yet):')
    for e in ents:
        here = where(e['pc'])
        line = '  #%-4d %-8s %8s bytes at 0x%08x  from %s' % (e['seq'], LEAK_KINDS.get(e['kind'], '?'),
                                                            e['size'] if e['size'] else '?', e['addr'], here)
        if e['pc2'] and not here.startswith(name):
            line += '  <- called from %s' % where(e['pc2'])
        out.append(line)
    if more:
        out.append('  ... and %d more (the report is capped)' % more)
    if not ents and running is False:
        out.append('nothing left over - no leaks')
    return '\n'.join(out)

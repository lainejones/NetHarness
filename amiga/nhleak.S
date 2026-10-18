| nhleak.S - NetHarness's leak tracker stubs (1.16): SetFunction() replacements for
| exec's AllocMem, FreeMem, AllocVec and FreeVec.
|
| Each stub always does the real work by calling the function it replaced.  Around
| that it calls the HOOK - netharness.c's lk_hook(kind, address, size, caller) - but
| only while a harness has one installed (offset 4, 0 = none).  A busy count
| (offset 8) is kept while the hook runs, so a harness that is about to exit can clear
| the hook and wait until no task is still inside its code.
|
| The block between _lk_blob and _lk_blob_end is COPIED into memory that is never
| freed (exec's vectors point into it; other patches may chain to it), so it must
| outlive any harness.  Position-independent, 68000 instructions only.
|
| Order matters: an allocation is recorded AFTER it succeeds; a free is recorded
| BEFORE the memory is given back (afterwards another task could be handed the same
| address and record it first).
|
| Block layout (offsets used by netharness.c):
|   0 magic 'NHLK'   4 hook   8 busy
|  12 old AllocMem  16 old FreeMem  20 old AllocVec  24 old FreeVec
|  28 struct SignalSemaphore "NetHarness.leak" (46 bytes), to 80
|  80 the stubs: AllocMem +0, FreeMem, AllocVec, FreeVec (addresses exported below)

    .text
    .globl  _lk_blob
    .globl  _lk_blob_end
    .globl  _lk_allocmem
    .globl  _lk_freemem
    .globl  _lk_allocvec
    .globl  _lk_freevec
    .globl  _lk_semname

_lk_blob:
    .long   0x4E484C4B              | 'NHLK'
    .long   0                       | hook
    .long   0                       | busy
    .long   0,0,0,0                 | the old vectors
    .space  52                      | the semaphore (28..79)

| AllocMem(d0 size, d1 flags) -> d0
_lk_allocmem:
    movem.l d0-d1,-(sp)             | (sp) size, 4 flags, 8 the caller's return address
    movea.l _lk_blob+12(pc),a0
    jsr     (a0)
    movem.l (sp)+,a0-a1             | a0 = size
    tst.l   d0
    beq.s   1f
    move.l  d0,-(sp)                | keep the result; the caller is now at 4(sp)
    move.l  4(sp),-(sp)             | caller
    move.l  a0,-(sp)                | size
    move.l  d0,-(sp)                | address
    pea     1.w                     | kind 1: AllocMem
    bsr.s   lk_common
    lea     16(sp),sp
    move.l  (sp)+,d0
1:  rts

| AllocVec(d0 size, d1 flags) -> d0
_lk_allocvec:
    movem.l d0-d1,-(sp)
    movea.l _lk_blob+20(pc),a0
    jsr     (a0)
    movem.l (sp)+,a0-a1
    tst.l   d0
    beq.s   1f
    move.l  d0,-(sp)
    move.l  4(sp),-(sp)
    move.l  a0,-(sp)
    move.l  d0,-(sp)
    pea     3.w                     | kind 3: AllocVec
    bsr.s   lk_common
    lea     16(sp),sp
    move.l  (sp)+,d0
1:  rts

| FreeMem(a1 block, d0 size)
_lk_freemem:
    movem.l d0/a1,-(sp)             | (sp) size, 4 block, 8 caller
    move.l  8(sp),-(sp)             | caller
    move.l  4(sp),-(sp)             | size
    move.l  12(sp),-(sp)            | address
    pea     2.w                     | kind 2: FreeMem
    bsr.s   lk_common
    lea     16(sp),sp
    movem.l (sp)+,d0/a1
    movea.l _lk_blob+16(pc),a0
    jmp     (a0)                    | the real FreeMem returns straight to the caller

| FreeVec(a1 block)
_lk_freevec:
    move.l  a1,-(sp)                | (sp) block, 4 caller
    move.l  4(sp),-(sp)             | caller
    clr.l   -(sp)                   | size (not known)
    move.l  8(sp),-(sp)             | address
    pea     4.w                     | kind 4: FreeVec
    bsr.s   lk_common
    lea     16(sp),sp
    movea.l (sp)+,a1
    movea.l _lk_blob+24(pc),a0
    jmp     (a0)

| lk_common(kind, address, size, caller): call the hook, if there is one, counted busy.
| The hook is ordinary C: it may change d0/d1/a0/a1, keeps the rest.
lk_common:
    lea     _lk_blob(pc),a0
    addq.l  #1,8(a0)
    move.l  4(a0),d1
    beq.s   2f
    movea.l d1,a1
    move.l  16(sp),-(sp)            | caller
    move.l  16(sp),-(sp)            | size
    move.l  16(sp),-(sp)            | address
    move.l  16(sp),-(sp)            | kind
    jsr     (a1)
    lea     16(sp),sp
2:  lea     _lk_blob(pc),a0
    subq.l  #1,8(a0)
    rts

_lk_semname:
    .asciz  "NetHarness.leak"
    .even
_lk_blob_end:

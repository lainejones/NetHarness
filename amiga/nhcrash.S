| nhcrash.S - NetHarness's crash catcher (1.15): a task trap handler for exec's
| SysBase->TaskTrapCode, so every task that has no handler of its own comes here
| when it takes a CPU exception (bus/address error, illegal instruction, divide
| by zero, line A/F, TRAP #n ...).
|
| It writes one record of what the task looked like at that moment.  Then, by the
| mode word at offset 4:
|   1 SUSPEND - the task is resumed in a stub that sleeps forever (Wait(0)), so the
|               machine carries on: exec's default for a task is a dead-end alert,
|               a guru and a reboot, which also wipes the record.  Only frames that
|               can be resumed by rewriting the PC are handled this way (68010+
|               formats 0 and 2; on a 68000 everything but bus/address errors), and
|               only user-mode crashes; anything else is passed on.
|   0 PASS    - hand the stack on, untouched, to the handler that was there before.
|
| The whole block between _nh_blob and _nh_blob_end is COPIED by netharness.c into
| memory that is never freed: tasks keep a pointer to the handler, so it must
| outlive any harness.  Position-independent, 68000 instructions only.
|
| Entered in supervisor mode with (sp) = the trap number, then the CPU's exception
| frame (SR.w PC.l [format/vector.w on 68010+] ...).
|
| Block layout (offsets used by netharness.c):
|   0  magic 'NHCR'        4  mode (1 suspend, 0 pass)        8  the previous handler
|  12  count (records written so far)
|  16  struct SignalSemaphore (46 bytes) - named "NetHarness.crash", how a later
|      harness finds this block again
|  64  4 records of 240 bytes, record n%4 holds crash n:
|        0 trap number  4 task  8 usp  12 d0-d7/a0-a6 (60)
|       72 64 bytes of the supervisor stack from the frame (the frame + beyond)
|      136 64 bytes of the task's stack at usp (0s if usp is outside its stack)
|      200 the task's name (31 chars + 0)   232 sequence number
|      236 what happened: 1 suspended, 0 passed on
| 1024  the handler code, the suspend stub, then the semaphore's name

    .text
    .globl  _nh_blob
    .globl  _nh_blob_end
    .globl  _nh_trap
    .globl  _nh_semname

_nh_blob:
    .long   0x4E484352              | 'NHCR'
    .long   1                       | mode
    .long   0                       | previous handler (set when installed)
    .long   0                       | count
    .space  48                      | the semaphore (16..63)
    .space  960                     | 4 records x 240

_nh_trap:
    movem.l d0-d7/a0-a6,-(sp)       | 60 bytes: the task's registers (a7 is in usp)
    lea     _nh_blob(pc),a5
    move.l  12(a5),d0               | count
    move.l  d0,d1
    andi.l  #3,d1
    mulu.w  #240,d1
    lea     64(a5,d1.l),a4          | this record
    addq.l  #1,d0
    move.l  d0,12(a5)
    move.l  d0,232(a4)              | sequence number
    clr.l   236(a4)                 | passed on, until we decide otherwise
    move.l  60(sp),(a4)             | trap number
    movea.l 4.w,a0
    move.l  276(a0),4(a4)           | SysBase->ThisTask
    move.l  usp,a1
    move.l  a1,8(a4)

    lea     12(a4),a2               | the registers
    movea.l sp,a3
    moveq   #14,d2
1:  move.l  (a3)+,(a2)+
    dbra    d2,1b

    lea     64(sp),a3               | the exception frame and what follows it
    lea     72(a4),a2
    moveq   #15,d2
2:  move.l  (a3)+,(a2)+
    dbra    d2,2b

    movea.l 4(a4),a0                | the task's own stack, only if usp is inside it
    lea     136(a4),a2
    moveq   #15,d2
    moveq   #0,d4                   | d4 = 1: usp is good
    cmpa.l  58(a0),a1               | usp < tc_SPLower ?
    bcs.s   4f
    move.l  a1,d3
    addi.l  #64,d3
    cmp.l   62(a0),d3               | usp + 64 > tc_SPUpper ?
    bhi.s   4f
    move.l  a1,d3                   | odd usp: a long read would fault again
    btst    #0,d3
    bne.s   4f
    moveq   #1,d4
3:  move.l  (a1)+,(a2)+
    dbra    d2,3b
    bra.s   5f
4:  clr.l   (a2)+
    dbra    d2,4b

5:  movea.l 4(a4),a0                | the task's name
    lea     200(a4),a2
    clr.b   (a2)
    move.l  10(a0),d3               | ln_Name
    beq.s   7f
    movea.l d3,a1
    moveq   #30,d2
6:  move.b  (a1)+,(a2)+
    dbeq    d2,6b
    clr.b   (a2)
7:
    tst.l   4(a5)                   | mode: pass on?
    beq.s   chain
    btst    #5,64(sp)               | the crash happened in supervisor mode: not ours to resume
    bne.s   chain
    movea.l 4.w,a0
    btst    #0,297(a0)              | AttnFlags & AFF_68010: does the frame carry a format word?
    bne.s   10f
    move.l  60(sp),d0               | 68000: a bus/address error frame can't be resumed
    cmpi.l  #3,d0
    bls.s   chain
    bra.s   11f
10: move.b  70(sp),d0               | 68010+: the format is the top nibble of the frame's 4th word
    andi.b  #0xF0,d0
    beq.s   11f                     | format 0
    cmpi.b  #0x20,d0
    bne.s   chain                   | format 2 is fine; bus fault frames (7/8/A/B) are not
11: lea     _nh_suspend(pc),a0
    move.l  a0,66(sp)               | the task resumes in the stub
    andi.w  #0x3FFF,64(sp)          | ... without trace bits
    tst.l   d4                      | a wild stack pointer: give the stub a safe spot in its own stack
    bne.s   12f
    movea.l 4(a4),a0
    move.l  62(a0),d3
    subi.l  #256,d3
    andi.b  #0xFC,d3
    movea.l d3,a0
    move.l  a0,usp
12: move.l  #1,236(a4)              | suspended
    movem.l (sp)+,d0-d7/a0-a6
    addq.l  #4,sp                   | drop the trap number
    rte

chain:
    movem.l (sp)+,d0-d7/a0-a6
    move.l  _nh_blob+8(pc),-(sp)    | on to the previous handler, stack as we found it
    rts

| user mode: where a crashed task is sent to sleep, for good
_nh_suspend:
    movea.l 4.w,a6
20: moveq   #0,d0
    jsr     -318(a6)                | Wait(0)
    bra.s   20b

_nh_semname:
    .asciz  "NetHarness.crash"
    .even
_nh_blob_end:

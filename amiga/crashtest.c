/* crashtest - crash on purpose, to test NetHarness's CRASH capture.
 *
 *   crashtest illegal | div0 | odd | trap | linef
 *
 * Before crashing it puts known values in the registers (d0-d7 = 0x0D0D0000 + n,
 * a0-a3 = 0x0A0A0000 + n) so the captured record can be checked exactly.
 * Build: m68k-amigaos-gcc -m68020 -O1 -noixemul -o crashtest crashtest.c
 */
#include <stdio.h>
#include <string.h>

#define LOAD_REGS \
    "move.l #0x0D0D0000,d0\n\t" "move.l #0x0D0D0001,d1\n\t" "move.l #0x0D0D0002,d2\n\t" \
    "move.l #0x0D0D0003,d3\n\t" "move.l #0x0D0D0004,d4\n\t" "move.l #0x0D0D0005,d5\n\t" \
    "move.l #0x0D0D0006,d6\n\t" "move.l #0x0D0D0007,d7\n\t" "move.l #0x0A0A0000,a0\n\t" \
    "move.l #0x0A0A0001,a1\n\t" "move.l #0x0A0A0002,a2\n\t" "move.l #0x0A0A0003,a3\n\t"
#define CLOBBERS "d0","d1","d2","d3","d4","d5","d6","d7","a0","a1","a2","a3","memory","cc"

int main(int argc, char **argv)
{
    const char *m = argc > 1 ? argv[1] : "";
    printf("crashtest: %s\n", m);
    fflush(stdout);
    if (!strcmp(m, "illegal")) __asm__ volatile (LOAD_REGS "illegal" ::: CLOBBERS);
    else if (!strcmp(m, "div0")) __asm__ volatile (LOAD_REGS "moveq #0,d7\n\tdivu.w d7,d0" ::: CLOBBERS);
    else if (!strcmp(m, "odd")) __asm__ volatile (LOAD_REGS "lea 1(pc),a4\n\tjmp (a4)" ::: CLOBBERS, "a4");
    else if (!strcmp(m, "trap")) __asm__ volatile (LOAD_REGS "trap #5" ::: CLOBBERS);
    else if (!strcmp(m, "linef")) __asm__ volatile (LOAD_REGS ".word 0xF5FF" ::: CLOBBERS);
    else { printf("usage: crashtest illegal|div0|odd|trap|linef\n"); return 5; }
    printf("crashtest: still alive?\n");
    return 0;
}

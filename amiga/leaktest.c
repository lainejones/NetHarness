/* leaktest - leak on purpose, to test NetHarness's LEAK tracker.
 *
 * Allocates seven blocks of sizes that are easy to spot and frees three:
 *   freed:   AllocMem 111, AllocVec 222, AllocMem 333
 *   leaked:  AllocMem 100, AllocVec 2000, AllocMem 444, AllocVec 555
 * and opens a file it never closes (dos allocates the handle and its buffer) - leaktest [file].
 * Build: m68k-amigaos-gcc -m68020 -O1 -noixemul -o leaktest leaktest.c
 */
#include <exec/memory.h>
#include <proto/exec.h>
#include <proto/dos.h>
#include <stdio.h>

int main(int argc, char **argv)
{
    APTR a = AllocMem(100, MEMF_ANY);
    APTR f1 = AllocMem(111, MEMF_ANY);
    APTR b = AllocVec(2000, MEMF_ANY | MEMF_CLEAR);
    APTR f2 = AllocVec(222, MEMF_ANY);
    APTR c = AllocMem(444, MEMF_PUBLIC);
    APTR f3 = AllocMem(333, MEMF_ANY);
    APTR d = AllocVec(555, MEMF_ANY);
    BPTR fh = Open((STRPTR)(argc > 1 ? argv[1] : "RAM:leaktest.tmp"), MODE_NEWFILE);   /* a new name each run: the last run's handle still holds its file */
    if (fh) Write(fh, "x", 1);
    FreeMem(f1, 111);
    FreeVec(f2);
    FreeMem(f3, 333);
    printf("leaktest: leaked 100@%p 2000@%p 444@%p 555@%p and a file handle\n", a, b, c, d);
    Delay(100);                         /* a moment to look at it while it runs */
    return 0;
}

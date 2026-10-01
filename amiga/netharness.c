/* netharness.c - TCP test-automation harness (Amiga side)
 *
 * Port of A314TestHarness's amiga/testharness.c from the A314 packet link to
 * plain TCP over the resident bsdsocket.library (Roadshow on the A4000).
 * Unlike the A314 version there is no Pi middleman: this program LISTENS on
 * a TCP port and the controlling host connects directly.
 *
 *  - injects synthetic keyboard/mouse input via input.device (IND_WRITEEVENT)
 *  - captures the active screen's bitmap and streams it back on request
 *  - NEW vs the A314 harness: CMD_EXEC runs an AmigaDOS command line via
 *    SystemTags() and returns its output (kills the whole "drive the
 *    Execute-Command requester by synthetic clicks" dance; ARexx scripting
 *    rides on it via `rx "..."`), and CMD_PING answers with an ack for
 *    cheap liveness checks.
 *
 * Wire protocol - big-endian multi-byte fields, byte stream with reassembly
 * (TCP preserves bytes, not message boundaries - same rule as A314):
 *
 *   host -> Amiga:
 *     CMD_MOUSE_MOVE   = 1   payload: dx2 dy2         (signed 16-bit delta)
 *     CMD_MOUSE_BUTTON = 2   payload: button1 state1  (0=L 1=R 2=M; 0=up 1=down)
 *     CMD_KEY          = 3   payload: keycode1 state1 (raw Amiga keycode)
 *     CMD_HOME_MOUSE   = 4   payload: none            (slam pointer to top-left)
 *     CMD_SCREENSHOT   = 5   payload: none
 *     CMD_REBOOT       = 6   payload: none            (ColdReboot(), never returns)
 *     CMD_RESET_INPUT  = 7   payload: none            (release held buttons/quals)
 *     CMD_EXEC         = 8   payload: len2 cmdline[len]  (AmigaDOS command)
 *     CMD_PING         = 9   payload: none
 *   v1.3 additions (see "semantic layer" below):
 *     CMD_POINTER      = 10  payload: none            (where IS the pointer?)
 *     CMD_UITREE       = 11  payload: none            (windows+gadgets, text)
 *     CMD_MENUS        = 12  payload: none            (menu strip, text)
 *     CMD_SCREENS      = 13  payload: none            (open screens, text)
 *     CMD_REGION_SUM   = 14  payload: x2 y2 w2 h2     (checksum a region)
 *     CMD_SHOT_REGION  = 15  payload: x2 y2 w2 h2     (partial screenshot)
 *     CMD_GETFILE      = 16  payload: len2 path[len]
 *     CMD_PUTFILE      = 17  payload: plen2 path[plen] dlen4 data[dlen]
 *   v1.10 additions:
 *     CMD_EXEC_T       = 19  payload: secs2 len2 cmdline[len]  (EXEC with a
 *       time limit, 0 = none; on timeout the command gets Ctrl-C, and the
 *       reply's rc is -2 with a note after its output)
 *     CMD_HELLO        = 20  payload: none  -> RESP_TEXT "netharness <ver>"
 *       (an older harness drops the unknown byte, so a client can send HELLO
 *       then PING and tell from the replies whether EXEC_T is understood)
 *     CMD_RELOAD       = 18  payload: none  (apply a staged
 *       C:netharness.new and restart IN PLACE - no machine reboot;
 *       ColdReboot() greyscreens some machines. Replies ACK+1 on
 *       handoff, ACK+0 if the update could not be applied.)
 *
 *   Amiga -> host:
 *     RESP_SCREENSHOT_HDR = 0x81  payload: width2 height2 depth1 bpr2, then
 *       raw bitplane bytes (bpr*height per plane, depth planes back to back).
 *     RESP_ACK            = 0x82  1 byte; after each injected input command
 *       and for CMD_PING.
 *     RESP_EXEC           = 0x83  payload: rc4 outlen4, then outlen bytes of
 *       the command's captured output.
 *     RESP_POINTER        = 0x84  payload: x2 y2 scrw2 scrh2 (screen-relative)
 *     RESP_TEXT           = 0x85  payload: len4 text[len]   (UITREE/MENUS/SCREENS)
 *     RESP_FILE           = 0x86  payload: status4 len4 data[len] (status 0 = ok)
 *     RESP_SUM            = 0x87  payload: sum4
 *
 * WHY THE SEMANTIC LAYER (v1.3): driving a GUI by pixel coordinates is the
 * single biggest source of wasted time with this harness — the pointer is an
 * invisible hardware sprite, and MOVETO's one-shot relative jump gets mangled
 * by Intuition's mouse acceleration, so clicks silently land on the wrong row
 * or a few pixels off a gadget and you cannot see why.  CMD_POINTER makes
 * positioning CLOSED-LOOP (move, read back where you actually are, correct),
 * and CMD_UITREE/CMD_MENUS let the controller address things by WHAT THEY ARE
 * ("the Insert button") instead of guessing where they are.  (Same idea as
 * thomas-luebker/amimcp's amiga_ui_tree / amiga_ui_click.)
 *
 * The Amiga side is single-threaded, so responses never interleave.
 *
 * Build (WSL/bebbo): make      -> netharness (see Makefile)
 * Run on the Amiga:  run >NIL: netharness    (add to S:User-Startup)
 */

#include <exec/types.h>
#include <exec/io.h>
#include <exec/memory.h>
#include <devices/input.h>
#include <devices/inputevent.h>
#include <intuition/intuition.h>
#include <intuition/screens.h>
#include <graphics/gfx.h>
#include <dos/dos.h>
#include <dos/dostags.h>
#include <proto/exec.h>
#include <proto/dos.h>
#include <proto/intuition.h>
#include <proto/graphics.h>
#include <cybergraphx/cybergraphics.h>
#include <proto/cybergraphics.h>
#include <string.h>
#include <stdio.h>

#include <sys/socket.h>
#include <netinet/in.h>
#include <proto/bsdsocket.h>

#define LISTEN_PORT 7800            /* default; override with an argv port */

/* Standard AmigaDOS version cookie, so `version C:netharness` reports the
 * build instead of "Could not find version information" — with the harness
 * deployed on several machines, "which build is on this one?" needs to be a
 * one-liner rather than a guess from the file size. Date is DD.MM.YYYY.
 * BUMP NH_VERSION whenever the wire protocol or commands change. */
#define NH_VERSION "1.10"
#define NH_VERDATE "30.9.2026"
static const char verstag[] __attribute__((used)) =
    "$VER: netharness " NH_VERSION " (" NH_VERDATE ")";

#define CMD_MOUSE_MOVE    1
#define CMD_MOUSE_BUTTON  2
#define CMD_KEY           3
#define CMD_HOME_MOUSE    4
#define CMD_SCREENSHOT    5
#define CMD_REBOOT        6
#define CMD_RESET_INPUT   7
#define CMD_EXEC          8
#define CMD_PING          9
#define CMD_POINTER      10
#define CMD_UITREE       11
#define CMD_MENUS        12
#define CMD_SCREENS      13
#define CMD_REGION_SUM   14
#define CMD_SHOT_REGION  15
#define CMD_GETFILE      16
#define CMD_PUTFILE      17
#define CMD_RELOAD       18
#define CMD_EXEC_T       19
#define CMD_HELLO        20

#define RESP_SCREENSHOT_HDR 0x81
#define RESP_ACK            0x82
#define RESP_EXEC           0x83
#define RESP_POINTER        0x84
#define RESP_TEXT           0x85
#define RESP_FILE           0x86
#define RESP_SUM            0x87

/* Where the installed harness lives; RELOAD applies <BINARY_PATH>.new here,
 * matching what C:nhboot does at boot. */
#define BINARY_PATH    "C:netharness"

#define EXEC_CMD_MAX   512          /* max AmigaDOS command line we accept */
#define EXEC_OUT_FMT   "T:netharness.out.%lu"   /* one file per job */
#define EXEC_DEFAULT_SECS 600       /* plain CMD_EXEC: 10 minutes, then Ctrl-C */
#define EXEC_GRACE_SECS   5         /* after Ctrl-C, how long to wait for it to end */

struct IntuitionBase *IntuitionBase = NULL;
struct GfxBase       *GfxBase       = NULL;
struct Library       *SocketBase    = NULL;
struct Library       *CyberGfxBase  = NULL;   /* opened lazily on RTG capture */

/* Diagnostics go to a file — the harness is normally launched `run >NIL:`,
 * so printf is invisible.  `type T:netharness.log` on the Amiga to read. */
#define NH_LOG_FILE "T:netharness.log"
static void nh_log(const char *msg, LONG num)
{
    BPTR fh = Open((STRPTR)NH_LOG_FILE, MODE_READWRITE);
    if (!fh) return;
    Seek(fh, 0, OFFSET_END);
    FPrintf(fh, (STRPTR)"%s %ld\n", (LONG)msg, num);
    Close(fh);
}

static struct MsgPort  *inputmp;
static struct IOStdReq *inputio;

/* ---- client allowlist ---------------------------------------------------
 * EXEC/PUTFILE/RELOAD/REBOOT give full control of the machine, so only the
 * controllers listed in ENV:NetHarness.allow may connect.  Entries are
 * separated by spaces, commas or newlines: an exact IPv4 address
 * ("192.168.1.10"), a prefix ending in a dot ("192.168.1."), or "*" for
 * anyone.  Loopback is always allowed.  The file is re-read on every connect,
 * so it can be edited live (copy it to ENVARC: to survive a reboot).
 * No file = the pre-1.8 behaviour (anyone), logged as a warning on each
 * connect, so upgrading a machine can never lock its controller out. */
#define ALLOW_VAR  "NetHarness.allow"
#define ALLOW_MAX  512

static BOOL peer_allowed(const UBYTE *ip)
{
    static char list[ALLOW_MAX];
    char dotted[16];
    LONG n;
    char *p;

    if (ip[0] == 127) return TRUE;
    /* %lu + unsigned long casts: this libc's %u reads a 16-bit value, which turned
       172.30.152.173 into "0.172.0.30" and matched no allowlist entry */
    sprintf(dotted, "%lu.%lu.%lu.%lu", (unsigned long)ip[0], (unsigned long)ip[1], (unsigned long)ip[2], (unsigned long)ip[3]);

    n = GetVar((STRPTR)ALLOW_VAR, (STRPTR)list, sizeof(list),
               GVF_GLOBAL_ONLY | GVF_BINARY_VAR);
    if (n < 0) {
        nh_log("WARNING: no ENV:" ALLOW_VAR " - any host may connect", 0);
        return TRUE;
    }
    list[n < (LONG)sizeof(list) ? n : (LONG)sizeof(list) - 1] = 0;

    for (p = list; *p; ) {
        char *tok;
        LONG len;
        while (*p == ' ' || *p == '\t' || *p == ',' || *p == '\n' || *p == '\r') p++;
        tok = p;
        while (*p && *p != ' ' && *p != '\t' && *p != ',' && *p != '\n' && *p != '\r') p++;
        len = (LONG)(p - tok);
        if (len == 0) continue;
        if (len == 1 && tok[0] == '*') return TRUE;
        if (tok[len - 1] == '.') {
            if (strncmp(dotted, tok, len) == 0) return TRUE;       /* prefix */
        } else if ((LONG)strlen(dotted) == len && strncmp(dotted, tok, len) == 0) {
            return TRUE;                                            /* exact */
        }
    }
    return FALSE;
}

static LONG g_client = -1;          /* accepted client socket, -1 = none */
static LONG g_listen = -1;          /* listening socket (RELOAD frees it) */
static WORD g_shutdown = 0;         /* stack is going away: close up and exit */

/* Note: the bsdsocket protos spell the select timeout "struct __timeval", but
 * this NDK maps that straight onto the Amiga "struct timeval" from
 * devices/timer.h (already included above) - do NOT redeclare it. */

/* ---- TCP helpers ------------------------------------------------------- */

/* Wait until `sock` is readable, a break arrives, or ~1s passes.
 * Returns 1 = readable, 0 = nothing yet (caller should loop), -1 = shut down.
 *
 * WHY THIS EXISTS: Roadshow's NetShutdown signals every bsdsocket user with
 * CTRL-C and then WAITS for them to close the library before tearing the stack
 * down. Parked in a blocking accept()/recv() we never noticed the signal, so
 * NetShutdown gave up with "timeout; network may shut down later" and DEFERRED
 * its teardown - which then fired asynchronously and took the machine down with
 * it (reset, and the SCSI bus left wedged so the GVP found no boot drive).
 * That is the Roadie "Go Offline" crash. WaitSelect() blocks on the socket AND
 * the signal, so a shutdown request is now noticed immediately. The 1s timeout
 * is belt-and-braces: even if a stack ignores the signal mask we still return
 * and re-check. */
static WORD wait_readable(LONG sock)
{
    struct timeval tv;
    ULONG readfds, sigs;
    LONG  rc;

    if (sock < 0) return -1;
    if (sock > 31) return 1;        /* outside a one-word fd_set: just try it */

    readfds     = 1UL << sock;      /* fd_set bit n = fd n, LSB first */
    sigs        = SIGBREAKF_CTRL_C;
    tv.tv_secs  = 1;
    tv.tv_micro = 0;

    rc = WaitSelect(sock + 1, &readfds, NULL, NULL, &tv, &sigs);

    if (sigs & SIGBREAKF_CTRL_C) return -1;
    if (rc < 0) {
        /* EINTR or a transient error - only bail out if a break really is set */
        if (CheckSignal(SIGBREAKF_CTRL_C)) return -1;
        return 0;
    }
    if (rc == 0) return 0;          /* timed out */
    return 1;
}

/* send() until all bytes are out (TCP can take partial writes). */
/* Wait until `sock` is writable or ~`secs` pass. Returns 1 = writable,
 * 0 = timed out / shutdown. Companion to wait_readable(): a client that
 * vanished mid-transfer (WiFi drop, killed controller) leaves its socket
 * unwritable forever, and a blocking send() then wedges the ENTIRE harness -
 * hit twice on 2026-08-23 (screenshot into a dead session froze remote
 * control until the connection finally RSTed). */
static WORD wait_writable_s(LONG sock, LONG secs)
{
    struct timeval tv;
    ULONG writefds, sigs;
    LONG  rc, waited = 0;

    if (sock < 0 || sock > 31) return 1;
    while (waited < secs) {
        writefds    = 1UL << sock;
        sigs        = SIGBREAKF_CTRL_C;
        tv.tv_secs  = 1;
        tv.tv_micro = 0;
        rc = WaitSelect(sock + 1, NULL, &writefds, NULL, &tv, &sigs);
        if (sigs & SIGBREAKF_CTRL_C) { g_shutdown = 1; return 0; }
        if (rc > 0) return 1;
        if (rc < 0) { if (CheckSignal(SIGBREAKF_CTRL_C)) { g_shutdown = 1; return 0; } }
        waited++;
    }
    return 0;
}

#define SEND_STALL_SECS 20   /* unwritable this long = client is gone */
#define CLIENT_IDLE_SECS 600 /* silent client dropped after this long */

static BOOL send_all(const UBYTE *buf, LONG len)
{
    while (len > 0) {
        LONG n;
        if (!wait_writable_s(g_client, SEND_STALL_SECS)) return FALSE;
        n = send(g_client, (APTR)buf, len, 0);
        if (n <= 0) return FALSE;
        buf += n;
        len -= n;
    }
    return TRUE;
}

static void send_ack(void)
{
    UBYTE a = RESP_ACK;
    send_all(&a, 1);
}

/* ---- input.device injection (verbatim from A314TestHarness) ------------ */

static BOOL input_open(void)
{
    inputmp = CreateMsgPort();
    if (!inputmp) return FALSE;

    inputio = (struct IOStdReq *)CreateIORequest(inputmp, sizeof(struct IOStdReq));
    if (!inputio) { DeleteMsgPort(inputmp); return FALSE; }

    if (OpenDevice((STRPTR)"input.device", 0, (struct IORequest *)inputio, 0) != 0) {
        DeleteIORequest((struct IORequest *)inputio);
        DeleteMsgPort(inputmp);
        return FALSE;
    }
    return TRUE;
}

static void input_close(void)
{
    CloseDevice((struct IORequest *)inputio);
    DeleteIORequest((struct IORequest *)inputio);
    DeleteMsgPort(inputmp);
}

static void inject_event(struct InputEvent *ie)
{
    inputio->io_Command = IND_WRITEEVENT;
    inputio->io_Data    = (APTR)ie;
    inputio->io_Length  = sizeof(struct InputEvent);
    DoIO((struct IORequest *)inputio);
}

/* Held-button bitmask (bit0=left, bit1=right): every RAWMOUSE event carries
 * the correct ie_Qualifier (pattern proven against remote-mouse.c/hid.c). */
static UBYTE g_mouse_buttons = 0;

static UWORD mouse_qualifier(void)
{
    UWORD q = IEQUALIFIER_RELATIVEMOUSE;
    if (g_mouse_buttons & 1) q |= IEQUALIFIER_LEFTBUTTON;
    if (g_mouse_buttons & 2) q |= IEQUALIFIER_RBUTTON;
    return q;
}

static void do_mouse_move(WORD dx, WORD dy)
{
    struct InputEvent ie;
    memset(&ie, 0, sizeof(ie));
    ie.ie_Class     = IECLASS_RAWMOUSE;
    ie.ie_Code      = IECODE_NOBUTTON;
    ie.ie_Qualifier = mouse_qualifier();
    ie.ie_X         = dx;
    ie.ie_Y         = dy;
    inject_event(&ie);
}

static void do_mouse_button(UBYTE button, UBYTE down)
{
    struct InputEvent ie;
    UWORD code;
    memset(&ie, 0, sizeof(ie));
    switch (button) {
        case 1:  code = IECODE_RBUTTON; g_mouse_buttons = down ? (g_mouse_buttons | 2) : (g_mouse_buttons & ~2); break;
        case 2:  code = IECODE_MBUTTON; break; /* no dedicated qualifier bit for middle */
        default: code = IECODE_LBUTTON; g_mouse_buttons = down ? (g_mouse_buttons | 1) : (g_mouse_buttons & ~1); break;
    }
    if (!down) code |= IECODE_UP_PREFIX;
    ie.ie_Class     = IECLASS_RAWMOUSE;
    ie.ie_Code      = code;
    ie.ie_Qualifier = mouse_qualifier();
    inject_event(&ie);
}

#define RAWKEY_LSHIFT 0x60
#define RAWKEY_RSHIFT 0x61

/* keymap.library reads ie_Qualifier off EACH event - every RAWKEY event must
 * carry the shift state itself (see A314TestHarness lesson #1). */
static UWORD g_key_qualifier = 0;

static void do_key(UBYTE keycode, UBYTE down)
{
    struct InputEvent ie;
    memset(&ie, 0, sizeof(ie));

    {   /* every qualifier key, not only Shift: Alt+key, Ctrl+key and Amiga+key reach the
         * program as combinations (hotkeys, menu shortcuts) */
        UWORD q = 0;
        switch (keycode) {
        case RAWKEY_LSHIFT: q = IEQUALIFIER_LSHIFT; break;
        case RAWKEY_RSHIFT: q = IEQUALIFIER_RSHIFT; break;
        case 0x63: q = IEQUALIFIER_CONTROL; break;
        case 0x64: q = IEQUALIFIER_LALT; break;
        case 0x65: q = IEQUALIFIER_RALT; break;
        case 0x66: q = IEQUALIFIER_LCOMMAND; break;
        case 0x67: q = IEQUALIFIER_RCOMMAND; break;
        }
        if (q) g_key_qualifier = down ? (g_key_qualifier | q) : (g_key_qualifier & ~q);
    }

    ie.ie_Class     = IECLASS_RAWKEY;
    ie.ie_Code      = down ? keycode : (UWORD)(keycode | IECODE_UP_PREFIX);
    ie.ie_Qualifier = g_key_qualifier;
    inject_event(&ie);
}

static void do_home_mouse(void)
{
    /* No "set absolute" event exists, so we walk the pointer into the top-left
     * corner and let Intuition clamp it there.
     *
     * NOT one big -16384 delta (what v1.2 did): with mouse ACCELERATION
     * enabled, Intuition scales the delta, and a value that large overflows
     * the signed 16-bit maths — the pointer wraps and slams into the BOTTOM
     * RIGHT corner instead, after which every MOVETO is wrong and nothing
     * looks broken (found the hard way 2026-08 by toggling Acceleration on
     * during a UI test).  Many modest deltas can't overflow however they are
     * scaled, so this is correct with acceleration on or off. */
    UWORD i;
    for (i = 0; i < 40; i++)
        do_mouse_move(-1000, -1000);
}

static void do_reset_input(void)
{
    if (g_mouse_buttons & 1) do_mouse_button(0, 0);  /* left  up */
    if (g_mouse_buttons & 2) do_mouse_button(1, 0);  /* right up */
    if (g_key_qualifier & IEQUALIFIER_LSHIFT) do_key(RAWKEY_LSHIFT, 0);
    if (g_key_qualifier & IEQUALIFIER_RSHIFT) do_key(RAWKEY_RSHIFT, 0);
    g_mouse_buttons = 0;
    g_key_qualifier = 0;
}

/* ---- screenshot --------------------------------------------------------- */

/* v1.2: universal chunky capture via ReadPixelArray8 + the screen's real
 * palette.  The old raw-bitplane path broke on RTG (zz9000/P96) screens:
 * their BitMap is a chunky fake whose Planes[] aren't 8 valid bitplanes, so
 * streaming bpr*height*depth "planar" bytes shipped garbage (24MB of it on a
 * 1024x768x8 Workbench).  ReadPixelArray8 works on planar AGA AND P96 chunky
 * alike, so this is now the only path.
 *
 * Chunky wire format (host detects via depth byte = 0xFF):
 *   hdr:  0x81 width2 height2 0xFF stride2      (stride = width padded to 16)
 *   then: ncolors2, ncolors*3 palette bytes (8-bit R,G,B per pen)
 *   then: stride*height chunky pen bytes
 * Screens deeper than 8 bpp send hdr depth byte 0xFE and nothing else. */
#define SHOT_BAND_H 32

/* x0/y0/width/height select a REGION of the front screen; the full-screen
 * CMD_SCREENSHOT passes the whole thing.  Grabbing just a status line instead
 * of a 3 MB frame is what makes polling for a redraw affordable. */
static void do_screenshot_region(WORD x0, WORD y0, WORD rw, WORD rh)
{
    struct Screen   *scr;
    struct RastPort *rp;
    struct RastPort  trp;
    struct BitMap   *tmpbm = NULL;
    UBYTE  *chunky = NULL;
    static ULONG pal32[256 * 3];      /* static: keep main()'s stack small */
    static UBYTE pal[2 + 256 * 3];
    UWORD  width, height, stride, ncol;
    ULONG  depth;
    UWORD  y, i;
    UBYTE  hdr[8];

    scr = IntuitionBase->ActiveScreen;
    if (!scr) scr = IntuitionBase->FirstScreen;
    if (!scr) return;

    rp = &scr->RastPort;
    /* clamp the requested region to the screen */
    if (x0 < 0) x0 = 0;
    if (y0 < 0) y0 = 0;
    if (rw <= 0 || rw > scr->Width  - x0) rw = (WORD)(scr->Width  - x0);
    if (rh <= 0 || rh > scr->Height - y0) rh = (WORD)(scr->Height - y0);
    if (rw <= 0 || rh <= 0) return;

    width  = (UWORD)rw;
    height = (UWORD)rh;
    depth  = GetBitMapAttr(rp->BitMap, BMA_DEPTH);
    stride = (UWORD)((width + 15) & ~15);   /* ReadPixelArray8 row padding */

    hdr[0] = RESP_SCREENSHOT_HDR;
    hdr[1] = (UBYTE)(width  >> 8); hdr[2] = (UBYTE)width;
    hdr[3] = (UBYTE)(height >> 8); hdr[4] = (UBYTE)height;

    if (depth > 8) {
        /* True-colour RTG screen (Picasso96/zz9000 Workbench on this A4000):
         * grab via cybergraphics.library ReadPixelArray in RECTFMT_RGB.
         * Wire: depth byte 0xFD, then width*height*3 raw RGB bytes, no
         * palette and no row padding. */
        UBYTE *rgb;
        UWORD  y;
        if (!CyberGfxBase)
            CyberGfxBase = OpenLibrary((STRPTR)"cybergraphics.library", 40);
        if (!CyberGfxBase) {
            hdr[5] = 0xFE; hdr[6] = 0; hdr[7] = 0;   /* unsupported */
            send_all(hdr, sizeof(hdr));
            return;
        }
        hdr[5] = 0xFD; hdr[6] = 0; hdr[7] = 0;
        rgb = AllocVec((ULONG)width * 3 * SHOT_BAND_H, MEMF_PUBLIC);
        if (!rgb) {
            hdr[5] = 0xFE;
            send_all(hdr, sizeof(hdr));
            return;
        }
        if (send_all(hdr, sizeof(hdr))) {
            for (y = 0; y < height; y += SHOT_BAND_H) {
                UWORD bh = (UWORD)((height - y > SHOT_BAND_H) ? SHOT_BAND_H : height - y);
                ReadPixelArray(rgb, 0, 0, (UWORD)(width * 3),
                               rp, x0, y0 + y, width, bh, RECTFMT_RGB);
                if (!send_all(rgb, (ULONG)width * 3 * bh)) break;
            }
        }
        FreeVec(rgb);
        return;
    }

    hdr[5] = 0xFF;
    hdr[6] = (UBYTE)(stride >> 8); hdr[7] = (UBYTE)stride;

    chunky = AllocVec((ULONG)stride * SHOT_BAND_H, MEMF_PUBLIC);
    tmpbm  = AllocBitMap(stride, SHOT_BAND_H, depth, 0, rp->BitMap);
    if (!chunky || !tmpbm) goto out;
    trp = *rp;
    trp.Layer  = NULL;
    trp.BitMap = tmpbm;

    ncol = (UWORD)(1UL << depth);
    GetRGB32(scr->ViewPort.ColorMap, 0, ncol, pal32);
    pal[0] = (UBYTE)(ncol >> 8); pal[1] = (UBYTE)ncol;
    for (i = 0; i < ncol; i++) {
        pal[2 + i * 3 + 0] = (UBYTE)(pal32[i * 3 + 0] >> 24);
        pal[2 + i * 3 + 1] = (UBYTE)(pal32[i * 3 + 1] >> 24);
        pal[2 + i * 3 + 2] = (UBYTE)(pal32[i * 3 + 2] >> 24);
    }

    if (!send_all(hdr, sizeof(hdr))) goto out;
    if (!send_all(pal, 2 + (ULONG)ncol * 3)) goto out;

    for (y = 0; y < height; y += SHOT_BAND_H) {
        UWORD bh = (UWORD)((height - y > SHOT_BAND_H) ? SHOT_BAND_H : height - y);
        ReadPixelArray8(rp, x0, y0 + y, x0 + width - 1, y0 + y + bh - 1, chunky, &trp);
        if (!send_all(chunky, (ULONG)stride * bh)) break;
    }

out:
    if (tmpbm)  FreeBitMap(tmpbm);
    if (chunky) FreeVec(chunky);
}

/* ---- EXEC ---------------------------------------------------------------- */

/* v1.10: the command runs in a worker process and we wait for it with a time
 * limit.  Before, do_exec() called SystemTags() itself, so a command that
 * hung (a socket wait, a lost requester, a deadlocked test) wedged the whole
 * harness: no PING, no BREAK, and on a machine without an A314 the only way
 * back was a human at the keyboard (three times on 2026-09-30).
 *
 * On timeout the command's shell process gets Ctrl-C (it is found by the name
 * we give it, NP_Name "nh_exec <n>"), and after EXEC_GRACE_SECS we answer
 * either way: rc -2 and a note after whatever output it wrote.  A command that
 * ignores Ctrl-C is ABANDONED: the worker frees its own job when it finally
 * ends, and its output file (one per job) stays behind in T:. */
struct ExecJob {
    struct Task  *parent;
    LONG          sig;
    volatile LONG rc;
    volatile UBYTE done, abandoned;
    char          cmd[EXEC_CMD_MAX + 1];
    char          out[32];
    char          name[24];
};

static ULONG g_jobno = 0;

static void exec_worker(void)
{
    struct ExecJob *j = (struct ExecJob *)FindTask(NULL)->tc_UserData;
    BPTR in, out;
    LONG rc = -1;

    in  = Open((STRPTR)"NIL:", MODE_OLDFILE);
    out = Open((STRPTR)j->out, MODE_NEWFILE);
    if (out) {
        rc = SystemTags((STRPTR)j->cmd,
                        SYS_Input,    (Tag)in,
                        SYS_Output,   (Tag)out,
                        NP_Name,      (Tag)j->name,
                        NP_Priority,  (Tag)0,
                        NP_WindowPtr, (Tag)-1L,   /* no DOS requesters - see main() */
                        TAG_DONE);
        Close(out);
    }
    if (in) Close(in);

    Forbid();
    j->rc = rc;
    j->done = 1;
    if (j->abandoned) FreeVec(j);             /* nobody is waiting any more */
    else Signal(j->parent, 1UL << j->sig);
    /* return while still Forbid()den: this process is gone before the harness
       could exit and unload the code we are running */
}

static void do_exec(const UBYTE *cmd, UWORD len, UWORD secs)
{
    struct ExecJob *j;
    struct Process *proc = NULL;
    BPTR  rd = 0;
    LONG  rc = -1, outlen = 0, sig, waited, limit;
    BOOL  timedout = FALSE, abandoned = FALSE, broke = FALSE;
    char  note[160];
    UBYTE hdr[9];
    UBYTE iobuf[1024];

    note[0] = 0;
    if (len > EXEC_CMD_MAX) len = EXEC_CMD_MAX;
    j = (struct ExecJob *)AllocVec(sizeof(*j), MEMF_PUBLIC | MEMF_CLEAR);
    sig = j ? AllocSignal(-1) : -1;
    if (j && sig >= 0) {
        memcpy(j->cmd, cmd, len);
        j->cmd[len] = 0;
        j->parent = FindTask(NULL);
        j->sig = sig;
        g_jobno++;
        sprintf(j->out, EXEC_OUT_FMT, (unsigned long)g_jobno);
        sprintf(j->name, "nh_exec %lu", (unsigned long)g_jobno);
        SetSignal(0, 1UL << sig);
        Forbid();                             /* it can't run before it has its job */
        proc = CreateNewProcTags(NP_Entry,     (Tag)exec_worker,
                                 NP_Name,      (Tag)"netharness exec",
                                 NP_StackSize, (Tag)8192,
                                 NP_Priority,  (Tag)0,
                                 /* a CLI of its own: it copies OUR command path and
                                    current dir, which System() then hands on - without
                                    it the command saw only C: ("rx: Unknown command") */
                                 NP_Cli,       (Tag)TRUE,
                                 NP_CommandName, (Tag)"netharness_exec",   /* not "C:netharness" in Status */
                                 TAG_DONE);
        if (proc) proc->pr_Task.tc_UserData = (APTR)j;
        Permit();
    }
    if (!proc) {
        if (sig >= 0) FreeSignal(sig);
        if (j) FreeVec(j);
        sprintf(note, "netharness: could not start the command\n");
        goto reply;
    }

    /* wait: the job's signal, polled with Delay so the time limit needs no timer */
    limit = secs ? (LONG)secs * 50 : 0x7fffffffL;
    for (waited = 0; !j->done && waited < limit; waited += 5) Delay(5);
    if (!j->done) {
        struct Task *t;
        timedout = TRUE;
        Forbid();
        t = FindTask((STRPTR)j->name);
        if (t) { Signal(t, SIGBREAKF_CTRL_C); broke = TRUE; }
        Permit();
        for (waited = 0; !j->done && waited < EXEC_GRACE_SECS * 50; waited += 5) Delay(5);
    }
    Forbid();
    if (!j->done) { j->abandoned = 1; abandoned = TRUE; }
    Permit();

    if (abandoned) {
        rc = -2;
        sprintf(note, "\n*** netharness: timed out after %lu s; %s - still running as \"%s\", output in %s\n",
                (unsigned long)secs, broke ? "Ctrl-C did not stop it" : "its process was not found to break",
                j->name, j->out);
        FreeSignal(sig);                      /* the worker frees the job itself */
        goto reply;
    }
    rc = timedout ? -2 : j->rc;
    if (timedout)
        sprintf(note, "\n*** netharness: timed out after %lu s; stopped with Ctrl-C (rc %ld)\n",
                (unsigned long)secs, (long)j->rc);
    rd = Open((STRPTR)j->out, MODE_OLDFILE);
    if (rd) {
        Seek(rd, 0, OFFSET_END);
        outlen = Seek(rd, 0, OFFSET_BEGINNING);
        if (outlen < 0) outlen = 0;
    }
    FreeSignal(sig);

reply:
    {
        LONG notelen = (LONG)strlen(note), total = outlen + notelen;
        hdr[0] = RESP_EXEC;
        hdr[1] = (UBYTE)(rc >> 24); hdr[2] = (UBYTE)(rc >> 16);
        hdr[3] = (UBYTE)(rc >>  8); hdr[4] = (UBYTE)rc;
        hdr[5] = (UBYTE)(total >> 24); hdr[6] = (UBYTE)(total >> 16);
        hdr[7] = (UBYTE)(total >>  8); hdr[8] = (UBYTE)total;
        if (send_all(hdr, sizeof(hdr))) {
            LONG left = outlen;
            while (rd && left > 0) {
                LONG want = (left > (LONG)sizeof(iobuf)) ? (LONG)sizeof(iobuf) : left;
                LONG n = Read(rd, iobuf, want);
                if (n <= 0) break;
                if (!send_all(iobuf, n)) { left = -1; break; }
                left -= n;
            }
            while (left > 0) {                /* short file: pad so the length holds */
                LONG n = left > (LONG)sizeof(iobuf) ? (LONG)sizeof(iobuf) : left;
                memset(iobuf, ' ', n);
                if (!send_all(iobuf, n)) break;
                left -= n;
            }
            if (notelen && left >= 0) send_all((UBYTE *)note, notelen);
        }
    }
    if (rd) {
        Close(rd);
        DeleteFile((STRPTR)j->out);
    }
    if (proc && !abandoned) FreeVec(j);
}

/* v1.10: HELLO -> RESP_TEXT "netharness <version>" */
static void do_hello(void)
{
    static const char txt[] = "netharness " NH_VERSION;
    UBYTE h[5];
    ULONG n = sizeof(txt) - 1;
    h[0] = RESP_TEXT;
    h[1] = (UBYTE)(n >> 24); h[2] = (UBYTE)(n >> 16); h[3] = (UBYTE)(n >> 8); h[4] = (UBYTE)n;
    if (send_all(h, 5)) send_all((UBYTE *)txt, n);
}

/* ---- v1.3: pointer readback, semantic UI tree, regions, file transfer ----- */

static struct Screen *front_screen(void)
{
    struct Screen *scr = IntuitionBase->ActiveScreen;
    if (!scr) scr = IntuitionBase->FirstScreen;
    return scr;
}

/* Where IS the pointer?  The sprite never shows up in a screenshot, so without
 * this the controller is flying blind after every move. */
static void do_pointer(void)
{
    struct Screen *scr = front_screen();
    UBYTE r[9];
    WORD x = 0, y = 0, w = 0, h = 0;
    if (scr) { x = scr->MouseX; y = scr->MouseY; w = scr->Width; h = scr->Height; }
    r[0] = RESP_POINTER;
    r[1] = (UBYTE)(x >> 8); r[2] = (UBYTE)x;
    r[3] = (UBYTE)(y >> 8); r[4] = (UBYTE)y;
    r[5] = (UBYTE)(w >> 8); r[6] = (UBYTE)w;
    r[7] = (UBYTE)(h >> 8); r[8] = (UBYTE)h;
    send_all(r, sizeof(r));
}

/* ---- text-response helper (UITREE / MENUS / SCREENS) --------------------- */

#define TEXTBUF_SIZE 16384
static char  g_text[TEXTBUF_SIZE];
static LONG  g_textlen;

static void tclear(void) { g_textlen = 0; }

static void tputs(const char *s)
{
    while (*s && g_textlen < TEXTBUF_SIZE - 1) g_text[g_textlen++] = *s++;
}

static void tputnum(LONG v)
{
    char tmp[12]; int i = 0, j;
    if (v < 0) { if (g_textlen < TEXTBUF_SIZE - 1) g_text[g_textlen++] = '-'; v = -v; }
    if (v == 0) tmp[i++] = '0';
    while (v > 0) { tmp[i++] = (char)('0' + (v % 10)); v /= 10; }
    for (j = i - 1; j >= 0 && g_textlen < TEXTBUF_SIZE - 1; j--) g_text[g_textlen++] = tmp[j];
}

/* quoted, with embedded quotes/newlines neutralised so the host can parse
 * one record per line safely */
static void tputq(const char *s)
{
    if (g_textlen < TEXTBUF_SIZE - 1) g_text[g_textlen++] = '"';
    if (s) {
        while (*s && g_textlen < TEXTBUF_SIZE - 2) {
            char c = *s++;
            if (c == '"' || c == '\\') c = '\'';
            else if (c == '\n' || c == '\r' || c == '\t') c = ' ';
            g_text[g_textlen++] = c;
        }
    }
    if (g_textlen < TEXTBUF_SIZE - 1) g_text[g_textlen++] = '"';
}

static void send_text(void)
{
    UBYTE hdr[5];
    hdr[0] = RESP_TEXT;
    hdr[1] = (UBYTE)(g_textlen >> 24); hdr[2] = (UBYTE)(g_textlen >> 16);
    hdr[3] = (UBYTE)(g_textlen >>  8); hdr[4] = (UBYTE)g_textlen;
    if (send_all(hdr, sizeof(hdr)))
        send_all((const UBYTE *)g_text, g_textlen);
}

/* ---- semantic UI tree ----------------------------------------------------
 * Emitted as one record per line so the host can parse it without a parser:
 *   S <w> <h> <depth> "<screen title>"
 *   W <idx> <left> <top> <w> <h> "<window title>"
 *   G <id> <KIND> <left> <top> <w> <h> "<label>" "<contents>"
 * Gadget coords are SCREEN-absolute (window origin added, REL* flags resolved)
 * so the host can click them directly. */

static const char *gadget_kind(struct Gadget *g)
{
    switch (g->GadgetType & GTYP_GTYPEMASK) {
        case GTYP_BOOLGADGET:   return "BOOL";
        case GTYP_STRGADGET:    return "STRING";
        case GTYP_PROPGADGET:   return "PROP";
        case GTYP_CUSTOMGADGET: return "CUSTOM";
        default:                return "GADGET";
    }
}

/* Append one raw C string to the quoted label being built. */
static void tput_label_chars(const char *s)
{
    if (!s) return;
    while (*s && g_textlen < TEXTBUF_SIZE - 2) {
        char c = *s++;
        if (c == '"' || c == '\\') c = '\'';
        else if (c == '\n' || c == '\r' || c == '\t') c = ' ';
        g_text[g_textlen++] = c;
    }
}

/* A gadget's label.  gg_GadgetText is NOT always an IntuiText: GFLG_LABELMASK
 * says which of three things it points at.  Reading a GFLG_LABELSTRING gadget
 * as an IntuiText yields an empty (or garbage) label — that's why Time
 * Prefs' Save/Use/Cancel buttons came back "" before this was handled. */
static void tput_gadget_label(struct Gadget *g)
{
    UWORD kind = (UWORD)(g->Flags & GFLG_LABELMASK);

    if (g_textlen < TEXTBUF_SIZE - 1) g_text[g_textlen++] = '"';

    if (kind == GFLG_LABELSTRING) {
        tput_label_chars((const char *)g->GadgetText);
    } else if (kind == GFLG_LABELITEXT) {
        struct IntuiText *it = (struct IntuiText *)g->GadgetText;
        while (it && g_textlen < TEXTBUF_SIZE - 2) {
            tput_label_chars((const char *)it->IText);
            it = it->NextText;
            if (it && g_textlen < TEXTBUF_SIZE - 2) g_text[g_textlen++] = ' ';
        }
    }
    /* GFLG_LABELIMAGE: an Image, no text to report — leave it empty. */

    if (g_textlen < TEXTBUF_SIZE - 1) g_text[g_textlen++] = '"';
}

static void do_uitree(void)
{
    struct Screen *scr = front_screen();
    struct Window *win;
    WORD widx = 0;

    tclear();
    if (!scr) { tputs("! no screen\n"); send_text(); return; }

    tputs("S "); tputnum(scr->Width); tputs(" "); tputnum(scr->Height);
    tputs(" "); tputnum((LONG)GetBitMapAttr(scr->RastPort.BitMap, BMA_DEPTH));
    tputs(" "); tputq((const char *)scr->Title); tputs("\n");

    for (win = scr->FirstWindow; win; win = win->NextWindow, widx++) {
        struct Gadget *g;
        tputs("W "); tputnum(widx);
        tputs(" "); tputnum(win->LeftEdge); tputs(" "); tputnum(win->TopEdge);
        tputs(" "); tputnum(win->Width);    tputs(" "); tputnum(win->Height);
        tputs(" "); tputq((const char *)win->Title); tputs("\n");

        for (g = win->FirstGadget; g; g = g->NextGadget) {
            /* resolve the REL* flags against the window's current size */
            WORD gl = g->LeftEdge, gt = g->TopEdge, gw = g->Width, gh = g->Height;
            if (g->Flags & GFLG_RELRIGHT)  gl += win->Width  - 1;
            if (g->Flags & GFLG_RELBOTTOM) gt += win->Height - 1;
            if (g->Flags & GFLG_RELWIDTH)  gw += win->Width;
            if (g->Flags & GFLG_RELHEIGHT) gh += win->Height;

            tputs("G "); tputnum(g->GadgetID);
            tputs(" "); tputs(gadget_kind(g));
            tputs(" "); tputnum(win->LeftEdge + gl);
            tputs(" "); tputnum(win->TopEdge  + gt);
            tputs(" "); tputnum(gw); tputs(" "); tputnum(gh);
            tputs(" "); tput_gadget_label(g);
            tputs(" ");
            if ((g->GadgetType & GTYP_GTYPEMASK) == GTYP_STRGADGET && g->SpecialInfo)
                tputq((const char *)((struct StringInfo *)g->SpecialInfo)->Buffer);
            else
                tputq("");
            tputs("\n");
        }
    }
    send_text();
}

/* Menu strip of the frontmost window that has one:
 *   M <menunum> "<menu title>"
 *   I <menunum> <itemnum> "<item text>" "<shortcut>"  */
static void do_menus(void)
{
    struct Screen *scr = front_screen();
    struct Window *win;
    struct Menu   *menu = NULL;
    WORD mnum = 0;

    tclear();
    if (scr)
        for (win = scr->FirstWindow; win; win = win->NextWindow)
            if (win->MenuStrip) { menu = win->MenuStrip; break; }

    if (!menu) { tputs("! no menu strip\n"); send_text(); return; }

    for (; menu; menu = menu->NextMenu, mnum++) {
        struct MenuItem *item;
        WORD inum = 0;
        tputs("M "); tputnum(mnum); tputs(" ");
        tputq((const char *)menu->MenuName); tputs("\n");
        for (item = menu->FirstItem; item; item = item->NextItem, inum++) {
            char sc[2];
            tputs("I "); tputnum(mnum); tputs(" "); tputnum(inum); tputs(" ");
            /* text items carry an IntuiText in ItemFill */
            if (!(item->Flags & ITEMTEXT) || !item->ItemFill) tputq("");
            else tputq((const char *)((struct IntuiText *)item->ItemFill)->IText);
            tputs(" ");
            if (item->Flags & COMMSEQ) { sc[0] = item->Command; sc[1] = 0; tputq(sc); }
            else tputq("");
            tputs("\n");
        }
    }
    send_text();
}

/* Open screens, front to back:  SC <idx> <w> <h> <depth> "<title>" */
static void do_screens(void)
{
    struct Screen *s;
    WORD i = 0;
    tclear();
    for (s = IntuitionBase->FirstScreen; s; s = s->NextScreen, i++) {
        tputs("SC "); tputnum(i);
        tputs(" "); tputnum(s->Width); tputs(" "); tputnum(s->Height);
        tputs(" "); tputnum((LONG)GetBitMapAttr(s->RastPort.BitMap, BMA_DEPTH));
        tputs(" "); tputq((const char *)s->Title); tputs("\n");
    }
    send_text();
}

/* ---- region checksum -----------------------------------------------------
 * "Has it finished redrawing?" in four bytes, instead of hauling a whole
 * frame across the wire (or guessing a fixed settle delay). */
static void do_region_sum(WORD x, WORD y, WORD w, WORD h)
{
    struct Screen *scr = front_screen();
    UBYTE  r[5];
    ULONG  sum = 2166136261UL;             /* FNV-1a seed */
    UBYTE *row = NULL;
    struct RastPort trp;
    struct BitMap  *tmpbm = NULL;

    if (scr && w > 0 && h > 0) {
        ULONG depth  = GetBitMapAttr(scr->RastPort.BitMap, BMA_DEPTH);
        UWORD stride = (UWORD)((w + 15) & ~15);
        if (depth > 8) {
            /* truecolour: checksum RGB rows straight from cybergraphics */
            if (!CyberGfxBase)
                CyberGfxBase = OpenLibrary((STRPTR)"cybergraphics.library", 40);
            if (CyberGfxBase) {
                row = AllocVec((ULONG)w * 3, MEMF_PUBLIC);
                if (row) {
                    WORD yy; ULONG i;
                    for (yy = 0; yy < h; yy++) {
                        ReadPixelArray(row, 0, 0, (UWORD)(w * 3),
                                       &scr->RastPort, x, y + yy, w, 1, RECTFMT_RGB);
                        for (i = 0; i < (ULONG)w * 3; i++)
                            { sum ^= row[i]; sum *= 16777619UL; }
                    }
                    FreeVec(row);
                }
            }
        } else {
            row   = AllocVec((ULONG)stride, MEMF_PUBLIC);
            tmpbm = AllocBitMap(stride, 1, depth, 0, scr->RastPort.BitMap);
            if (row && tmpbm) {
                WORD yy; ULONG i;
                trp = scr->RastPort; trp.Layer = NULL; trp.BitMap = tmpbm;
                for (yy = 0; yy < h; yy++) {
                    ReadPixelArray8(&scr->RastPort, x, y + yy, x + w - 1, y + yy, row, &trp);
                    for (i = 0; i < (ULONG)w; i++)
                        { sum ^= row[i]; sum *= 16777619UL; }
                }
            }
            if (tmpbm) FreeBitMap(tmpbm);
            if (row)   FreeVec(row);
        }
    }
    r[0] = RESP_SUM;
    r[1] = (UBYTE)(sum >> 24); r[2] = (UBYTE)(sum >> 16);
    r[3] = (UBYTE)(sum >>  8); r[4] = (UBYTE)sum;
    send_all(r, sizeof(r));
}

/* ---- file transfer -------------------------------------------------------
 * Lets the controller deploy a freshly built binary straight onto the Amiga
 * with no file share in the middle. */
static void send_file_status(LONG status)
{
    UBYTE hdr[9];
    hdr[0] = RESP_FILE;
    hdr[1] = (UBYTE)(status >> 24); hdr[2] = (UBYTE)(status >> 16);
    hdr[3] = (UBYTE)(status >>  8); hdr[4] = (UBYTE)status;
    hdr[5] = hdr[6] = hdr[7] = hdr[8] = 0;
    send_all(hdr, sizeof(hdr));
}

static void do_getfile(const UBYTE *p, UWORD plen)
{
    char  path[256];
    BPTR  fh;
    LONG  len;
    UBYTE hdr[9], iobuf[1024];

    if (plen > 255) plen = 255;
    memcpy(path, p, plen); path[plen] = 0;

    fh = Open((STRPTR)path, MODE_OLDFILE);
    if (!fh) { send_file_status(-1); return; }
    Seek(fh, 0, OFFSET_END);
    len = Seek(fh, 0, OFFSET_BEGINNING);
    if (len < 0) len = 0;

    hdr[0] = RESP_FILE;
    hdr[1] = hdr[2] = hdr[3] = hdr[4] = 0;          /* status 0 = ok */
    hdr[5] = (UBYTE)(len >> 24); hdr[6] = (UBYTE)(len >> 16);
    hdr[7] = (UBYTE)(len >>  8); hdr[8] = (UBYTE)len;
    if (send_all(hdr, sizeof(hdr))) {
        LONG left = len;
        while (left > 0) {
            LONG want = (left > (LONG)sizeof(iobuf)) ? (LONG)sizeof(iobuf) : left;
            LONG n = Read(fh, iobuf, want);
            if (n <= 0) break;
            if (!send_all(iobuf, n)) break;
            left -= n;
        }
    }
    Close(fh);
}

/* A file is far bigger than the 1 KB command buffer, so PUTFILE STREAMS: the
 * caller hands over whatever data already arrived in the buffer, and we pull
 * the remainder straight off the socket. */
static void do_putfile(const UBYTE *p, UWORD plen,
                       const UBYTE *have, ULONG havelen, ULONG dlen)
{
    char  path[256];
    BPTR  fh;
    ULONG left;
    UBYTE iobuf[1024];
    BOOL  ok = TRUE;

    if (plen > 255) plen = 255;
    memcpy(path, p, plen); path[plen] = 0;

    fh = Open((STRPTR)path, MODE_NEWFILE);
    if (!fh) {
        /* still have to drain the payload or the stream desyncs */
        left = dlen - havelen;
        while (left > 0) {
            LONG want = (left > sizeof(iobuf)) ? (LONG)sizeof(iobuf) : (LONG)left;
            LONG n = recv(g_client, (APTR)iobuf, want, 0);
            if (n <= 0) break;
            left -= n;
        }
        send_file_status(-1);
        return;
    }

    if (havelen && Write(fh, (APTR)have, (LONG)havelen) != (LONG)havelen) ok = FALSE;

    left = dlen - havelen;
    while (left > 0) {
        LONG want = (left > sizeof(iobuf)) ? (LONG)sizeof(iobuf) : (LONG)left;
        LONG n = recv(g_client, (APTR)iobuf, want, 0);
        if (n <= 0) { ok = FALSE; break; }
        if (Write(fh, iobuf, n) != n) { ok = FALSE; break; }
        left -= n;
    }
    Close(fh);
    send_file_status(ok ? 0 : -2);
}

/* ---- flush filesystems before a reset --------------------------------------
 * ColdReboot() resets the machine INSTANTLY.  Any data still sitting in a
 * filesystem's dirty buffers is lost - which silently threw away every file
 * written shortly before a REBOOT (a library copied into LIBS: reverted to its
 * previous contents after the reset, costing a long debugging detour on
 * 2026-07-25).  Send ACTION_FLUSH to every mounted filesystem handler first.
 *
 * The handler ports are collected under the DosList lock and the packets sent
 * AFTER unlocking: DoPkt() waits for a reply, and blocking while holding the
 * DosList lock can deadlock against the handler itself. */
#define ACTION_FLUSH 27
#define MAX_FLUSH_PORTS 24

static void flush_all_filesystems(void)
{
    struct MsgPort *ports[MAX_FLUSH_PORTS];
    UWORD n = 0, i;
    struct DosList *dl;

    dl = LockDosList(LDF_DEVICES | LDF_READ);
    if (dl) {
        while ((dl = NextDosEntry(dl, LDF_DEVICES | LDF_READ)) != NULL) {
            if (dl->dol_Task && n < MAX_FLUSH_PORTS)
                ports[n++] = dl->dol_Task;
        }
        UnLockDosList(LDF_DEVICES | LDF_READ);
    }
    for (i = 0; i < n; i++)
        DoPkt(ports[i], ACTION_FLUSH, 0, 0, 0, 0, 0);
    Delay(25);   /* 0.5s: let drivers push their own write caches out */
}

/* ---- RELOAD: restart the harness in place, WITHOUT rebooting the machine ---
 * `REBOOT` calls ColdReboot(), and some machines do not survive a warm CPU
 * reset: the A2000 here lands on a grey screen and needs a power cycle (a
 * classic symptom on boxes whose accelerator/SCSI controller does not
 * reinitialise on reset). Rebooting merely to swap a binary was always
 * heavy-handed anyway, so RELOAD does the same job in place:
 *   1. apply a staged C:netharness.new (exactly what C:nhboot does at boot)
 *      WHILE STILL SERVING, so a failed copy leaves us alive to report it,
 *   2. free the listening port,
 *   3. launch the replacement and exit.
 * The incoming instance retries bind for 60s, which covers the handover, and
 * SO_REUSEADDR is already set. */

static BOOL g_reload = FALSE;
static void release_single(void);           /* v1.10, below with the port setup */

static BOOL file_exists(const char *path)
{
    BPTR l = Lock((STRPTR)path, ACCESS_READ);
    if (!l) return FALSE;
    UnLock(l);
    return TRUE;
}

/* Run a DOS command synchronously with output discarded; returns its rc. */
static LONG run_quiet(const char *cmd)
{
    BPTR in, out;
    LONG rc;
    in  = Open((STRPTR)"NIL:", MODE_OLDFILE);
    out = Open((STRPTR)"NIL:", MODE_NEWFILE);
    rc = SystemTags((STRPTR)cmd,
                    SYS_Input,    (Tag)in,
                    SYS_Output,   (Tag)out,
                    NP_WindowPtr, (Tag)-1L,
                    TAG_DONE);
    /* this dos.library does not close SYS_Input/SYS_Output for us */
    if (out) Close(out);
    if (in)  Close(in);
    return rc;
}

static void do_reload(void)
{
    UBYTE hdr[2];

    /* 1. apply a staged update while we are still able to answer. */
    if (file_exists(BINARY_PATH ".new")) {
        if (run_quiet("C:Copy " BINARY_PATH ".new " BINARY_PATH " CLONE") != 0) {
            nh_log("reload: copy of .new FAILED, staying on the old build", 0);
            hdr[0] = RESP_ACK; hdr[1] = 0;      /* 0 = refused, still running */
            send_all(hdr, 2);
            return;
        }
        run_quiet("C:Delete " BINARY_PATH ".new QUIET");
        run_quiet("C:Protect " BINARY_PATH " +e");
        nh_log("reload: applied staged update", 0);
    }

    hdr[0] = RESP_ACK; hdr[1] = 1;              /* 1 = reloading now */
    send_all(hdr, 2);

    /* 2. free the port before the replacement tries to bind. */
    if (g_client >= 0) { CloseSocket(g_client); g_client = -1; }
    if (g_listen >= 0) { CloseSocket(g_listen); g_listen = -1; }

    /* 3. hand off. `run` returns immediately, so this does not block us.
       The single-instance port goes first, or the replacement would exit. */
    release_single();
    run_quiet("run >NIL: " BINARY_PATH);
    nh_log("reload: handed off, exiting", 0);
    g_reload = TRUE;
}

/* ---- command stream reassembly -------------------------------------------
 * TCP is a byte stream: commands can arrive split across, or several per,
 * recv().  Accumulate and parse out complete commands - same pattern (and
 * same hard-won reason) as the A314 harness. */

#define CMDBUF_SIZE 1024
static UBYTE cmdbuf[CMDBUF_SIZE];
static WORD  cmdbuf_len = 0;

/* Returns bytes consumed for one complete command, 0 if incomplete, or -1 for
 * a command that can never fit in cmdbuf.  Lengths are checked as LONG: a
 * 16-bit length near 65535 cast to WORD goes negative, which used to pass the
 * "complete?" test and walk the parser backwards out of cmdbuf.  An oversized
 * command drops the client - its payload would otherwise be parsed as
 * commands once it arrived. */
static WORD dispatch_one(const UBYTE *b, WORD avail)
{
    switch (b[0]) {
        case CMD_MOUSE_MOVE:
            if (avail < 5) return 0;
            {
                WORD dx = (WORD)((b[1] << 8) | b[2]);
                WORD dy = (WORD)((b[3] << 8) | b[4]);
                do_mouse_move(dx, dy);
            }
            send_ack();
            return 5;
        case CMD_MOUSE_BUTTON:
            if (avail < 3) return 0;
            do_mouse_button(b[1], b[2]);
            send_ack();
            return 3;
        case CMD_KEY:
            if (avail < 3) return 0;
            do_key(b[1], b[2]);
            send_ack();
            return 3;
        case CMD_HOME_MOUSE:
            do_home_mouse();
            send_ack();
            return 1;
        case CMD_RESET_INPUT:
            do_reset_input();
            send_ack();
            return 1;
        case CMD_SCREENSHOT:
            do_screenshot_region(0, 0, 0, 0);   /* 0 w/h = whole screen */
            return 1;
        case CMD_SHOT_REGION:
            if (avail < 9) return 0;
            do_screenshot_region((WORD)((b[1] << 8) | b[2]), (WORD)((b[3] << 8) | b[4]),
                                 (WORD)((b[5] << 8) | b[6]), (WORD)((b[7] << 8) | b[8]));
            return 9;
        case CMD_POINTER:
            do_pointer();
            return 1;
        case CMD_UITREE:
            do_uitree();
            return 1;
        case CMD_MENUS:
            do_menus();
            return 1;
        case CMD_SCREENS:
            do_screens();
            return 1;
        case CMD_REGION_SUM:
            if (avail < 9) return 0;
            do_region_sum((WORD)((b[1] << 8) | b[2]), (WORD)((b[3] << 8) | b[4]),
                          (WORD)((b[5] << 8) | b[6]), (WORD)((b[7] << 8) | b[8]));
            return 9;
        case CMD_GETFILE:
            if (avail < 3) return 0;
            {
                UWORD plen = (UWORD)((b[1] << 8) | b[2]);
                if (3L + plen > CMDBUF_SIZE) return -1;
                if ((LONG)avail < 3L + plen) return 0;
                do_getfile(b + 3, plen);
                return (WORD)(3 + plen);
            }
        case CMD_PUTFILE:
            if (avail < 3) return 0;
            {
                UWORD plen = (UWORD)((b[1] << 8) | b[2]);
                ULONG dlen, have;
                WORD  hdrlen;
                if (3L + plen + 4 > CMDBUF_SIZE) return -1;
                if ((LONG)avail < 3L + plen + 4) return 0;
                hdrlen = (WORD)(3 + plen + 4);
                dlen = ((ULONG)b[3 + plen] << 24) | ((ULONG)b[4 + plen] << 16) |
                       ((ULONG)b[5 + plen] << 8)  |  (ULONG)b[6 + plen];
                /* hand over what's buffered; do_putfile pulls the rest itself */
                have = (ULONG)(avail - hdrlen);
                if (have > dlen) have = dlen;
                do_putfile(b + 3, plen, b + hdrlen, have, dlen);
                return (WORD)(hdrlen + have);
            }
        case CMD_REBOOT:
            flush_all_filesystems();   /* MUST precede ColdReboot - see below */
            ColdReboot();
            return 1;   /* unreachable */
        case CMD_EXEC:
            if (avail < 3) return 0;
            {
                UWORD clen = (UWORD)((b[1] << 8) | b[2]);
                if (3L + clen > CMDBUF_SIZE) return -1;
                if ((LONG)avail < 3L + clen) return 0;
                do_exec(b + 3, clen, EXEC_DEFAULT_SECS);
                return (WORD)(3 + clen);
            }
        case CMD_EXEC_T:
            if (avail < 5) return 0;
            {
                UWORD secs = (UWORD)((b[1] << 8) | b[2]);
                UWORD clen = (UWORD)((b[3] << 8) | b[4]);
                if (5L + clen > CMDBUF_SIZE) return -1;
                if ((LONG)avail < 5L + clen) return 0;
                do_exec(b + 5, clen, secs);
                return (WORD)(5 + clen);
            }
        case CMD_HELLO:
            do_hello();
            return 1;
        case CMD_PING:
            send_ack();
            return 1;
        case CMD_RELOAD:
            do_reload();
            return 1;
        default:
            /* Unknown byte: drop and resync rather than jam the stream. */
            return 1;
    }
}

/* ---- TCP server bring-up -------------------------------------------------
 * Retried from scratch each round: on a cold boot this may run before
 * Roadshow's interfaces are up (OpenLibrary starts the stack, but bind can
 * still fail early).  v1.7: retries FOREVER - 2s apart for the first minute,
 * then every 10s - so the harness self-connects whenever the network path
 * eventually appears (a314 Pi finishing its own boot, WiFi rejoin, stack
 * restart) instead of giving up after 60s and needing a manual run.
 * CTRL-C still aborts the wait. */

#define BRINGUP_FAST_ATTEMPTS  30
#define BRINGUP_FAST_DELAY     100     /* Delay() ticks: 2s */
#define BRINGUP_SLOW_DELAY     500     /* Delay() ticks: 10s */

static UWORD g_port   = LISTEN_PORT;   /* overridable: `netharness 7801` */

/* ---- v1.10: one harness per port ------------------------------------------
 * A machine that starts the harness twice (the A1200's User-Startup had two
 * lines) used to keep the second copy alive forever, retrying bind every 10s.
 * A running harness now owns a public port "NetHarness.<tcp port>"; a new one
 * that finds it - and finds its owner still alive - exits at once.  RELOAD
 * gives the port up before it launches the replacement, and a port left by a
 * crashed instance (owner gone) is taken over. */
static struct MsgPort *g_single = NULL;
static char g_single_name[24];

static BOOL task_alive(struct Task *t)
{
    struct Node *n;
    BOOL found = FALSE;
    if (!t) return FALSE;
    Disable();                                /* the lists change in interrupts */
    if ((struct Task *)SysBase->ThisTask == t) found = TRUE;
    for (n = SysBase->TaskReady.lh_Head; !found && n->ln_Succ; n = n->ln_Succ)
        if ((struct Task *)n == t) found = TRUE;
    for (n = SysBase->TaskWait.lh_Head; !found && n->ln_Succ; n = n->ln_Succ)
        if ((struct Task *)n == t) found = TRUE;
    Enable();
    return found;
}

static BOOL claim_single(void)
{
    struct MsgPort *old;
    sprintf(g_single_name, "NetHarness.%lu", (unsigned long)g_port);
    g_single = CreateMsgPort();
    if (!g_single) return TRUE;               /* can't tell - run anyway */
    g_single->mp_Node.ln_Name = g_single_name;
    g_single->mp_Node.ln_Pri  = 0;
    Forbid();
    old = FindPort((STRPTR)g_single_name);
    if (old && task_alive((struct Task *)old->mp_SigTask)) {
        Permit();
        DeleteMsgPort(g_single);
        g_single = NULL;
        return FALSE;
    }
    if (old) RemPort(old);                    /* left behind by a crashed instance */
    AddPort(g_single);
    Permit();
    return TRUE;
}

static void release_single(void)
{
    if (!g_single) return;
    RemPort(g_single);
    DeleteMsgPort(g_single);
    g_single = NULL;
}

static BOOL server_up(void)
{
    volatile UBYTE sabuf[16];   /* raw sockaddr_in - see the note below */
    LONG one = 1;
    UWORD i;

    if (!SocketBase) {
        SocketBase = OpenLibrary((STRPTR)"bsdsocket.library", 4);
        if (!SocketBase) return FALSE;
    }

    g_listen = socket(AF_INET, SOCK_STREAM, 0);
    if (g_listen < 0) return FALSE;

    setsockopt(g_listen, SOL_SOCKET, SO_REUSEADDR, (APTR)&one, sizeof(one));

    /* Build the sockaddr as VOLATILE BYTES rather than struct fields: at -O2
     * this compiler can merge the adjacent 16-bit stores of sin_family and
     * sin_port into one 32-bit store and drop the family, leaving a sockaddr
     * the stack rejects with EAFNOSUPPORT.  Layout is family(2) port(2)
     * addr(4), big-endian throughout on 68k; addr 0 = INADDR_ANY. */
    for (i = 0; i < 16; i++) sabuf[i] = 0;
    sabuf[1] = AF_INET;
    sabuf[2] = (UBYTE)(g_port >> 8);
    sabuf[3] = (UBYTE)g_port;

    if (bind(g_listen, (struct sockaddr *)sabuf, 16) < 0 ||
        listen(g_listen, 1) < 0) {
        CloseSocket(g_listen);
        g_listen = -1;
        return FALSE;
    }
    return TRUE;
}

/* ---- main ----------------------------------------------------------------- */

int main(int argc, char **argv)
{
    UBYTE readbuf[512];
    LONG  n, idle_secs = 0;
    int   i;
    LONG  old_priority;

    /* Optional port argument. Running a NEW build on a spare port next to the
     * live one is the safe way to test changes: never hot-swap the harness
     * that is currently carrying your only remote control. */
    if (argc > 1 && argv[1]) {
        UWORD p = 0;
        const char *s = argv[1];
        while (*s >= '0' && *s <= '9') p = (UWORD)(p * 10 + (*s++ - '0'));
        if (p) g_port = p;
    }

    if (!claim_single()) {
        printf("netharness: another netharness already serves port %lu - exiting\n", (unsigned long)g_port);
        return 5;
    }
    {
        struct DateStamp ds;
        DateStamp(&ds);                       /* job numbers differ across restarts */
        g_jobno = (ULONG)ds.ds_Minute * 3000UL + (ULONG)ds.ds_Tick;
    }
    run_quiet("C:Delete T:netharness.out.#? QUIET");   /* leftovers of abandoned jobs */

    /* Modest boost: stay responsive above busy apps, but EXEC children are
     * explicitly started at 0 so they can't be starved by us either. */
    old_priority = SetTaskPri(FindTask(NULL), 20);

    /* pr_WindowPtr = -1: make DOS FAIL instead of putting up a requester.
     * Without this, any command touching a missing volume ("Please insert
     * volume NAS: in any drive") blocks forever inside our synchronous
     * System() call - and because this harness is single-threaded, that
     * wedges the ENTIRE remote channel with no way to click the requester
     * away (hit for real on 2026-07-25; needed a human at the machine).
     * EXEC children inherit this. */
    ((struct Process *)FindTask(NULL))->pr_WindowPtr = (APTR)-1L;

    IntuitionBase = (struct IntuitionBase *)OpenLibrary((STRPTR)"intuition.library", 37);
    GfxBase       = (struct GfxBase *)OpenLibrary((STRPTR)"graphics.library", 37);
    if (!IntuitionBase || !GfxBase) {
        printf("netharness: failed to open intuition/graphics\n");
        goto cleanup_libs;
    }

    if (!input_open()) {
        printf("netharness: failed to open input.device\n");
        goto cleanup_libs;
    }

    for (i = 0; ; i++) {
        if (server_up()) break;
        if (CheckSignal(SIGBREAKF_CTRL_C)) {
            printf("netharness: CTRL-C while waiting for the network - exiting\n");
            nh_log("CTRL-C during bring-up wait", 0);
            goto cleanup_input;
        }
        if (i == BRINGUP_FAST_ATTEMPTS) {
            printf("netharness: network not up yet - will keep retrying every 10s\n");
            nh_log("bind still failing - entering slow retry, errno",
                   SocketBase ? Errno() : -1);
        }
        Delay(i < BRINGUP_FAST_ATTEMPTS ? BRINGUP_FAST_DELAY : BRINGUP_SLOW_DELAY);
    }
    printf("netharness: listening on port %d\n", (int)g_port);
    nh_log("netharness " NH_VERSION " listening on port", (LONG)g_port);

    for (;;) {
        UBYTE peer[16];
        socklen_t peerlen = (socklen_t)sizeof(peer);
        /* Real addr buffer, not NULL: WinUAE's bsdsocket_emu tolerates
         * accept(s,NULL,NULL) but a real stack may EFAULT on it.  And on ANY
         * accept failure, sleep before retrying — a tight retry loop at our
         * boosted priority would busy-lock the whole machine. */
        if (g_reload) break;          /* handed off: do not accept() a closed fd */
        /* A stray instance used to be unkillable: blocked in accept()/Delay()
         * it never noticed a Shell `break`, so it had to be rebooted away.
         * Check the break signal on every pass round the accept loop. */
        if (CheckSignal(SIGBREAKF_CTRL_C)) {
            nh_log("CTRL-C: exiting", 0);
            break;
        }
        /* Block on the socket AND the break signal, never on accept() alone -
         * see wait_readable(): a blocking accept() here is what made
         * NetShutdown time out and defer its teardown. */
        {
            WORD w = wait_readable(g_listen);
            if (w < 0) { nh_log("shutdown requested: exiting", 0); g_shutdown = 1; break; }
            if (w == 0) continue;       /* nothing pending - re-check signals */
        }
        g_client = accept(g_listen, (struct sockaddr *)peer, &peerlen);
        if (g_client < 0) {
            nh_log("accept failed, errno", Errno());
            if (CheckSignal(SIGBREAKF_CTRL_C)) { nh_log("CTRL-C: exiting", 0); break; }
            Delay(50);   /* 1s */
            continue;
        }
        /* peer is a BSD sockaddr_in: len(1) family(1) port(2) addr(4) */
        if (!peer_allowed(peer + 4)) {
            char why[80];
            sprintf(why, "rejected %lu.%lu.%lu.%lu (not in ENV:" ALLOW_VAR "), port",
                    (unsigned long)peer[4], (unsigned long)peer[5], (unsigned long)peer[6], (unsigned long)peer[7]);
            nh_log(why, (LONG)g_port);
            CloseSocket(g_client);
            g_client = -1;
            continue;
        }
        nh_log("client connected, fd", g_client);
        cmdbuf_len = 0;

        idle_secs = 0;
        for (;;) {
            /* Same reasoning as the accept() above: a blocking recv() would
             * hold bsdsocket open across a shutdown request. */
            {
                WORD w = wait_readable(g_client);
                if (w < 0) { nh_log("shutdown requested: dropping client", 0); g_shutdown = 1; break; }
                if (w == 0) {
                    /* idle-client reaper: a controller that died without a
                     * FIN (link drop) would otherwise hold this slot forever
                     * - and the harness serves ONE client at a time */
                    if (++idle_secs >= CLIENT_IDLE_SECS) {
                        nh_log("client idle too long, dropping", idle_secs);
                        break;
                    }
                    continue;   /* idle - keep waiting */
                }
                idle_secs = 0;
            }
            /* only take what fits: the unparsed tail is always one partial
             * command (<= CMDBUF_SIZE), so a legit stream can't overflow */
            {
                LONG room = CMDBUF_SIZE - cmdbuf_len;
                if (room > (LONG)sizeof(readbuf)) room = sizeof(readbuf);
                n = recv(g_client, (APTR)readbuf, room, 0);
            }
            if (n <= 0) break;    /* client gone */
            if (g_reload) break;  /* handed off to the replacement */

            if (cmdbuf_len + n > CMDBUF_SIZE) {
                /* every valid command fits, so this is garbage or hostile;
                 * resyncing mid-stream could run payload bytes as commands */
                nh_log("command buffer overflow, dropping client", cmdbuf_len + n);
                break;
            }
            memcpy(cmdbuf + cmdbuf_len, readbuf, n);
            cmdbuf_len += (WORD)n;

            {
                WORD pos = 0, consumed = 0;
                while (pos < cmdbuf_len) {
                    consumed = dispatch_one(cmdbuf + pos, cmdbuf_len - pos);
                    if (consumed <= 0) break;
                    pos += consumed;
                }
                if (consumed < 0) {
                    nh_log("oversized command, dropping client", cmdbuf[pos]);
                    break;
                }
                if (pos > 0) {
                    WORD remaining = cmdbuf_len - pos;
                    if (remaining > 0) memmove(cmdbuf, cmdbuf + pos, remaining);
                    cmdbuf_len = remaining;
                }
            }
        }

        if (g_client >= 0) CloseSocket(g_client);
        g_client = -1;
        if (g_reload) break;          /* replacement is taking over; exit */
        if (g_shutdown) break;        /* stack going away; release it promptly */
        /* loop back to accept() for the next controller connection */
    }

    /* not reached in normal operation */
cleanup_input:
    input_close();
cleanup_libs:
    /* Release the listening socket BEFORE the library, so a NetShutdown that is
     * waiting on us can proceed the moment we exit. (Not on the RELOAD path -
     * there the replacement instance inherits the fd.) */
    if (!g_reload && g_listen >= 0) { CloseSocket(g_listen); g_listen = -1; }
    if (SocketBase)    CloseLibrary(SocketBase);
    if (GfxBase)       CloseLibrary((struct Library *)GfxBase);
    if (IntuitionBase) CloseLibrary((struct Library *)IntuitionBase);
    release_single();
    SetTaskPri(FindTask(NULL), old_priority);
    return 0;
}

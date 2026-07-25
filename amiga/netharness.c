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
 *
 *   Amiga -> host:
 *     RESP_SCREENSHOT_HDR = 0x81  payload: width2 height2 depth1 bpr2, then
 *       raw bitplane bytes (bpr*height per plane, depth planes back to back).
 *     RESP_ACK            = 0x82  1 byte; after each injected input command
 *       and for CMD_PING.
 *     RESP_EXEC           = 0x83  payload: rc4 outlen4, then outlen bytes of
 *       the command's captured output.
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
#include <dos/dostags.h>
#include <proto/exec.h>
#include <proto/dos.h>
#include <proto/intuition.h>
#include <string.h>
#include <stdio.h>

#include <netinclude/sys/socket.h>
#include <netinclude/netinet/in.h>
#include <netinclude/sys/select.h>
#include <inline/bsdsocket.h>

#define LISTEN_PORT 7800

#define CMD_MOUSE_MOVE    1
#define CMD_MOUSE_BUTTON  2
#define CMD_KEY           3
#define CMD_HOME_MOUSE    4
#define CMD_SCREENSHOT    5
#define CMD_REBOOT        6
#define CMD_RESET_INPUT   7
#define CMD_EXEC          8
#define CMD_PING          9

#define RESP_SCREENSHOT_HDR 0x81
#define RESP_ACK            0x82
#define RESP_EXEC           0x83

#define EXEC_CMD_MAX   512          /* max AmigaDOS command line we accept */
#define EXEC_OUT_FILE  "T:netharness.out"

struct IntuitionBase *IntuitionBase = NULL;
struct GfxBase       *GfxBase       = NULL;
struct Library       *SocketBase    = NULL;

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

static LONG g_client = -1;          /* accepted client socket, -1 = none */

/* ---- TCP helpers ------------------------------------------------------- */

/* send() until all bytes are out (TCP can take partial writes). */
static BOOL send_all(const UBYTE *buf, LONG len)
{
    while (len > 0) {
        LONG n = send(g_client, (APTR)buf, len, 0);
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

    if (keycode == RAWKEY_LSHIFT) {
        g_key_qualifier = down ? (g_key_qualifier | IEQUALIFIER_LSHIFT) : (g_key_qualifier & ~IEQUALIFIER_LSHIFT);
    } else if (keycode == RAWKEY_RSHIFT) {
        g_key_qualifier = down ? (g_key_qualifier | IEQUALIFIER_RSHIFT) : (g_key_qualifier & ~IEQUALIFIER_RSHIFT);
    }

    ie.ie_Class     = IECLASS_RAWKEY;
    ie.ie_Code      = down ? keycode : (UWORD)(keycode | IECODE_UP_PREFIX);
    ie.ie_Qualifier = g_key_qualifier;
    inject_event(&ie);
}

static void do_home_mouse(void)
{
    /* No "set absolute" event exists - a saturating negative delta pins the
     * pointer at (0,0) as a known origin. */
    do_mouse_move(-16384, -16384);
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

static void do_screenshot(void)
{
    struct Screen *scr;
    struct BitMap *bm;
    UWORD width, height, bpr;
    UBYTE depth, i;
    UBYTE hdr[8];

    scr = IntuitionBase->ActiveScreen;
    if (!scr) scr = IntuitionBase->FirstScreen;
    if (!scr) return;

    bm     = &scr->BitMap;
    width  = (UWORD)scr->Width;
    height = (UWORD)scr->Height;
    depth  = bm->Depth;
    bpr    = bm->BytesPerRow;

    hdr[0] = RESP_SCREENSHOT_HDR;
    hdr[1] = (UBYTE)(width  >> 8); hdr[2] = (UBYTE)width;
    hdr[3] = (UBYTE)(height >> 8); hdr[4] = (UBYTE)height;
    hdr[5] = depth;
    hdr[6] = (UBYTE)(bpr    >> 8); hdr[7] = (UBYTE)bpr;
    if (!send_all(hdr, sizeof(hdr))) return;

    /* Over TCP there is no packet cap - send each whole plane in one go;
     * send_all() handles the stack's own segmentation. */
    for (i = 0; i < depth; i++) {
        UBYTE *plane = (UBYTE *)bm->Planes[i];
        LONG   plane_size = (LONG)bpr * height;
        if (!plane) continue;
        if (!send_all(plane, plane_size)) return;
    }
}

/* ---- EXEC ---------------------------------------------------------------- */

static void do_exec(const UBYTE *cmd, UWORD len)
{
    char  cmdline[EXEC_CMD_MAX + 1];
    BPTR  in, out, rd;
    LONG  rc = -1;
    LONG  outlen = 0;
    UBYTE hdr[9];
    UBYTE iobuf[1024];

    if (len > EXEC_CMD_MAX) len = EXEC_CMD_MAX;
    memcpy(cmdline, cmd, len);
    cmdline[len] = 0;

    in  = Open((STRPTR)"NIL:", MODE_OLDFILE);
    out = Open((STRPTR)EXEC_OUT_FILE, MODE_NEWFILE);
    if (out) {
        /* Synchronous System(): we keep ownership of the handles and close
         * them ourselves.  Child runs at priority 0 so a busy command can't
         * starve this task (which sits above it). */
        rc = SystemTags((STRPTR)cmdline,
                        SYS_Input,   (Tag)in,
                        SYS_Output,  (Tag)out,
                        NP_Priority, (Tag)0,
                        TAG_DONE);
        Close(out);
    }
    if (in) Close(in);

    /* Measure the captured output. */
    rd = Open((STRPTR)EXEC_OUT_FILE, MODE_OLDFILE);
    if (rd) {
        Seek(rd, 0, OFFSET_END);
        outlen = Seek(rd, 0, OFFSET_BEGINNING);
        if (outlen < 0) outlen = 0;
    }

    hdr[0] = RESP_EXEC;
    hdr[1] = (UBYTE)(rc >> 24); hdr[2] = (UBYTE)(rc >> 16);
    hdr[3] = (UBYTE)(rc >>  8); hdr[4] = (UBYTE)rc;
    hdr[5] = (UBYTE)(outlen >> 24); hdr[6] = (UBYTE)(outlen >> 16);
    hdr[7] = (UBYTE)(outlen >>  8); hdr[8] = (UBYTE)outlen;
    if (!send_all(hdr, sizeof(hdr))) {
        if (rd) Close(rd);
        return;
    }

    if (rd) {
        LONG left = outlen;
        while (left > 0) {
            LONG want = (left > (LONG)sizeof(iobuf)) ? (LONG)sizeof(iobuf) : left;
            LONG n = Read(rd, iobuf, want);
            if (n <= 0) break;
            if (!send_all(iobuf, n)) break;
            left -= n;
        }
        Close(rd);
        DeleteFile((STRPTR)EXEC_OUT_FILE);
    }
}

/* ---- command stream reassembly -------------------------------------------
 * TCP is a byte stream: commands can arrive split across, or several per,
 * recv().  Accumulate and parse out complete commands - same pattern (and
 * same hard-won reason) as the A314 harness. */

#define CMDBUF_SIZE 1024
static UBYTE cmdbuf[CMDBUF_SIZE];
static WORD  cmdbuf_len = 0;

/* Returns bytes consumed for one complete command, or 0 if incomplete. */
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
            do_screenshot();
            return 1;
        case CMD_REBOOT:
            ColdReboot();
            return 1;   /* unreachable */
        case CMD_EXEC:
            if (avail < 3) return 0;
            {
                UWORD clen = (UWORD)((b[1] << 8) | b[2]);
                if (avail < (WORD)(3 + clen)) return 0;
                do_exec(b + 3, clen);
                return (WORD)(3 + clen);
            }
        case CMD_PING:
            send_ack();
            return 1;
        default:
            /* Unknown byte: drop and resync rather than jam the stream. */
            return 1;
    }
}

/* ---- TCP server bring-up -------------------------------------------------
 * Retried from scratch each round: on a cold boot this may run before
 * Roadshow's interfaces are up (OpenLibrary starts the stack, but bind can
 * still fail early).  30 x 2s covers boot ordering comfortably. */

#define BRINGUP_RETRY_ATTEMPTS 30
#define BRINGUP_RETRY_DELAY    100     /* Delay() ticks: 2s */

static LONG g_listen = -1;

static BOOL server_up(void)
{
    struct sockaddr_in sa;
    LONG one = 1;

    if (!SocketBase) {
        SocketBase = OpenLibrary((STRPTR)"bsdsocket.library", 4);
        if (!SocketBase) return FALSE;
    }

    g_listen = socket(AF_INET, SOCK_STREAM, 0);
    if (g_listen < 0) return FALSE;

    setsockopt(g_listen, SOL_SOCKET, SO_REUSEADDR, (APTR)&one, sizeof(one));

    memset(&sa, 0, sizeof(sa));
    sa.sin_family      = AF_INET;
    sa.sin_port        = LISTEN_PORT;
    sa.sin_addr.s_addr = 0;   /* INADDR_ANY */

    if (bind(g_listen, (APTR)&sa, sizeof(sa)) < 0 ||
        listen(g_listen, 1) < 0) {
        CloseSocket(g_listen);
        g_listen = -1;
        return FALSE;
    }
    return TRUE;
}

/* ---- main ----------------------------------------------------------------- */

int main(void)
{
    UBYTE readbuf[512];
    LONG  n;
    int   i;
    LONG  old_priority;

    /* Modest boost: stay responsive above busy apps, but EXEC children are
     * explicitly started at 0 so they can't be starved by us either. */
    old_priority = SetTaskPri(FindTask(NULL), 20);

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

    for (i = 0; i < BRINGUP_RETRY_ATTEMPTS; i++) {
        if (server_up()) break;
        Delay(BRINGUP_RETRY_DELAY);
    }
    if (g_listen < 0) {
        printf("netharness: could not bind TCP port %d (stack down?)\n", LISTEN_PORT);
        nh_log("bind/listen failed after retries, errno", SocketBase ? Errno() : -1);
        goto cleanup_input;
    }
    printf("netharness: listening on port %d\n", LISTEN_PORT);
    nh_log("listening on port", LISTEN_PORT);

    for (;;) {
        struct sockaddr_in peer;
        LONG peerlen = sizeof(peer);
        /* Real addr buffer, not NULL: WinUAE's bsdsocket_emu tolerates
         * accept(s,NULL,NULL) but a real stack may EFAULT on it.  And on ANY
         * accept failure, sleep before retrying — a tight retry loop at our
         * boosted priority would busy-lock the whole machine. */
        g_client = accept(g_listen, (APTR)&peer, &peerlen);
        if (g_client < 0) {
            nh_log("accept failed, errno", Errno());
            Delay(50);   /* 1s */
            continue;
        }
        nh_log("client connected, fd", g_client);
        cmdbuf_len = 0;

        for (;;) {
            n = recv(g_client, (APTR)readbuf, sizeof(readbuf), 0);
            if (n <= 0) break;    /* client gone */

            if (cmdbuf_len + n > CMDBUF_SIZE) {
                cmdbuf_len = 0;   /* never overrun; resync */
                continue;
            }
            memcpy(cmdbuf + cmdbuf_len, readbuf, n);
            cmdbuf_len += (WORD)n;

            {
                WORD pos = 0;
                while (pos < cmdbuf_len) {
                    WORD consumed = dispatch_one(cmdbuf + pos, cmdbuf_len - pos);
                    if (consumed == 0) break;
                    pos += consumed;
                }
                if (pos > 0) {
                    WORD remaining = cmdbuf_len - pos;
                    if (remaining > 0) memmove(cmdbuf, cmdbuf + pos, remaining);
                    cmdbuf_len = remaining;
                }
            }
        }

        CloseSocket(g_client);
        g_client = -1;
        /* loop back to accept() for the next controller connection */
    }

    /* not reached in normal operation */
cleanup_input:
    input_close();
cleanup_libs:
    if (SocketBase)    CloseLibrary(SocketBase);
    if (GfxBase)       CloseLibrary((struct Library *)GfxBase);
    if (IntuitionBase) CloseLibrary((struct Library *)IntuitionBase);
    SetTaskPri(FindTask(NULL), old_priority);
    return 0;
}

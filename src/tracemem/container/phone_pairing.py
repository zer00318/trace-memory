"""OWNS: the pairing ceremony — how the phone comes to hold the phone key.

The phone's camera meets its brain first: `./trace pair-phone` shows a QR
code in a window and the phone app scans it. The code carries the phone
purpose key (never the master), this Mac's Bonjour host name, its current
LAN addresses and the port. The image exists only in this process's
memory: no file holding a key is ever written, so there is nothing to clean
up and nothing for a backup to sweep up.

WHY A WINDOW (2026-10-15). The first version drew half-block characters in
the terminal. In a terminal whose line height exceeds its glyphs (the Claude
app's panel), every row pair is separated by a gap, and the phone saw a
striped code it could not read. `--pair-phone-text` keeps the terminal art
for a plain Terminal.app.

Run it only through the signed helper — the key is derived from the master
the Keychain releases to that binary alone (F-KEYS).
"""
from __future__ import annotations

import base64
import json
import socket
import subprocess
from typing import List

from tracemem.container.phone_link import PORT, PROTOCOL


def local_host_name() -> str:
    try:
        out = subprocess.run(["scutil", "--get", "LocalHostName"],
                             capture_output=True, text=True, timeout=5)
        name = out.stdout.strip()
        if name:
            return name + ".local"
    except Exception:                                    # noqa: BLE001
        pass
    return socket.gethostname()


def lan_addresses() -> List[str]:
    """IPv4 addresses on private interfaces, best first."""
    from tracemem.container.phone_link import peer_allowed
    out = []
    for iface in ("en0", "en1", "en2"):
        try:
            r = subprocess.run(["ipconfig", "getifaddr", iface],
                               capture_output=True, text=True, timeout=5)
        except Exception:                                # noqa: BLE001
            continue
        ip = r.stdout.strip()
        if ip and peer_allowed(ip) and not ip.startswith("127."):
            out.append(ip)
    return out


def payload(key: bytes, host: str, addresses: List[str], port: int = PORT,
            device: str = "") -> str:
    """`key` is the DEVICE key for `device` (phone_link.device_key), never the
    root: a code photographed by someone else opens one revocable id."""
    return json.dumps({"p": PROTOCOL, "h": host, "a": addresses, "port": port,
                       "d": device, "k": base64.b64encode(key).decode()},
                      separators=(",", ":"))


def qr_matrix(text: str) -> List[List[bool]]:
    """Modules of a QR code for `text`, via CoreImage (no extra dependency)."""
    import Quartz
    from Foundation import NSData

    data = NSData.dataWithBytes_length_(text.encode(), len(text.encode()))
    f = Quartz.CIFilter.filterWithName_("CIQRCodeGenerator")
    f.setValue_forKey_(data, "inputMessage")
    f.setValue_forKey_("M", "inputCorrectionLevel")
    image = f.outputImage()
    extent = image.extent()
    w, h = int(extent.size.width), int(extent.size.height)
    ctx = Quartz.CIContext.contextWithOptions_(None)
    cg = ctx.createCGImage_fromRect_(image, extent)
    space = Quartz.CGColorSpaceCreateDeviceGray()
    bitmap = Quartz.CGBitmapContextCreate(None, w, h, 8, w, space,
                                          Quartz.kCGImageAlphaNone)
    Quartz.CGContextDrawImage(bitmap, Quartz.CGRectMake(0, 0, w, h), cg)
    raw = Quartz.CGBitmapContextGetData(bitmap)
    buf = bytes(raw.as_buffer(w * h)) if hasattr(raw, "as_buffer") else bytes(raw[:w * h])
    return [[buf[y * w + x] < 128 for x in range(w)] for y in range(h)]


def render(matrix: List[List[bool]], quiet: int = 2) -> str:
    """Two rows per line with half blocks, BLACK ON WHITE whatever the
    terminal's theme: a light theme would otherwise draw the code inverted,
    and a phone camera reads an inverted code unreliably or not at all."""
    n = len(matrix)
    size = n + 2 * quiet

    def dark(x, y):
        x, y = x - quiet, y - quiet
        return 0 <= x < n and 0 <= y < n and matrix[y][x]

    glyph = {(False, False): " ", (True, False): "\u2580",
             (False, True): "\u2584", (True, True): "\u2588"}
    lines = []
    for y in range(0, size, 2):
        row = "".join(glyph[(dark(x, y), dark(x, y + 1))] for x in range(size))
        lines.append("\x1b[30;47m" + row + "\x1b[0m")
    return "\n".join(lines)


def ceremony(key: bytes, device: str = "") -> str:
    host, addresses = local_host_name(), lan_addresses()
    text = payload(key, host, addresses, device=device)
    return "\n".join([
        "PAIR YOUR PHONE — open TRACE on the iPhone and point it at this code.",
        "",
        render(qr_matrix(text)),
        "",
        "This Mac: %s  %s  port %d" % (host, " ".join(addresses) or "(no LAN address)", PORT),
        "This phone will be %s (revoke with ./trace revoke-phone %s)." % (device, device),
    ])

def qr_cgimage(text: str, scale: int = 10):
    """A crisp CGImage of the code, `scale` pixels per module."""
    import Quartz
    from Foundation import NSData

    raw = text.encode()
    f = Quartz.CIFilter.filterWithName_("CIQRCodeGenerator")
    f.setValue_forKey_(NSData.dataWithBytes_length_(raw, len(raw)), "inputMessage")
    f.setValue_forKey_("M", "inputCorrectionLevel")
    image = f.outputImage().imageByApplyingTransform_(
        Quartz.CGAffineTransformMakeScale(scale, scale))
    ctx = Quartz.CIContext.contextWithOptions_(None)
    return ctx.createCGImage_fromRect_(image, image.extent()), image.extent().size


def show_window(text: str, host: str, seconds: float = 600.0) -> None:
    """Show the code in a plain white window until it is closed (or for
    `seconds`). Blocks; returns when the window is gone."""
    import time

    import AppKit
    import Quartz
    from Foundation import NSDate

    cg, size = qr_cgimage(text)
    pad, caption = 48, 56
    w, h = size.width + 2 * pad, size.height + 2 * pad + caption
    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyRegular)
    win = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        Quartz.CGRectMake(0, 0, w, h),
        AppKit.NSWindowStyleMaskTitled | AppKit.NSWindowStyleMaskClosable,
        AppKit.NSBackingStoreBuffered, False)
    win.setReleasedWhenClosed_(False)
    win.setTitle_("Pair your iPhone with TRACE")
    win.setBackgroundColor_(AppKit.NSColor.whiteColor())
    view = AppKit.NSImageView.alloc().initWithFrame_(
        Quartz.CGRectMake(pad, pad + caption, size.width, size.height))
    view.setImage_(AppKit.NSImage.alloc().initWithCGImage_size_(cg, size))
    win.contentView().addSubview_(view)
    label = AppKit.NSTextField.labelWithString_(
        "Open TRACE on the iPhone and point its camera here.\n"
        "This Mac: %s  ·  close this window once the phone says paired." % host)
    label.setFrame_(Quartz.CGRectMake(pad, pad / 2, size.width, caption))
    label.setTextColor_(AppKit.NSColor.blackColor())
    win.contentView().addSubview_(label)
    win.center()
    win.makeKeyAndOrderFront_(None)
    app.activateIgnoringOtherApps_(True)
    deadline = time.monotonic() + seconds
    while win.isVisible() and time.monotonic() < deadline:
        event = app.nextEventMatchingMask_untilDate_inMode_dequeue_(
            AppKit.NSEventMaskAny, NSDate.dateWithTimeIntervalSinceNow_(0.2),
            AppKit.NSDefaultRunLoopMode, True)
        if event is not None:
            app.sendEvent_(event)
    win.close()


def pair(root: bytes, registry, now_ms: int, text_mode: bool = False) -> str:
    """The `./trace pair-phone` entry: mint a device, show ITS key."""
    from tracemem.container.phone_link import device_key
    device = registry.mint(now_ms)
    key = device_key(root, device)
    if text_mode:
        return ceremony(key, device)
    host, addresses = local_host_name(), lan_addresses()
    show_window(payload(key, host, addresses, device=device), host)
    return ("pairing window closed. This phone is %s; revoke it with "
            "./trace revoke-phone %s" % (device, device))

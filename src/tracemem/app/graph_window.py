"""The native, browsable live map shown by TRACE.app.

The face receives a bounded projection through the daemon's private Unix
socket.  It never opens the raw or graph database, and therefore never holds
the memory key.  Current beliefs are coloured, retired beliefs are hollow;
clicking either opens attributes, relationships, and exact cited moments.
"""
from __future__ import annotations

import math
import time

try:
    from tracemem.app.menubar import ask_daemon
except ImportError:                                      # bundled beside window.py
    from menubar import ask_daemon                       # type: ignore


_OPEN = []                         # retain controllers while their windows live


def _when(t_ms):
    if not t_ms:
        return "time unknown"
    try:
        return time.strftime("%Y-%m-%d %H:%M",
                             time.localtime(int(t_ms) / 1000))
    except (ValueError, OSError, OverflowError):
        return "time unknown"


def _detail_text(data):
    state = "RETIRED %s" % _when(data.get("retired_t_ms")) \
        if data.get("retired") else "CURRENT"
    lines = [str(data.get("label") or "Unnamed belief"),
             "%s · %s · %d supporting episode(s)" %
             (state, data.get("kind", "belief"),
              data.get("evidence_count", 0)), ""]
    attrs = data.get("attributes") or []
    if attrs:
        lines.append("WHAT IT HOLDS")
        for attr in attrs:
            lines.append("  %s: %s" % (attr.get("key"), attr.get("value")))
            lines.append("    %s · confidence %.0f%% · %s → %s" %
                         (attr.get("grade"),
                          100 * float(attr.get("confidence") or 0),
                          _when(attr.get("first_t_ms")),
                          _when(attr.get("last_t_ms"))))
        lines.append("")
    neighbours = data.get("neighbours") or []
    if neighbours:
        lines.append("CONNECTED TO")
        for neighbour in neighbours[:30]:
            lines.append("  %s — %s%s" %
                         (neighbour.get("edge", "related"),
                          neighbour.get("label") or neighbour.get("id", "")[:8],
                          " (retired)" if neighbour.get("retired") else ""))
        lines.append("")
    evidence = data.get("evidence") or []
    lines.append("SUPPORTING MOMENTS")
    if not evidence:
        lines.append("  No raw moment is currently resolvable for this belief.")
    for item in evidence:
        where = item.get("app") or item.get("source") or item.get("provenance")
        lines.extend([
            "  [%s] %s · %s · %s" %
            (str(item.get("claim_id") or "")[:8], _when(item.get("t_ms")),
             item.get("grade", ""), where or "source unknown"),
            "    %s" % str(item.get("text") or "").replace("\n", " "),
        ])
    if data.get("evidence_truncated"):
        lines.append("  …more supporting moments exist; narrow the map search.")
    return "\n".join(lines)


def build_controller():
    import AppKit
    import objc
    from Foundation import NSMakeRect, NSObject

    W, H = 900, 650

    class GraphCanvas(AppKit.NSView):
        def initWithFrame_controller_(self, frame, controller):
            self = objc.super(GraphCanvas, self).initWithFrame_(frame)
            if self is not None:
                self.controller = controller
                self.nodes, self.edges, self.positions = [], [], {}
            return self

        def setGraph_edges_(self, nodes, edges):
            self.nodes, self.edges = list(nodes), list(edges)
            self._layout()
            self.setNeedsDisplay_(True)

        @objc.python_method
        def _layout(self):
            bounds = self.bounds()
            width, height = max(100, bounds.size.width), max(100, bounds.size.height)
            cx, cy = width / 2, height / 2
            current = [n for n in self.nodes if not n.get("retired")]
            retired = [n for n in self.nodes if n.get("retired")]
            self.positions = {}
            # A deterministic golden-angle spiral keeps dense current graphs
            # readable without pretending this is a physics simulation.
            for i, node in enumerate(current):
                angle = i * 2.399963
                radius = 18 + math.sqrt(i + 1) * min(width, height) * 0.036
                radius = min(radius, min(width, height) * 0.34)
                self.positions[node["id"]] = (
                    cx + math.cos(angle) * radius,
                    cy + math.sin(angle) * radius)
            outer = min(width, height) * 0.43
            for i, node in enumerate(retired):
                angle = 2 * math.pi * i / max(1, len(retired))
                self.positions[node["id"]] = (
                    cx + math.cos(angle) * outer,
                    cy + math.sin(angle) * outer)

        def drawRect_(self, _dirty):
            AppKit.NSColor.windowBackgroundColor().setFill()
            AppKit.NSBezierPath.fillRect_(self.bounds())
            AppKit.NSColor.separatorColor().setStroke()
            for edge in self.edges:
                a = self.positions.get(edge.get("src"))
                b = self.positions.get(edge.get("dst"))
                if not a or not b:
                    continue
                path = AppKit.NSBezierPath.bezierPath()
                path.moveToPoint_(a)
                path.lineToPoint_(b)
                path.setLineWidth_(min(2.0, 0.35 + float(edge.get("weight") or 0)))
                path.stroke()
            selected = getattr(self.controller, "selected", None)
            for node in self.nodes:
                x, y = self.positions.get(node["id"], (0, 0))
                radius = 8.0 + min(7.0, math.log1p(node.get("evidence_count", 0)) * 2)
                rect = NSMakeRect(x - radius, y - radius, radius * 2, radius * 2)
                if node.get("retired"):
                    AppKit.NSColor.systemGrayColor().setStroke()
                    path = AppKit.NSBezierPath.bezierPathWithOvalInRect_(rect)
                    path.setLineWidth_(3 if selected == node["id"] else 1.5)
                    path.stroke()
                else:
                    colors = {
                        "person": AppKit.NSColor.systemBlueColor(),
                        "place": AppKit.NSColor.systemGreenColor(),
                        "site": AppKit.NSColor.systemPurpleColor(),
                        "pattern": AppKit.NSColor.systemOrangeColor(),
                        "hypothesis": AppKit.NSColor.systemRedColor(),
                    }
                    colors.get(node.get("kind"), AppKit.NSColor.systemTealColor()).setFill()
                    AppKit.NSBezierPath.bezierPathWithOvalInRect_(rect).fill()
                    if selected == node["id"]:
                        AppKit.NSColor.labelColor().setStroke()
                        ring = AppKit.NSBezierPath.bezierPathWithOvalInRect_(
                            NSMakeRect(x-radius-3, y-radius-3,
                                       radius*2+6, radius*2+6))
                        ring.setLineWidth_(2)
                        ring.stroke()
                if len(self.nodes) <= 70 or selected == node["id"]:
                    label = str(node.get("label") or "")[:22]
                    attrs = {AppKit.NSFontAttributeName:
                             AppKit.NSFont.systemFontOfSize_(9)}
                    label.drawAtPoint_withAttributes_((x + radius + 2, y - 5), attrs)

        def mouseDown_(self, event):
            point = self.convertPoint_fromView_(event.locationInWindow(), None)
            nearest, distance = None, 10 ** 9
            for node in self.nodes:
                x, y = self.positions.get(node["id"], (0, 0))
                d = math.hypot(point.x - x, point.y - y)
                if d < distance:
                    nearest, distance = node, d
            if nearest is not None and distance <= 24:
                self.controller.openNode_(nearest["id"])

    class LiveMapController(NSObject):
        def init(self):
            self = objc.super(LiveMapController, self).init()
            if self is None:
                return None
            self.selected = None
            style = (AppKit.NSWindowStyleMaskTitled |
                     AppKit.NSWindowStyleMaskClosable |
                     AppKit.NSWindowStyleMaskResizable)
            self.win = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
                NSMakeRect(0, 0, W, H), style,
                AppKit.NSBackingStoreBuffered, False)
            self.win.setTitle_("TRACE — Live map")
            self.win.center()
            self.win.setReleasedWhenClosed_(False)
            self.win.setDelegate_(self)
            view = self.win.contentView()

            self.search = AppKit.NSSearchField.alloc().initWithFrame_(
                NSMakeRect(18, H - 44, 350, 26))
            self.search.setPlaceholderString_("Find a person, place, site, or belief")
            self.search.setTarget_(self)
            self.search.setAction_("refresh:")
            view.addSubview_(self.search)
            self.summary = AppKit.NSTextField.labelWithString_("")
            self.summary.setFrame_(NSMakeRect(382, H - 43, W - 400, 24))
            view.addSubview_(self.summary)

            self.canvas = GraphCanvas.alloc().initWithFrame_controller_(
                NSMakeRect(12, 12, 555, H - 66), self)
            self.canvas.setAutoresizingMask_(
                AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
            view.addSubview_(self.canvas)

            scroll = AppKit.NSScrollView.alloc().initWithFrame_(
                NSMakeRect(575, 12, W - 587, H - 66))
            scroll.setHasVerticalScroller_(True)
            scroll.setBorderType_(AppKit.NSBezelBorder)
            scroll.setAutoresizingMask_(
                AppKit.NSViewMinXMargin | AppKit.NSViewHeightSizable)
            self.detail = AppKit.NSTextView.alloc().initWithFrame_(
                NSMakeRect(0, 0, W - 590, H - 68))
            self.detail.setEditable_(False)
            self.detail.setFont_(AppKit.NSFont.userFixedPitchFontOfSize_(10))
            scroll.setDocumentView_(self.detail)
            view.addSubview_(scroll)
            self.refresh_(None)
            return self

        def refresh_(self, _sender):
            reply = ask_daemon("map", timeout=30.0,
                               query=str(self.search.stringValue()),
                               include_retired=True, limit=160)
            if not reply or not reply.get("ok"):
                self.detail.setString_(
                    "TRACE is not ready to show the map.\n\n%s" %
                    ((reply or {}).get("error") or "Turn TRACE on first."))
                return
            self.canvas.setGraph_edges_(reply.get("nodes") or [],
                                        reply.get("edges") or [])
            suffix = " · narrow the search to see more" if reply.get("truncated") else ""
            self.summary.setStringValue_(
                "%d current · %d retired · %d shown%s" %
                (reply.get("total_live", 0), reply.get("total_retired", 0),
                 reply.get("shown", 0), suffix))
            if not reply.get("nodes"):
                self.detail.setString_("No beliefs match that search.")
            elif self.selected is None:
                self.detail.setString_(
                    "Click any belief to inspect what it holds, how it is "
                    "connected, and the exact moments supporting it.\n\n"
                    "Hollow circles are retired beliefs: things TRACE used "
                    "to think and did not erase.")

        def openNode_(self, node_id):
            self.selected = str(node_id)
            self.canvas.setNeedsDisplay_(True)
            reply = ask_daemon("belief", timeout=30.0, id=self.selected)
            if not reply or not reply.get("ok"):
                self.detail.setString_((reply or {}).get("error") or
                                       "That belief could not be opened.")
                return
            self.detail.setString_(_detail_text(reply))

        def windowWillClose_(self, _notification):
            if self in _OPEN:
                _OPEN.remove(self)

    return LiveMapController


def open_live_map():
    controller_class = build_controller()
    controller = controller_class.alloc().init()
    _OPEN.append(controller)
    controller.win.makeKeyAndOrderFront_(None)
    return controller

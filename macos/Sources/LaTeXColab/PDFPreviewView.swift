import AppKit
import PDFKit
import SwiftUI

/// PDFKit preview. A plain click (no drag, no modifiers) reports the page, the
/// point in page space and the text around the click so the model can map it
/// to a source paragraph.
struct PDFPreviewView: NSViewRepresentable {
    let document: PDFDocument?
    let version: Int
    let onClick: (_ pageIndex: Int, _ pagePoint: CGPoint, _ pageBounds: CGRect, _ nearbyText: String?) -> Void

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    func makeNSView(context: Context) -> ClickTrackingPDFView {
        let view = ClickTrackingPDFView()
        view.autoScales = true
        view.displayMode = .singlePageContinuous
        view.displayDirection = .vertical
        view.displaysPageBreaks = true
        view.backgroundColor = NSColor.underPageBackgroundColor
        view.pageShadowsEnabled = true
        view.onClick = { [weak coordinator = context.coordinator] point in
            coordinator?.handleClick(at: point)
        }
        context.coordinator.pdfView = view
        view.document = document
        context.coordinator.lastDocument = document
        context.coordinator.version = version
        return view
    }

    func updateNSView(_ view: ClickTrackingPDFView, context: Context) {
        let c = context.coordinator
        c.parent = self
        guard c.version != version || view.document !== document else { return }
        let destination = view.currentDestination
        let oldIndex = destination?.page.flatMap { c.lastDocument?.index(for: $0) }
        let point = destination?.point ?? .zero
        view.document = document
        if let idx = oldIndex, let doc = document, idx < doc.pageCount, let page = doc.page(at: idx) {
            DispatchQueue.main.async {
                view.go(to: PDFDestination(page: page, at: point))
            }
        }
        c.lastDocument = document
        c.version = version
    }

    static func dismantleNSView(_ view: ClickTrackingPDFView, coordinator: Coordinator) {
        view.stopTracking()
    }

    final class Coordinator: NSObject {
        var parent: PDFPreviewView
        weak var pdfView: PDFView?
        var lastDocument: PDFDocument?
        var version = -1
        private var clearWork: DispatchWorkItem?

        init(_ parent: PDFPreviewView) { self.parent = parent }

        /// `viewPoint` is in the PDFView's coordinate space.
        func handleClick(at viewPoint: NSPoint) {
            guard let view = pdfView, let doc = view.document else {
                if ClickTrackingPDFView.debug { NSLog("pdf-click: no view/document") }
                return
            }
            guard let page = view.page(for: viewPoint, nearest: false) else {
                if ClickTrackingPDFView.debug { NSLog("pdf-click: no page under point") }
                return
            }
            let pagePoint = view.convert(viewPoint, to: page)
            let bounds = page.bounds(for: .mediaBox)
            let index = doc.index(for: page)

            // Text in a band around the click, for the fallback matcher.
            let band = CGRect(x: bounds.minX, y: pagePoint.y - 26, width: bounds.width, height: 52)
            let nearby = page.selection(for: band)?.string

            // Flash the clicked line so the user sees what was picked.
            if let line = page.selectionForLine(at: pagePoint) {
                view.setCurrentSelection(line, animate: true)
                clearWork?.cancel()
                let work = DispatchWorkItem { [weak view] in view?.clearSelection() }
                clearWork = work
                DispatchQueue.main.asyncAfter(deadline: .now() + 1.0, execute: work)
            }
            parent.onClick(index, pagePoint, bounds, nearby)
        }
    }
}

/// PDFView that detects plain clicks through a local event monitor, so it works
/// no matter how PDFKit's internal views consume mouse events.
final class ClickTrackingPDFView: PDFView {
    var onClick: ((NSPoint) -> Void)?
    private var monitor: Any?
    private var downPoint: NSPoint?
    private var downTime: TimeInterval = 0

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        if window != nil { startTracking() } else { stopTracking() }
    }

    func startTracking() {
        guard monitor == nil else { return }
        monitor = NSEvent.addLocalMonitorForEvents(matching: [.leftMouseDown, .leftMouseUp]) { [weak self] event in
            self?.track(event)
            return event
        }
    }

    func stopTracking() {
        if let m = monitor { NSEvent.removeMonitor(m) }
        monitor = nil
    }

    deinit { stopTracking() }

    static let debug = ProcessInfo.processInfo.environment["LATEXCOLAB_DEBUG"] != nil

    private func track(_ event: NSEvent) {
        guard let window, event.window === window, let superview else {
            if Self.debug { NSLog("pdf-click: event for another window (%@)", event.window?.title ?? "nil") }
            return
        }
        let point = convert(event.locationInWindow, from: nil)
        if Self.debug { NSLog("pdf-click: %@ at view point (%.0f, %.0f) bounds %@", event.type == .leftMouseDown ? "down" : "up", point.x, point.y, NSStringFromRect(bounds)) }
        guard bounds.contains(point) else { downPoint = nil; return }
        if event.type == .leftMouseDown {
            // Ignore the scrollers and anything that isn't the page area.
            let hit = hitTest(superview.convert(event.locationInWindow, from: nil))
            var v: NSView? = hit
            while let cur = v {
                if cur is NSScroller { downPoint = nil; return }
                v = cur.superview
            }
            downPoint = point
            downTime = event.timestamp
            return
        }
        guard let start = downPoint else { return }
        downPoint = nil
        let moved = hypot(point.x - start.x, point.y - start.y)
        guard moved < 4, event.clickCount <= 1, event.timestamp - downTime < 1.0 else { return }
        guard event.modifierFlags.intersection([.command, .shift, .option, .control]).isEmpty else { return }
        if Self.debug { NSLog("pdf-click: recognised click, onClick set: %d", onClick != nil ? 1 : 0) }
        onClick?(point)
    }
}

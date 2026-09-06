import AppKit
import PDFKit
import SwiftUI

/// PDFKit preview. A plain click (no drag, no modifiers) reports the page, the
/// point in page space and the text around the click so the model can map it
/// to a source paragraph.
struct PDFPreviewView: NSViewRepresentable {
    let document: PDFDocument?
    let version: Int
    var focus: PDFFocus? = nil
    let onClick: (_ pageIndex: Int, _ pagePoint: CGPoint, _ pageBounds: CGRect, _ nearbyText: String?, _ mode: PDFEditMode) -> Void

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    func makeNSView(context: Context) -> ClickTrackingPDFView {
        let view = ClickTrackingPDFView()
        view.autoScales = true
        view.displayMode = .singlePageContinuous
        view.displayDirection = .vertical
        view.displaysPageBreaks = true
        view.backgroundColor = NSColor.underPageBackgroundColor
        view.pageShadowsEnabled = true
        view.onClick = { [weak coordinator = context.coordinator] point, modifiers, selection in
            coordinator?.handleClick(at: point, option: modifiers.contains(.option), selection: selection)
        }
        view.onContextEdit = { [weak coordinator = context.coordinator] point, kind, selection in
            switch kind {
            case 0: coordinator?.handleClick(at: point, option: false, selection: nil)
            case 1: coordinator?.handleClick(at: point, option: true, selection: nil)
            default: coordinator?.handleClick(at: point, option: true, selection: selection)
            }
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
        let focusChanged = focus?.id != c.lastFocusID
        if c.version != version || view.document !== document {
            let destination = view.currentDestination
            let oldIndex = destination?.page.flatMap { c.lastDocument?.index(for: $0) }
            let point = destination?.point ?? .zero
            view.document = document
            if !focusChanged, let idx = oldIndex, let doc = document, idx < doc.pageCount, let page = doc.page(at: idx) {
                DispatchQueue.main.async {
                    view.go(to: PDFDestination(page: page, at: point))
                }
            }
            c.lastDocument = document
            c.version = version
        }
        if focusChanged {
            c.lastFocusID = focus?.id
            if let f = focus {
                DispatchQueue.main.async { c.show(f) }
            }
        }
    }

    static func dismantleNSView(_ view: ClickTrackingPDFView, coordinator: Coordinator) {
        view.stopTracking()
    }

    final class Coordinator: NSObject {
        var parent: PDFPreviewView
        weak var pdfView: PDFView?
        var lastDocument: PDFDocument?
        var version = -1
        var lastFocusID: UUID?
        private var clearWork: DispatchWorkItem?

        init(_ parent: PDFPreviewView) { self.parent = parent }

        /// Scroll so the paragraph is near the top of the view and flash it.
        func show(_ focus: PDFFocus) {
            guard let view = pdfView, let doc = view.document, focus.pageIndex < doc.pageCount,
                  let page = doc.page(at: focus.pageIndex) else { return }
            let mb = page.bounds(for: .mediaBox)
            let rect = CGRect(x: mb.minX + focus.rect.minX,
                              y: mb.maxY - focus.rect.maxY,
                              width: focus.rect.width,
                              height: focus.rect.height)
            view.go(to: PDFDestination(page: page, at: CGPoint(x: mb.minX, y: min(mb.maxY, rect.maxY + 36))))
            if let sel = page.selection(for: rect.insetBy(dx: -2, dy: -2)) {
                view.setCurrentSelection(sel, animate: true)
                clearWork?.cancel()
                let work = DispatchWorkItem { [weak view] in view?.clearSelection() }
                clearWork = work
                DispatchQueue.main.asyncAfter(deadline: .now() + 2.5, execute: work)
            }
        }

        /// `viewPoint` is in the PDFView's coordinate space. Plain click →
        /// paragraph; option → the sentence under the pointer, or the current
        /// text selection when the pointer is inside it.
        func handleClick(at viewPoint: NSPoint, option: Bool, selection: PDFSelection?) {
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

            if option, let sel = selection, let text = sel.string,
               !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
               sel.pages.contains(page), sel.bounds(for: page).insetBy(dx: -6, dy: -6).contains(pagePoint) {
                let lines = sel.selectionsByLine()
                if let first = lines.first, let last = lines.last, let p0 = first.pages.first, let p1 = last.pages.first {
                    let b0 = first.bounds(for: p0), b1 = last.bounds(for: p1)
                    let start = CGPoint(x: b0.minX + 3, y: b0.midY)
                    let end = CGPoint(x: b1.maxX - 3, y: b1.midY)
                    view.setCurrentSelection(sel, animate: false)
                    scheduleClear(view, after: 1.5)
                    parent.onClick(doc.index(for: p0), start, p0.bounds(for: .mediaBox), nearby,
                                   .selection(text: text, endPageIndex: doc.index(for: p1), endPoint: end))
                    return
                }
            }

            // Flash the clicked line so the user sees what was picked.
            if let line = page.selectionForLine(at: pagePoint) {
                view.setCurrentSelection(line, animate: true)
                scheduleClear(view, after: 1.0)
            }
            if option {
                let word = page.selectionForWord(at: pagePoint)?.string
                parent.onClick(index, pagePoint, bounds, nearby, .sentence(word: word))
            } else {
                parent.onClick(index, pagePoint, bounds, nearby, .paragraph)
            }
        }

        private func scheduleClear(_ view: PDFView, after seconds: Double) {
            clearWork?.cancel()
            let work = DispatchWorkItem { [weak view] in view?.clearSelection() }
            clearWork = work
            DispatchQueue.main.asyncAfter(deadline: .now() + seconds, execute: work)
        }
    }
}

/// PDFView that detects plain clicks through a local event monitor, so it works
/// no matter how PDFKit's internal views consume mouse events.
final class ClickTrackingPDFView: PDFView {
    var onClick: ((NSPoint, NSEvent.ModifierFlags, PDFSelection?) -> Void)?
    /// Context menu: kind 0 = paragraph, 1 = sentence, 2 = selection.
    var onContextEdit: ((NSPoint, Int, PDFSelection?) -> Void)?
    private var monitor: Any?
    private var downPoint: NSPoint?
    private var downTime: TimeInterval = 0
    private var downModifiers: NSEvent.ModifierFlags = []
    /// The selection as it was before this mouse-down (PDFKit clears it on click).
    private var selectionAtMouseDown: PDFSelection?
    private var contextPoint: NSPoint = .zero

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
            downModifiers = event.modifierFlags.intersection([.command, .shift, .option, .control])
            selectionAtMouseDown = currentSelection.map { $0.copy() as! PDFSelection }
            return
        }
        guard let start = downPoint else { return }
        downPoint = nil
        let moved = hypot(point.x - start.x, point.y - start.y)
        guard moved < 4, event.clickCount <= 1, event.timestamp - downTime < 1.0 else { return }
        // ⌥ = sentence / selection; ⌘, ⇧ and ⌃ are left to PDFKit.
        guard downModifiers.isSubset(of: [.option]) else { return }
        if Self.debug { NSLog("pdf-click: recognised click (option: %d), onClick set: %d", downModifiers.contains(.option) ? 1 : 0, onClick != nil ? 1 : 0) }
        onClick?(point, downModifiers, selectionAtMouseDown)
    }

    // MARK: - Context menu

    override func menu(for event: NSEvent) -> NSMenu? {
        contextPoint = convert(event.locationInWindow, from: nil)
        let menu = NSMenu()
        let hasSelection = !(currentSelection?.string ?? "").trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        let items: [(String, Int, Bool)] = [
            ("Edit Selection…", 2, hasSelection),
            ("Edit Sentence…", 1, true),
            ("Edit Paragraph…", 0, true),
        ]
        for (title, kind, enabled) in items {
            let item = NSMenuItem(title: title, action: #selector(contextEdit(_:)), keyEquivalent: "")
            item.target = self
            item.tag = kind
            item.isEnabled = enabled
            menu.addItem(item)
        }
        menu.autoenablesItems = false
        if let original = super.menu(for: event), !original.items.isEmpty {
            menu.addItem(.separator())
            for item in original.items {
                if let copy = item.copy() as? NSMenuItem { menu.addItem(copy) }
            }
        }
        return menu
    }

    @objc private func contextEdit(_ sender: NSMenuItem) {
        onContextEdit?(contextPoint, sender.tag, currentSelection)
    }
}

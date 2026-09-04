import AppKit
import SwiftUI

struct TextHighlight: Equatable {
    let range: NSRange
    let color: NSColor
}

/// NSTextView wrapper with LaTeX syntax colouring, optional line numbers and
/// optional background highlights (used for the rewrite diff).
struct CodeTextView: NSViewRepresentable {
    @Binding var text: String
    var isEditable = true
    var showLineNumbers = false
    var fontSize: CGFloat = 13
    var highlights: [TextHighlight] = []
    var onChange: ((String) -> Void)? = nil

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    func makeNSView(context: Context) -> NSScrollView {
        // Explicit TextKit 1 stack: predictable layoutManager access for the ruler.
        let storage = NSTextStorage()
        let layout = NSLayoutManager()
        storage.addLayoutManager(layout)
        let container = NSTextContainer(size: NSSize(width: 0, height: CGFloat.greatestFiniteMagnitude))
        container.widthTracksTextView = true
        layout.addTextContainer(container)

        let tv = NSTextView(frame: .zero, textContainer: container)
        tv.minSize = .zero
        tv.maxSize = NSSize(width: CGFloat.greatestFiniteMagnitude, height: CGFloat.greatestFiniteMagnitude)
        tv.isVerticallyResizable = true
        tv.isHorizontallyResizable = false
        tv.autoresizingMask = [.width]
        tv.isRichText = false
        tv.allowsUndo = true
        tv.usesFindBar = true
        tv.isIncrementalSearchingEnabled = true
        tv.isAutomaticQuoteSubstitutionEnabled = false
        tv.isAutomaticDashSubstitutionEnabled = false
        tv.isAutomaticTextReplacementEnabled = false
        tv.isAutomaticSpellingCorrectionEnabled = false
        tv.isAutomaticLinkDetectionEnabled = false
        tv.isContinuousSpellCheckingEnabled = false
        tv.isGrammarCheckingEnabled = false
        tv.smartInsertDeleteEnabled = false
        let font = NSFont.monospacedSystemFont(ofSize: fontSize, weight: .regular)
        tv.font = font
        tv.typingAttributes = [.font: font, .foregroundColor: NSColor.labelColor]
        tv.textColor = .labelColor
        tv.backgroundColor = .textBackgroundColor
        tv.insertionPointColor = .labelColor
        tv.textContainerInset = NSSize(width: 6, height: 8)
        tv.isEditable = isEditable
        tv.isSelectable = true
        tv.delegate = context.coordinator

        let scroll = NSScrollView()
        scroll.documentView = tv
        scroll.hasVerticalScroller = true
        scroll.hasHorizontalScroller = false
        scroll.autohidesScrollers = true
        scroll.borderType = .noBorder
        scroll.drawsBackground = true
        scroll.backgroundColor = .textBackgroundColor
        if showLineNumbers {
            let ruler = LineNumberRulerView(textView: tv)
            scroll.verticalRulerView = ruler
            scroll.hasVerticalRuler = true
            scroll.rulersVisible = true
        }
        context.coordinator.textView = tv
        tv.string = text
        context.coordinator.rehighlight(immediate: true)
        return scroll
    }

    func updateNSView(_ scroll: NSScrollView, context: Context) {
        let c = context.coordinator
        c.parent = self
        guard let tv = c.textView else { return }
        if tv.isEditable != isEditable { tv.isEditable = isEditable }
        if !c.isEditing && tv.string != text {
            let selected = tv.selectedRange()
            tv.string = text
            let len = (text as NSString).length
            let loc = min(selected.location, len)
            tv.setSelectedRange(NSRange(location: loc, length: min(selected.length, len - loc)))
            c.rehighlight(immediate: true)
        } else if c.appliedHighlights != highlights {
            c.rehighlight(immediate: true)
        }
    }

    final class Coordinator: NSObject, NSTextViewDelegate {
        var parent: CodeTextView
        weak var textView: NSTextView?
        var isEditing = false
        var appliedHighlights: [TextHighlight] = []
        private var pending: DispatchWorkItem?

        init(_ parent: CodeTextView) { self.parent = parent }

        func textDidChange(_ notification: Notification) {
            guard let tv = textView else { return }
            isEditing = true
            parent.text = tv.string
            parent.onChange?(tv.string)
            isEditing = false
            rehighlight()
        }

        func rehighlight(immediate: Bool = false) {
            pending?.cancel()
            let work = DispatchWorkItem { [weak self] in self?.applyHighlighting() }
            pending = work
            if immediate {
                work.perform()
            } else {
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.12, execute: work)
            }
        }

        private func applyHighlighting() {
            guard let tv = textView, let storage = tv.textStorage else { return }
            appliedHighlights = parent.highlights
            LaTeXHighlighter.highlight(storage, fontSize: parent.fontSize, backgrounds: parent.highlights)
        }
    }
}

enum LaTeXHighlighter {
    static let comment = try! NSRegularExpression(pattern: #"(?<!\\)%[^\n]*"#)
    static let command = try! NSRegularExpression(pattern: #"\\(?:[a-zA-Z@]+\*?|[^a-zA-Z\s])"#)
    static let math = try! NSRegularExpression(pattern: #"\$\$[\s\S]*?\$\$|\$[^$\n]*\$|\\\[[\s\S]*?\\\]|\\\([\s\S]*?\\\)"#)
    static let environment = try! NSRegularExpression(pattern: #"\\(?:begin|end)\{([^}]*)\}"#)
    static let brace = try! NSRegularExpression(pattern: #"[{}\[\]]"#)

    static func highlight(_ storage: NSTextStorage, fontSize: CGFloat, backgrounds: [TextHighlight]) {
        let full = NSRange(location: 0, length: storage.length)
        let s = storage.string
        storage.beginEditing()
        storage.setAttributes([
            .font: NSFont.monospacedSystemFont(ofSize: fontSize, weight: .regular),
            .foregroundColor: NSColor.labelColor,
        ], range: full)
        for m in math.matches(in: s, range: full) {
            storage.addAttribute(.foregroundColor, value: NSColor.systemPurple, range: m.range)
        }
        for m in brace.matches(in: s, range: full) {
            storage.addAttribute(.foregroundColor, value: NSColor.systemGray, range: m.range)
        }
        for m in command.matches(in: s, range: full) {
            storage.addAttribute(.foregroundColor, value: NSColor.systemBlue, range: m.range)
        }
        for m in environment.matches(in: s, range: full) {
            storage.addAttribute(.foregroundColor, value: NSColor.systemGreen, range: m.range(at: 1))
        }
        for m in comment.matches(in: s, range: full) {
            storage.addAttribute(.foregroundColor, value: NSColor.secondaryLabelColor, range: m.range)
        }
        for h in backgrounds {
            let r = NSIntersectionRange(h.range, full)
            if r.length > 0 { storage.addAttribute(.backgroundColor, value: h.color, range: r) }
        }
        storage.endEditing()
    }
}

/// Gutter with line numbers for an NSTextView inside an NSScrollView.
final class LineNumberRulerView: NSRulerView {
    private weak var textView: NSTextView?

    init(textView: NSTextView) {
        self.textView = textView
        super.init(scrollView: textView.enclosingScrollView, orientation: .verticalRuler)
        clientView = textView
        ruleThickness = 46
        NotificationCenter.default.addObserver(self, selector: #selector(refresh),
                                               name: NSText.didChangeNotification, object: textView)
        if let clip = textView.enclosingScrollView?.contentView {
            clip.postsBoundsChangedNotifications = true
            NotificationCenter.default.addObserver(self, selector: #selector(refresh),
                                                   name: NSView.boundsDidChangeNotification, object: clip)
        }
    }

    required init(coder: NSCoder) { fatalError("init(coder:) is not supported") }

    override var isFlipped: Bool { true }

    @objc private func refresh() { needsDisplay = true }

    override func drawHashMarksAndLabels(in rect: NSRect) {
        NSColor.windowBackgroundColor.setFill()
        bounds.fill()
        guard let tv = textView, let lm = tv.layoutManager, let tc = tv.textContainer else { return }
        let ns = tv.string as NSString
        let attrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.monospacedDigitSystemFont(ofSize: 10.5, weight: .regular),
            .foregroundColor: NSColor.tertiaryLabelColor,
        ]
        let visible = tv.visibleRect
        let glyphRange = lm.glyphRange(forBoundingRect: visible, in: tc)
        let charRange = lm.characterRange(forGlyphRange: glyphRange, actualGlyphRange: nil)

        // Line number of the first visible line = newlines before it + 1.
        var index = ns.lineRange(for: NSRange(location: min(charRange.location, ns.length), length: 0)).location
        var lineNumber = 1
        if index > 0 {
            var i = 0
            while i < index {
                if ns.character(at: i) == 10 { lineNumber += 1 }
                i += 1
            }
        }

        func draw(_ number: Int, at lineRect: NSRect) {
            let y = convert(NSPoint(x: 0, y: lineRect.minY + tv.textContainerOrigin.y), from: tv).y
            let label = "\(number)" as NSString
            let size = label.size(withAttributes: attrs)
            let point = NSPoint(x: ruleThickness - size.width - 7, y: y + (lineRect.height - size.height) / 2)
            label.draw(at: point, withAttributes: attrs)
        }

        while index < ns.length {
            let lineRange = ns.lineRange(for: NSRange(location: index, length: 0))
            let glyph = lm.glyphIndexForCharacter(at: index)
            let lineRect = lm.lineFragmentRect(forGlyphAt: glyph, effectiveRange: nil)
            if lineRect.minY > visible.maxY { return }
            draw(lineNumber, at: lineRect)
            lineNumber += 1
            index = NSMaxRange(lineRange)
            if lineRange.length == 0 { break }
        }
        if ns.length == 0 || ns.character(at: ns.length - 1) == 10 {
            draw(lineNumber, at: lm.extraLineFragmentRect)
        }
    }
}

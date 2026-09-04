import Foundation

/// A box record (`(`/`[`/`h`/`v`) from a SyncTeX file, in PDF points (bp)
/// measured from the top-left corner of the page.
public struct SyncTeXBox {
    public let page: Int
    public let tag: Int
    public let line: Int
    public let x: Double
    public let y: Double
    public let width: Double
    public let height: Double
    public let depth: Double
    public let isHorizontal: Bool
    public let nesting: Int

    public var minX: Double { min(x, x + width) }
    public var maxX: Double { max(x, x + width) }
    public var minY: Double { y - height }
    public var maxY: Double { y + depth }
    public var area: Double { abs(width) * (height + depth) }

    public func contains(_ px: Double, _ py: Double) -> Bool {
        px >= minX && px <= maxX && py >= minY && py <= maxY
    }

    /// Distance from a point to the box rectangle (0 when inside).
    public func distance(to px: Double, _ py: Double) -> (dx: Double, dy: Double) {
        let dx = px < minX ? minX - px : (px > maxX ? px - maxX : 0)
        let dy = py < minY ? minY - py : (py > maxY ? py - maxY : 0)
        return (dx, dy)
    }
}

/// A point record (`x`, `k`, `g`, `$`) attached to the innermost enclosing box.
public struct SyncTeXPoint {
    public let page: Int
    public let tag: Int
    public let line: Int
    public let x: Double
    public let y: Double
    public let boxIndex: Int?
}

public struct SyncTeXLocation: Equatable {
    public let file: URL
    public let line: Int
    public let tag: Int
}

/// Parser for the SyncTeX format (pdfTeX/XeTeX/LuaTeX) with a PDF→source
/// ("edit") query. Coordinates in the file are scaled points; they are
/// converted to bp so they line up with PDFKit's page space.
public final class SyncTeXScanner {
    public private(set) var inputs: [Int: String] = [:]
    public private(set) var boxes: [SyncTeXBox] = []
    public private(set) var points: [SyncTeXPoint] = []
    /// Indices of boxes that directly own point records (text lines, not layout containers).
    public private(set) var boxesWithPoints: Set<Int> = []
    public let baseURL: URL
    private var fileCache: [Int: URL?] = [:]

    private var unit: Double = 1
    private var magnification: Double = 1000
    private var xOffset: Double = 0
    private var yOffset: Double = 0

    /// 1 bp = 72.27/72 pt = 65781.76 sp.
    private var scale: Double { unit * (magnification / 1000.0) / 65781.76 }

    public init(text: String, baseURL: URL) {
        self.baseURL = baseURL
        parse(text)
    }

    public convenience init(fileURL: URL, baseURL: URL) throws {
        let text = try Gzip.readTextFile(at: fileURL)
        self.init(text: text, baseURL: baseURL)
    }

    /// `main.pdf` → `main.synctex.gz` or `main.synctex`, whichever exists.
    public static func locateFile(forPDF pdfURL: URL) -> URL? {
        let stem = pdfURL.deletingPathExtension()
        for ext in ["synctex.gz", "synctex"] {
            let u = stem.appendingPathExtension(ext)
            if FileManager.default.fileExists(atPath: u.path) { return u }
        }
        return nil
    }

    // MARK: - Parsing

    private func parse(_ text: String) {
        var inContent = false
        var page = 0
        var stack: [Int] = []

        for rawLine in text.split(separator: "\n", omittingEmptySubsequences: true) {
            let line = Substring(rawLine)
            if line.hasPrefix("Input:") {
                let rest = line.dropFirst("Input:".count)
                if let colon = rest.firstIndex(of: ":"), let tag = Int(rest[rest.startIndex..<colon]) {
                    inputs[tag] = String(rest[rest.index(after: colon)...])
                }
                continue
            }
            if !inContent {
                if line.hasPrefix("Magnification:") { magnification = Double(line.dropFirst(14)) ?? 1000 }
                else if line.hasPrefix("Unit:") { unit = Double(line.dropFirst(5)) ?? 1 }
                else if line.hasPrefix("X Offset:") { xOffset = Double(line.dropFirst(9)) ?? 0 }
                else if line.hasPrefix("Y Offset:") { yOffset = Double(line.dropFirst(9)) ?? 0 }
                else if line.hasPrefix("Content:") { inContent = true }
                continue
            }
            if line.hasPrefix("Postamble:") { break }
            guard let kind = line.first else { continue }
            let body = line.dropFirst()
            switch kind {
            case "{":
                page = Int(body) ?? page
                stack.removeAll()
            case "}":
                stack.removeAll()
            case "[", "(":
                if let box = parseBox(body, page: page, horizontal: kind == "(", nesting: stack.count) {
                    boxes.append(box)
                    stack.append(boxes.count - 1)
                } else {
                    // Keep nesting balanced even if a record is malformed.
                    stack.append(-1)
                }
            case "]", ")":
                if !stack.isEmpty { stack.removeLast() }
            case "h", "v":
                if let box = parseBox(body, page: page, horizontal: kind == "h", nesting: stack.count) {
                    boxes.append(box)
                }
            case "x", "k", "g", "$", "r":
                if let p = parsePoint(body, page: page, boxIndex: stack.last.flatMap { $0 >= 0 ? $0 : nil }) {
                    points.append(p)
                    if let b = p.boxIndex { boxesWithPoints.insert(b) }
                }
            default:
                continue // "!" offsets, "f" form refs, etc.
            }
        }
    }

    /// `tag,line:x,y:w,h,d`
    private func parseBox(_ body: Substring, page: Int, horizontal: Bool, nesting: Int) -> SyncTeXBox? {
        let parts = body.split(separator: ":", omittingEmptySubsequences: false)
        guard parts.count >= 3 else { return nil }
        let tl = parts[0].split(separator: ",")
        let xy = parts[1].split(separator: ",")
        let whd = parts[2].split(separator: ",")
        guard tl.count == 2, xy.count == 2, whd.count == 3,
              let tag = Int(tl[0]), let line = Int(tl[1]),
              let x = Double(xy[0]), let y = Double(xy[1]),
              let w = Double(whd[0]), let h = Double(whd[1]), let d = Double(whd[2])
        else { return nil }
        return SyncTeXBox(page: page, tag: tag, line: line,
                          x: (x + xOffset) * scale, y: (y + yOffset) * scale,
                          width: w * scale, height: h * scale, depth: d * scale,
                          isHorizontal: horizontal, nesting: nesting)
    }

    /// `tag,line:x,y[:w]`
    private func parsePoint(_ body: Substring, page: Int, boxIndex: Int?) -> SyncTeXPoint? {
        let parts = body.split(separator: ":", omittingEmptySubsequences: false)
        guard parts.count >= 2 else { return nil }
        let tl = parts[0].split(separator: ",")
        let xy = parts[1].split(separator: ",")
        guard tl.count == 2, xy.count == 2,
              let tag = Int(tl[0]), let line = Int(tl[1]),
              let x = Double(xy[0]), let y = Double(xy[1])
        else { return nil }
        return SyncTeXPoint(page: page, tag: tag, line: line,
                            x: (x + xOffset) * scale, y: (y + yOffset) * scale, boxIndex: boxIndex)
    }

    // MARK: - Queries

    /// Resolve an input tag to an existing regular file. pdfTeX writes empty
    /// paths for some inputs ("Input:7:") — those resolve to nil.
    public func fileURL(forTag tag: Int) -> URL? {
        if let cached = fileCache[tag] { return cached }
        let resolved = resolve(tag)
        fileCache[tag] = resolved
        return resolved
    }

    private func resolve(_ tag: Int) -> URL? {
        guard let raw = inputs[tag]?.trimmingCharacters(in: .whitespaces), !raw.isEmpty else { return nil }
        var candidates: [URL] = []
        if raw.hasPrefix("/") {
            candidates.append(URL(fileURLWithPath: raw))
        } else {
            candidates.append(baseURL.appendingPathComponent(raw))
        }
        if !raw.hasSuffix(".tex") {
            candidates.append(contentsOf: candidates.map { $0.appendingPathExtension("tex") })
        }
        for c in candidates {
            let std = c.standardizedFileURL
            var isDir: ObjCBool = false
            if FileManager.default.fileExists(atPath: std.path, isDirectory: &isDir), !isDir.boolValue { return std }
        }
        return nil
    }

    /// PDF → source. `page` is 1-based; `x`/`y` are bp from the page's top-left.
    ///
    /// Order of preference: the deepest text-line box under the point, then
    /// the nearest text-line box within `tolerance` (clicks between lines),
    /// then the deepest box of any kind under the point (figures, display
    /// math, layout containers). The line is refined with the closest point
    /// record inside the chosen box.
    public func editQuery(page: Int, x: Double, y: Double, tolerance: Double = 24) -> SyncTeXLocation? {
        // Text lines are short boxes owning glue/kern records; page-wide layout
        // boxes can own a stray kern too, so height is part of the test.
        func isTextLine(_ i: Int, _ b: SyncTeXBox) -> Bool {
            boxesWithPoints.contains(i) && b.height + b.depth <= 60 && b.width > 0
        }
        func deepestContaining(requirePoints: Bool) -> Int? {
            var best: Int? = nil
            var bestKey: (Int, Double) = (-1, .greatestFiniteMagnitude)
            for (i, b) in boxes.enumerated() where b.page == page && b.isHorizontal && b.contains(x, y) {
                if requirePoints && !isTextLine(i, b) { continue }
                let key = (b.nesting, -b.area)
                if key.0 > bestKey.0 || (key.0 == bestKey.0 && key.1 > bestKey.1) {
                    best = i
                    bestKey = key
                }
            }
            return best
        }
        func nearestLine() -> Int? {
            var best: Int? = nil
            var bestDist = Double.greatestFiniteMagnitude
            for (i, b) in boxes.enumerated() where b.page == page && b.isHorizontal && isTextLine(i, b) {
                let (dx, dy) = b.distance(to: x, y)
                guard dy <= tolerance, dx <= tolerance * 6 else { continue }
                let dist = dy * 4 + dx
                if dist < bestDist { bestDist = dist; best = i }
            }
            return best
        }

        guard let idx = deepestContaining(requirePoints: true) ?? nearestLine() ?? deepestContaining(requirePoints: false) else {
            return nil
        }
        let box = boxes[idx]
        // Closest point record with a resolvable file wins; else the box itself.
        var chosen: (tag: Int, line: Int)? = nil
        var nearest = Double.greatestFiniteMagnitude
        for p in points where p.boxIndex == idx {
            let d = abs(p.x - x)
            if d < nearest, fileURL(forTag: p.tag) != nil {
                nearest = d
                chosen = (p.tag, p.line)
            }
        }
        if chosen == nil, fileURL(forTag: box.tag) != nil {
            chosen = (box.tag, box.line)
        }
        guard let c = chosen, let file = fileURL(forTag: c.tag) else { return nil }
        return SyncTeXLocation(file: file, line: c.line, tag: c.tag)
    }
}

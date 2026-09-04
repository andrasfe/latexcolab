// Renders a simple app icon (rounded square with "TeX") into an .icns.
// Usage: swift make-icon.swift <output.icns>
import AppKit

let out = CommandLine.arguments.dropFirst().first ?? "AppIcon.icns"
let iconset = FileManager.default.temporaryDirectory.appendingPathComponent("LaTeXColab-\(UUID().uuidString).iconset")
try! FileManager.default.createDirectory(at: iconset, withIntermediateDirectories: true)

func render(_ size: Int) -> NSImage {
    let s = CGFloat(size)
    let img = NSImage(size: NSSize(width: s, height: s))
    img.lockFocus()
    let inset = s * 0.06
    let rect = NSRect(x: inset, y: inset, width: s - 2 * inset, height: s - 2 * inset)
    let path = NSBezierPath(roundedRect: rect, xRadius: s * 0.22, yRadius: s * 0.22)
    NSGradient(colors: [NSColor(calibratedRed: 0.05, green: 0.42, blue: 0.55, alpha: 1),
                        NSColor(calibratedRed: 0.02, green: 0.22, blue: 0.36, alpha: 1)])!
        .draw(in: path, angle: -90)
    // Page
    let page = NSRect(x: s * 0.24, y: s * 0.18, width: s * 0.52, height: s * 0.64)
    NSColor.white.withAlphaComponent(0.92).setFill()
    NSBezierPath(roundedRect: page, xRadius: s * 0.03, yRadius: s * 0.03).fill()
    // Text lines
    NSColor(calibratedWhite: 0.55, alpha: 1).setFill()
    for i in 0..<4 {
        let y = page.maxY - s * 0.14 - CGFloat(i) * s * 0.09
        let w = i == 3 ? page.width * 0.45 : page.width * 0.7
        NSBezierPath(roundedRect: NSRect(x: page.minX + s * 0.08, y: y, width: w, height: s * 0.035), xRadius: s * 0.01, yRadius: s * 0.01).fill()
    }
    // "TeX"
    let attrs: [NSAttributedString.Key: Any] = [
        .font: NSFont.systemFont(ofSize: s * 0.22, weight: .heavy),
        .foregroundColor: NSColor(calibratedRed: 0.05, green: 0.42, blue: 0.55, alpha: 1),
    ]
    let str = "TeX" as NSString
    let sz = str.size(withAttributes: attrs)
    str.draw(at: NSPoint(x: page.midX - sz.width / 2, y: page.minY + s * 0.05), withAttributes: attrs)
    img.unlockFocus()
    return img
}

for (size, scale) in [(16, 1), (16, 2), (32, 1), (32, 2), (128, 1), (128, 2), (256, 1), (256, 2), (512, 1), (512, 2)] {
    let px = size * scale
    let img = render(px)
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: px, pixelsHigh: px, bitsPerSample: 8,
                               samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                               colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    rep.size = NSSize(width: px, height: px)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
    img.draw(in: NSRect(x: 0, y: 0, width: px, height: px))
    NSGraphicsContext.restoreGraphicsState()
    let name = scale == 1 ? "icon_\(size)x\(size).png" : "icon_\(size)x\(size)@2x.png"
    try! rep.representation(using: .png, properties: [:])!.write(to: iconset.appendingPathComponent(name))
}
let p = Process()
p.executableURL = URL(fileURLWithPath: "/usr/bin/iconutil")
p.arguments = ["-c", "icns", iconset.path, "-o", out]
try! p.run()
p.waitUntilExit()
try? FileManager.default.removeItem(at: iconset)
exit(p.terminationStatus)

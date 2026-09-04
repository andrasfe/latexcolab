import Foundation

/// One entry of the project file tree. `id` is the project-relative path.
public struct FileNode: Identifiable, Hashable {
    public let id: String
    public let name: String
    public let url: URL
    public let isDirectory: Bool
    public let children: [FileNode]?

    public init(id: String, name: String, url: URL, isDirectory: Bool, children: [FileNode]?) {
        self.id = id
        self.name = name
        self.url = url
        self.isDirectory = isDirectory
        self.children = children
    }

    public var relativePath: String { id }
    public var fileExtension: String { (name as NSString).pathExtension.lowercased() }
}

public enum FileTree {
    /// Directories that never show up in the tree or the zip.
    public static let excludedDirectories: Set<String> = [
        ".git", "__pycache__", ".venv", "node_modules", ".idea", ".vscode",
    ]
    /// LaTeX build artifacts hidden from the tree and left out of the zip.
    public static let excludedSuffixes: [String] = [
        ".aux", ".log", ".out", ".toc", ".fls", ".fdb_latexmk",
        ".synctex.gz", ".synctex", ".bbl", ".blg", ".nav", ".snm", ".vrb",
    ]
    public static let excludedFiles: Set<String> = [".DS_Store"]

    public static let textExtensions: Set<String> = [
        "tex", "bib", "cls", "sty", "md", "txt", "json", "yaml", "yml",
        "toml", "cfg", "ini", "gitignore", "sh", "py", "js", "ts", "html",
        "css", "csv", "tsv", "bst", "def", "clo", "ltx", "dtx",
    ]
    static let textFileNames: Set<String> = [".gitignore", ".gitattributes", "Makefile", "latexmkrc", ".latexmkrc"]

    public static func isExcluded(fileName: String) -> Bool {
        if excludedFiles.contains(fileName) { return true }
        return excludedSuffixes.contains { fileName.hasSuffix($0) }
    }

    public static func isTextFile(named name: String) -> Bool {
        if textFileNames.contains(name) { return true }
        return textExtensions.contains((name as NSString).pathExtension.lowercased())
    }

    public static func build(root: URL) -> [FileNode] {
        buildChildren(of: root, root: root)
    }

    private static func buildChildren(of directory: URL, root: URL) -> [FileNode] {
        let fm = FileManager.default
        guard let items = try? fm.contentsOfDirectory(
            at: directory,
            includingPropertiesForKeys: [.isDirectoryKey],
            options: []
        ) else { return [] }

        var nodes: [FileNode] = []
        for item in items {
            let isDir = (try? item.resourceValues(forKeys: [.isDirectoryKey]).isDirectory) ?? false
            let name = item.lastPathComponent
            let rel = relativePath(of: item, root: root)
            if isDir {
                if excludedDirectories.contains(name) { continue }
                nodes.append(FileNode(id: rel, name: name, url: item, isDirectory: true,
                                      children: buildChildren(of: item, root: root)))
            } else {
                if isExcluded(fileName: name) { continue }
                nodes.append(FileNode(id: rel, name: name, url: item, isDirectory: false, children: nil))
            }
        }
        nodes.sort { a, b in
            if a.isDirectory != b.isDirectory { return a.isDirectory }
            return a.name.localizedCaseInsensitiveCompare(b.name) == .orderedAscending
        }
        return nodes
    }

    public static func relativePath(of url: URL, root: URL) -> String {
        let rootPath = root.standardizedFileURL.path
        let path = url.standardizedFileURL.path
        if path == rootPath { return "" }
        if path.hasPrefix(rootPath + "/") {
            return String(path.dropFirst(rootPath.count + 1))
        }
        return url.lastPathComponent
    }

    /// Depth-first list of every file node (directories excluded).
    public static func flattenFiles(_ nodes: [FileNode]) -> [FileNode] {
        var out: [FileNode] = []
        for n in nodes {
            if n.isDirectory {
                out.append(contentsOf: flattenFiles(n.children ?? []))
            } else {
                out.append(n)
            }
        }
        return out
    }

    public static func find(_ path: String, in nodes: [FileNode]) -> FileNode? {
        for n in nodes {
            if n.id == path { return n }
            if let c = n.children, let hit = find(path, in: c) { return hit }
        }
        return nil
    }
}

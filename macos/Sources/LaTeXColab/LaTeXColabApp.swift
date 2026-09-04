import AppKit
import SwiftUI
import LaTeXColabCore

@main
struct LaTeXColabApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var model = AppModel()

    var body: some Scene {
        WindowGroup("LaTeX Colab") {
            MainView()
                .environmentObject(model)
                .frame(minWidth: 900, minHeight: 560)
        }
        .defaultSize(width: 1320, height: 860)
        .commands {
            CommandGroup(replacing: .newItem) {
                Button("Open Folder…") { model.chooseProjectFolder() }
                    .keyboardShortcut("o")
                Button("Export Zip…") { model.exportZip() }
                    .keyboardShortcut("e", modifiers: [.command, .shift])
                    .disabled(model.projectURL == nil)
            }
            CommandGroup(replacing: .saveItem) {
                Button("Save") { model.saveEditor() }
                    .keyboardShortcut("s")
                    .disabled(model.editorPath == nil)
            }
            CommandMenu("Build") {
                Button("Regenerate PDF") { model.regenerate() }
                    .keyboardShortcut("r")
                    .disabled(model.projectURL == nil || model.isCompiling)
                Button("Refresh File Tree") { model.reloadTree() }
                    .keyboardShortcut("r", modifiers: [.command, .shift])
                    .disabled(model.projectURL == nil)
            }
            CommandMenu("Git") {
                GitMenuItems(withShortcuts: true).environmentObject(model)
            }
            CommandGroup(after: .toolbar) {
                Divider()
                Button("Show PDF") { model.viewMode = .pdf }.keyboardShortcut("1")
                Button("Show Editor") { model.viewMode = .editor }.keyboardShortcut("2")
                Button("Toggle PDF / Editor") { model.toggleViewMode() }.keyboardShortcut("e")
                Divider()
                Button(model.showLog ? "Hide Build Log" : "Show Build Log") { model.showLog.toggle() }
                    .keyboardShortcut("l")
                Divider()
                Button("Paragraph Editor") { model.paragraphWindowRequest += 1 }
                    .keyboardShortcut("p", modifiers: [.command, .shift])
            }
        }

        Window("Paragraph Editor", id: ParagraphEditorWindow.id) {
            ParagraphEditorWindow()
                .environmentObject(model)
        }
        .defaultSize(width: 1120, height: 660)

        Settings {
            SettingsView()
                .environmentObject(model)
        }
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        // Makes `swift run` (no bundle) behave like a normal app: Dock icon, menu bar, focus.
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }
}

import SwiftUI

struct LogPanelView: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Label("Build log", systemImage: "terminal")
                    .font(.caption.bold())
                    .foregroundStyle(.secondary)
                Spacer()
                Button("Clear") { model.log = "" }
                    .buttonStyle(.borderless)
                    .font(.caption)
                Button { model.showLog = false } label: { Image(systemName: "xmark") }
                    .buttonStyle(.borderless)
                    .help("Hide log (⌘L)")
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 5)
            Divider()
            ScrollViewReader { proxy in
                ScrollView {
                    Text(model.log.isEmpty ? "No output yet." : model.log)
                        .font(.system(size: 11, design: .monospaced))
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(8)
                    Color.clear.frame(height: 1).id("bottom")
                }
                .onChange(of: model.log) { _, _ in
                    proxy.scrollTo("bottom", anchor: .bottom)
                }
            }
        }
        .background(Color(nsColor: .textBackgroundColor))
    }
}

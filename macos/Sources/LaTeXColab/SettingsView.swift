import SwiftUI
import LaTeXColabCore

struct SettingsView: View {
    @EnvironmentObject var model: AppModel
    @State private var url = ""
    @State private var modelID = ""
    @State private var temperature = 0.2
    @State private var maxWords = 20.0
    @State private var autoRegen = true
    @State private var loaded = false
    @State private var refreshing = false

    var body: some View {
        Form {
            Section("LM Studio") {
                TextField("Server URL", text: $url, prompt: Text(LMStudioService.defaultBaseURL))
                    .onSubmit { Task { await refresh() } }
                HStack {
                    Picker("Model", selection: $modelID) {
                        Text("Whatever is loaded (automatic)").tag("")
                        if !modelID.isEmpty, !model.lmModels.contains(where: { $0.id == modelID }) {
                            Text(modelID).tag(modelID)
                        }
                        ForEach(model.lmModels.filter { $0.isChatModel }) { m in
                            Text(m.isLoaded ? "\(m.id) · loaded" : m.id).tag(m.id)
                        }
                    }
                    Button {
                        Task { await refresh() }
                    } label: {
                        if refreshing { ProgressView().controlSize(.small) } else { Image(systemName: "arrow.clockwise") }
                    }
                    .help("Reload the model list from LM Studio")
                }
                if let err = model.lmModelsError {
                    Text(err).font(.caption).foregroundStyle(.red)
                } else if !model.lmModels.isEmpty {
                    Text("\(model.lmModels.filter { $0.isChatModel }.count) chat models · \(model.lmModels.filter { $0.isLoaded }.count) loaded")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Slider(value: $temperature, in: 0...1, step: 0.05) {
                    Text("Temperature \(temperature, format: .number.precision(.fractionLength(2)))")
                }
                Text("Requests go to \(url.isEmpty ? LMStudioService.defaultBaseURL : url)/v1/chat/completions. No API key is needed.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Section("Paragraph editing") {
                Slider(value: $maxWords, in: 0...100, step: 1) {
                    Text("Default max words to change: \(Int(maxWords))")
                }
                Toggle("Regenerate the PDF after Apply", isOn: $autoRegen)
                Text("Drafts live in \(model.editsFileName) inside the project folder. Apply writes them into the .tex file.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Section("Build") {
                LabeledContent("LaTeX engine", value: model.engineName ?? "not found")
                LabeledContent("Main file", value: model.mainFile)
                LabeledContent("Settings file", value: AppConfig.fileURL.path)
            }
        }
        .formStyle(.grouped)
        .frame(width: 560)
        .onAppear {
            guard !loaded else { return }
            url = model.config.lmStudioURL
            modelID = model.config.model
            temperature = model.config.temperature
            maxWords = Double(model.config.defaultMaxWords)
            autoRegen = model.config.autoRegenerateAfterApply
            loaded = true
            Task { await refresh() }
        }
        .onChange(of: url) { _, _ in save() }
        .onChange(of: modelID) { _, _ in save() }
        .onChange(of: temperature) { _, _ in save() }
        .onChange(of: maxWords) { _, _ in save() }
        .onChange(of: autoRegen) { _, _ in save() }
    }

    private func save() {
        guard loaded else { return }
        model.config.lmStudioURL = url.isEmpty ? LMStudioService.defaultBaseURL : url
        model.config.model = modelID
        model.config.temperature = temperature
        model.config.defaultMaxWords = Int(maxWords)
        model.config.autoRegenerateAfterApply = autoRegen
        model.saveConfig()
    }

    private func refresh() async {
        refreshing = true
        save()
        await model.refreshLMModels()
        refreshing = false
    }
}

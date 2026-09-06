import Foundation

/// A model as reported by LM Studio's local server.
public struct LMModel: Identifiable, Equatable, Hashable {
    public let id: String
    public let type: String   // llm / vlm / embeddings / unknown
    public let state: String  // loaded / not-loaded / unknown

    public init(id: String, type: String, state: String) {
        self.id = id
        self.type = type
        self.state = state
    }

    public var isLoaded: Bool { state == "loaded" }
    public var isChatModel: Bool {
        type != "embeddings" && !id.lowercased().contains("embed")
    }
}

public struct CleanupRequest {
    public var paragraph: String
    public var maxWords: Int
    public var instructions: String
    public var model: String
    public var temperature: Double

    public init(paragraph: String, maxWords: Int, instructions: String, model: String, temperature: Double = 0.2) {
        self.paragraph = paragraph
        self.maxWords = maxWords
        self.instructions = instructions
        self.model = model
        self.temperature = temperature
    }
}

public struct CleanupResult {
    public let text: String
    public let model: String
    public let promptTokens: Int
    public let completionTokens: Int
}

public enum LMStudioError: Error, LocalizedError {
    case unreachable(String, String)
    case badStatus(Int, String)
    case emptyResponse
    case exhaustedThinking
    case invalidJSON
    case noModel

    public var errorDescription: String? {
        switch self {
        case .unreachable(let url, let why):
            return "LM Studio is not reachable at \(url) (\(why)). In LM Studio open the Developer tab and start the local server."
        case .badStatus(let code, let msg):
            return "LM Studio returned HTTP \(code): \(msg)"
        case .emptyResponse:
            return "LM Studio returned an empty response."
        case .exhaustedThinking:
            return "The model spent its whole output budget on reasoning and produced no text. Pick a non-thinking model or a smaller paragraph, or lower the model's reasoning effort in LM Studio."
        case .invalidJSON:
            return "Could not parse the LM Studio response."
        case .noModel:
            return "No chat model is available in LM Studio. Load one in LM Studio or pick one in Settings (⌘,)."
        }
    }
}

/// Client for LM Studio's OpenAI-compatible local server.
public final class LMStudioService {
    public static let defaultBaseURL = "http://127.0.0.1:1234"

    public let baseURL: URL
    let session: URLSession

    public init(baseURL: String, session: URLSession = .shared) {
        var s = baseURL.trimmingCharacters(in: .whitespacesAndNewlines)
        if s.isEmpty { s = LMStudioService.defaultBaseURL }
        if !s.contains("://") { s = "http://" + s }
        while s.hasSuffix("/") { s.removeLast() }
        if s.hasSuffix("/v1") { s.removeLast(3) }
        self.baseURL = URL(string: s) ?? URL(string: LMStudioService.defaultBaseURL)!
        self.session = session
    }

    // MARK: - Prompt

    public static let systemPrompt = """
    You are a meticulous copy editor working directly on LaTeX source. You receive exactly one paragraph of LaTeX and you reply with the revised paragraph and nothing else: no explanations, no preamble, no code fences, no quotation marks around the result, no <paragraph> tags.

    Rules:
    - Preserve every LaTeX command, macro, math expression, citation (\\cite…), reference (\\ref, \\eqref, \\cref…), label, and environment exactly, unless it is syntactically broken and you are fixing the syntax.
    - Keep the author's meaning and voice. Do not add or remove sentences unless the instructions ask for it.
    - Keep the paragraph about the same length and keep line breaks close to the input so that diffs stay small.
    - If the paragraph needs no changes, return it unchanged.
    """

    public static func userPrompt(for req: CleanupRequest) -> String {
        var p = "Clean up this paragraph but change not more than \(req.maxWords) words, plus punctuation and LaTeX syntax corrections."
        if req.maxWords == 0 {
            p += " Do not change, add, or remove any words at all; only fix punctuation and LaTeX syntax."
        } else {
            p += " Every added, removed, or replaced word counts toward the limit of \(req.maxWords)."
        }
        let extra = req.instructions.trimmingCharacters(in: .whitespacesAndNewlines)
        if !extra.isEmpty {
            p += "\n\nAdditional instructions from the author: \(extra)"
        }
        p += "\n\nParagraph:\n<paragraph>\n\(req.paragraph)\n</paragraph>\n\nReply with only the revised paragraph."
        return p
    }

    /// Strip reasoning blocks, fences, wrapper tags and chatty lead-ins that
    /// local models tend to add despite instructions.
    public static func extractParagraph(from raw: String) -> String {
        var s = raw
        if let re = try? NSRegularExpression(pattern: #"<think>[\s\S]*?</think>"#) {
            s = re.stringByReplacingMatches(in: s, range: NSRange(s.startIndex..., in: s), withTemplate: "")
        }
        s = s.trimmingCharacters(in: .whitespacesAndNewlines)
        if s.hasPrefix("```") {
            if let nl = s.firstIndex(of: "\n") { s = String(s[s.index(after: nl)...]) } else { s = "" }
            if s.hasSuffix("```") { s = String(s.dropLast(3)) }
            s = s.trimmingCharacters(in: .whitespacesAndNewlines)
        }
        if s.hasPrefix("<paragraph>") { s = String(s.dropFirst("<paragraph>".count)) }
        if s.hasSuffix("</paragraph>") { s = String(s.dropLast("</paragraph>".count)) }
        s = s.trimmingCharacters(in: .whitespacesAndNewlines)
        // "Here is the revised paragraph:" followed by a blank line.
        let parts = s.components(separatedBy: "\n\n")
        if parts.count >= 2, let first = parts.first,
           first.count < 90, first.hasSuffix(":"), !first.contains("\\") {
            s = parts.dropFirst().joined(separator: "\n\n").trimmingCharacters(in: .whitespacesAndNewlines)
        }
        return s
    }

    // MARK: - HTTP

    private func request(path: String, body: [String: Any]? = nil, timeout: TimeInterval) throws -> URLRequest {
        var r = URLRequest(url: baseURL.appendingPathComponent(path))
        r.timeoutInterval = timeout
        r.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if let body {
            r.httpMethod = "POST"
            r.httpBody = try JSONSerialization.data(withJSONObject: body)
        }
        return r
    }

    private func send(_ req: URLRequest) async throws -> [String: Any] {
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: req)
        } catch {
            throw LMStudioError.unreachable(baseURL.absoluteString, error.localizedDescription)
        }
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        guard status == 200 else {
            var msg = String(decoding: data.prefix(600), as: UTF8.self)
            if let err = obj?["error"] {
                if let d = err as? [String: Any], let m = d["message"] as? String { msg = m }
                else if let s = err as? String { msg = s }
            }
            throw LMStudioError.badStatus(status, msg)
        }
        guard let obj else { throw LMStudioError.invalidJSON }
        return obj
    }

    /// Models known to LM Studio. Prefers `/api/v0/models` (has type and load
    /// state); falls back to the OpenAI-style `/v1/models`.
    public func listModels() async throws -> [LMModel] {
        if let v0 = try? await send(request(path: "api/v0/models", timeout: 10)),
           let data = v0["data"] as? [[String: Any]] {
            return data.compactMap { d in
                guard let id = d["id"] as? String else { return nil }
                return LMModel(id: id, type: d["type"] as? String ?? "unknown", state: d["state"] as? String ?? "unknown")
            }
        }
        let v1 = try await send(request(path: "v1/models", timeout: 10))
        let data = v1["data"] as? [[String: Any]] ?? []
        return data.compactMap { d in
            (d["id"] as? String).map { LMModel(id: $0, type: "unknown", state: "unknown") }
        }
    }

    /// Picks a model when none is configured: a loaded chat model first, then any chat model.
    public static func pickModel(from models: [LMModel]) -> String? {
        let chat = models.filter { $0.isChatModel }
        return (chat.first { $0.isLoaded } ?? chat.first)?.id
    }

    public func cleanUp(_ req: CleanupRequest) async throws -> CleanupResult {
        guard !req.model.isEmpty else { throw LMStudioError.noModel }
        let body: [String: Any] = [
            "model": req.model,
            "messages": [
                ["role": "system", "content": LMStudioService.systemPrompt],
                ["role": "user", "content": LMStudioService.userPrompt(for: req)],
            ],
            "temperature": req.temperature,
            "max_tokens": 8192,
            "stream": false,
        ]
        let obj = try await sendChat(body)
        return try LMStudioService.parseResponse(obj)
    }

    /// POST a chat-completion body to LM Studio and return the decoded JSON.
    func sendChat(_ body: [String: Any]) async throws -> [String: Any] {
        try await send(request(path: "v1/chat/completions", body: body, timeout: 600))
    }

    static func parseResponse(_ obj: [String: Any]) throws -> CleanupResult {
        let choices = obj["choices"] as? [[String: Any]] ?? []
        let message = choices.first?["message"] as? [String: Any]
        var content = ""
        if let s = message?["content"] as? String {
            content = s
        } else if let parts = message?["content"] as? [[String: Any]] {
            content = parts.compactMap { $0["text"] as? String }.joined()
        }
        let cleaned = extractParagraph(from: content)
        guard !cleaned.isEmpty else {
            // Reasoning models can burn the whole budget on thinking; say so.
            if let reasoning = message?["reasoning_content"] as? String, !reasoning.isEmpty {
                throw LMStudioError.exhaustedThinking
            }
            throw LMStudioError.emptyResponse
        }
        let usage = obj["usage"] as? [String: Any] ?? [:]
        return CleanupResult(text: cleaned,
                             model: obj["model"] as? String ?? "",
                             promptTokens: usage["prompt_tokens"] as? Int ?? 0,
                             completionTokens: usage["completion_tokens"] as? Int ?? 0)
    }
}

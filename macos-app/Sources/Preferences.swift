import AppKit

/// User-configurable state. The only thing the app needs to remember is where the
/// Second Brain vault lives on disk — the folder that contains `dashboard/bridge.py`.
enum Preferences {
    private static let vaultKey = "vaultPath"

    /// Fixed port the bridge binds on 127.0.0.1 (matches `run.sh` / bridge default).
    static let port = 4173

    private static let pageZoomKey = "pageZoom"

    /// Persisted dashboard zoom factor for the native web view (1.0 == 100%).
    static var pageZoom: Double {
        get {
            let v = UserDefaults.standard.double(forKey: pageZoomKey)
            return v > 0 ? v : 1.0   // default 100% when unset
        }
        set { UserDefaults.standard.set(newValue, forKey: pageZoomKey) }
    }

    static var vaultURL: URL? {
        get {
            guard let p = UserDefaults.standard.string(forKey: vaultKey) else { return nil }
            return URL(fileURLWithPath: p)
        }
        set { UserDefaults.standard.set(newValue?.path, forKey: vaultKey) }
    }

    /// A folder is a valid vault only if it contains `dashboard/bridge.py`.
    static func isValidVault(_ url: URL) -> Bool {
        FileManager.default.fileExists(
            atPath: url.appendingPathComponent("dashboard/bridge.py").path)
    }

    /// Returns a valid vault, prompting the user if the stored one is missing/invalid.
    /// Must be called on the main thread (may present an open panel). `nil` if cancelled.
    static func resolveVault() -> URL? {
        if let u = vaultURL, isValidVault(u) { return u }
        return promptForVault()
    }

    /// Presents a folder picker until the user chooses a valid vault or cancels.
    /// Main thread only.
    @discardableResult
    static func promptForVault() -> URL? {
        while true {
            let panel = NSOpenPanel()
            panel.title = "Choose your Second Brain vault folder"
            panel.message = "Select the SecondBrain repo folder (the one containing dashboard/bridge.py)."
            panel.prompt = "Use This Folder"
            panel.canChooseDirectories = true
            panel.canChooseFiles = false
            panel.allowsMultipleSelection = false

            let dev = FileManager.default.homeDirectoryForCurrentUser
                .appendingPathComponent("Development/SecondBrain")
            if FileManager.default.fileExists(atPath: dev.path) {
                panel.directoryURL = dev
            }

            guard panel.runModal() == .OK, let url = panel.url else { return nil }

            if isValidVault(url) {
                vaultURL = url
                return url
            }

            let alert = NSAlert()
            alert.messageText = "That folder isn't a Second Brain vault"
            alert.informativeText =
                "No dashboard/bridge.py was found in “\(url.lastPathComponent)”. "
                + "Pick the repository root instead."
            alert.addButton(withTitle: "Try Again")
            alert.addButton(withTitle: "Cancel")
            if alert.runModal() != .alertFirstButtonReturn { return nil }
        }
    }

    // MARK: - Engine (claude | codex)

    private static let engineKey = "engine"

    static func isValidEngine(_ s: String) -> Bool { s == "claude" || s == "codex" }

    /// The user's explicit engine choice from the app menu, or `nil` to defer to the
    /// vault's `.env` / the bridge default. Only a non-nil value is injected into the
    /// bridge's environment.
    static var engineChoice: String? {
        get {
            guard let s = UserDefaults.standard.string(forKey: engineKey),
                  isValidEngine(s) else { return nil }
            return s
        }
        set { UserDefaults.standard.set(newValue, forKey: engineKey) }
    }

    /// The engine that will actually back the bridge, for driving the menu checkmark:
    /// explicit choice → vault `.env` `AGENT_ENGINE` → `"claude"`. This mirrors the
    /// bridge's own resolution so the checkmark matches reality even when we don't
    /// inject an override.
    static func effectiveEngine() -> String {
        if let c = engineChoice { return c }
        if let e = envFileEngine(), isValidEngine(e) { return e }
        return "claude"
    }

    /// Best-effort read of `AGENT_ENGINE` from `<vault>/.env`. `nil` on any failure.
    private static func envFileEngine() -> String? {
        guard let vault = vaultURL,
              let text = try? String(contentsOf: vault.appendingPathComponent(".env"),
                                     encoding: .utf8) else { return nil }
        for raw in text.split(whereSeparator: \.isNewline) {
            let line = raw.trimmingCharacters(in: .whitespaces)
            if line.isEmpty || line.hasPrefix("#") { continue }
            guard let eq = line.firstIndex(of: "=") else { continue }
            if line[..<eq].trimmingCharacters(in: .whitespaces) == "AGENT_ENGINE" {
                return line[line.index(after: eq)...]
                    .trimmingCharacters(in: .whitespaces).lowercased()
            }
        }
        return nil
    }
}

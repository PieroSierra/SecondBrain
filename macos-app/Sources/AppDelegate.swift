import AppKit

final class AppDelegate: NSObject, NSApplicationDelegate {

    private var claudeItem: NSMenuItem?
    private var codexItem: NSMenuItem?

    func applicationDidFinishLaunching(_ notification: Notification) {
        buildMenu()
        launchBridge()
    }

    // Closing the window keeps the app (and bridge) alive → Dock click can reopen it.
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        false
    }

    // Clicking the Dock icon while no window is visible reopens the dashboard.
    func applicationShouldHandleReopen(_ sender: NSApplication,
                                       hasVisibleWindows flag: Bool) -> Bool {
        WebWindow.shared.show()
        return true
    }

    // Quit (Cmd-Q) → kill the bridge we started.
    func applicationWillTerminate(_ notification: Notification) {
        BridgeController.shared.stop()
    }

    // MARK: - Bridge startup

    private func launchBridge() {
        BridgeController.shared.start(
            ready: { [weak self] in
                WebWindow.shared.load()
                WebWindow.shared.show()
                self?.updateEngineChecks()
            },
            failed: { [weak self] message in
                self?.handleStartFailure(message)
            })
    }

    private func handleStartFailure(_ message: String) {
        let alert = NSAlert()
        alert.alertStyle = .critical
        alert.messageText = "Second Brain couldn’t start"
        alert.informativeText = message
        alert.addButton(withTitle: "Choose Vault…")
        alert.addButton(withTitle: "Quit")
        if alert.runModal() == .alertFirstButtonReturn {
            if Preferences.promptForVault() != nil {
                launchBridge()
            } else {
                NSApp.terminate(nil)
            }
        } else {
            NSApp.terminate(nil)
        }
    }

    @objc private func chooseVault(_ sender: Any?) {
        // Re-pick the vault, then relaunch the bridge against it.
        guard Preferences.promptForVault() != nil else { return }
        BridgeController.shared.stop()
        launchBridge()
    }

    // MARK: - Engine selection

    @objc private func selectClaude(_ sender: Any?) { selectEngine("claude") }
    @objc private func selectCodex(_ sender: Any?) { selectEngine("codex") }

    private func engineLabel(_ engine: String) -> String {
        engine == "codex" ? "Codex" : "Claude Code"
    }

    /// Restart the bridge under `engine` and reload the dashboard. No-op if already
    /// active. On failure, revert to the previous engine.
    private func selectEngine(_ engine: String) {
        let previous = Preferences.effectiveEngine()
        guard engine != previous else { updateEngineChecks(); return }

        BridgeController.shared.switchEngine(
            to: engine,
            ready: { [weak self] in
                WebWindow.shared.load()
                self?.updateEngineChecks()
            },
            failed: { [weak self] message in
                guard let self else { return }
                let alert = NSAlert()
                alert.alertStyle = .warning
                alert.messageText = "Couldn’t switch to \(self.engineLabel(engine))"
                alert.informativeText = "\(message)\n\nReverting to \(self.engineLabel(previous))."
                alert.runModal()
                BridgeController.shared.switchEngine(
                    to: previous,
                    ready: { [weak self] in
                        WebWindow.shared.load()
                        self?.updateEngineChecks()
                    },
                    failed: { _ in })
            })
        updateEngineChecks() // optimistic; corrected by the callbacks
    }

    private func updateEngineChecks() {
        let active = Preferences.effectiveEngine()
        claudeItem?.state = (active == "claude") ? .on : .off
        codexItem?.state = (active == "codex") ? .on : .off
    }

    // MARK: - Menu

    /// A minimal programmatic main menu (no nib): App, File, Edit, Window.
    private func buildMenu() {
        let mainMenu = NSMenu()

        // App menu
        let appItem = NSMenuItem()
        mainMenu.addItem(appItem)
        let appMenu = NSMenu()
        appItem.submenu = appMenu
        appMenu.addItem(withTitle: "About Second Brain",
                        action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)),
                        keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Choose Vault…",
                        action: #selector(chooseVault(_:)), keyEquivalent: "")
            .target = self
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Hide Second Brain",
                        action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        appMenu.addItem(withTitle: "Quit Second Brain",
                        action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")

        // File menu (Reload)
        let fileItem = NSMenuItem()
        mainMenu.addItem(fileItem)
        let fileMenu = NSMenu(title: "File")
        fileItem.submenu = fileMenu
        fileMenu.addItem(withTitle: "Reload Dashboard",
                         action: #selector(WebWindow.reload), keyEquivalent: "r")
            .target = WebWindow.shared

        // Edit menu (so copy/paste work inside the web view)
        let editItem = NSMenuItem()
        mainMenu.addItem(editItem)
        let editMenu = NSMenu(title: "Edit")
        editItem.submenu = editMenu
        editMenu.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
        editMenu.addItem(withTitle: "Redo", action: Selector(("redo:")), keyEquivalent: "Z")
        editMenu.addItem(.separator())
        editMenu.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        editMenu.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editMenu.addItem(withTitle: "Select All",
                         action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")

        // Engine menu (claude | codex), radio-style checkmarks.
        let engineItem = NSMenuItem()
        mainMenu.addItem(engineItem)
        let engineMenu = NSMenu(title: "Engine")
        engineItem.submenu = engineMenu
        let ci = engineMenu.addItem(withTitle: "Claude Code",
                                    action: #selector(selectClaude(_:)), keyEquivalent: "")
        ci.target = self
        let xi = engineMenu.addItem(withTitle: "Codex",
                                    action: #selector(selectCodex(_:)), keyEquivalent: "")
        xi.target = self
        claudeItem = ci
        codexItem = xi
        updateEngineChecks()

        // Window menu
        let windowItem = NSMenuItem()
        mainMenu.addItem(windowItem)
        let windowMenu = NSMenu(title: "Window")
        windowItem.submenu = windowMenu
        windowMenu.addItem(withTitle: "Minimize",
                           action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        windowMenu.addItem(withTitle: "Close",
                           action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        NSApp.windowsMenu = windowMenu

        NSApp.mainMenu = mainMenu
    }
}

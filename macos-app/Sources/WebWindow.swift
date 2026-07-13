import AppKit
import WebKit

/// The single dashboard window: an `NSWindow` hosting a `WKWebView` pointed at the
/// bridge. Closing the red button hides the window (the app keeps running so a Dock
/// click brings it back) rather than tearing down the web view and its state.
final class WebWindow: NSObject, NSWindowDelegate {
    static let shared = WebWindow()

    private(set) var window: NSWindow?
    private var webView: WKWebView?

    /// Discrete browser-like zoom stops, so each ⌘+/⌘- press is a familiar step.
    private let zoomStops: [Double] = [0.5, 0.67, 0.8, 0.9, 1.0, 1.1, 1.25, 1.5, 1.75, 2.0]

    private override init() { super.init() }

    private func makeWindowIfNeeded() {
        guard window == nil else { return }

        let frame = NSRect(x: 0, y: 0, width: 1200, height: 820)
        // A "secondBrain" script-message channel lets the dashboard drive native
        // actions (e.g. the in-page engine dropdown). Present only in the app, so
        // the web page can feature-detect it and fall back to a static label in a
        // plain browser.
        let config = WKWebViewConfiguration()
        let contentController = WKUserContentController()
        contentController.add(WeakScriptMessageHandler(self), name: "secondBrain")
        config.userContentController = contentController
        let wv = WKWebView(frame: frame, configuration: config)
        wv.autoresizingMask = [.width, .height]
        if #available(macOS 11.0, *) { wv.pageZoom = Preferences.pageZoom }
        wv.uiDelegate = self       // so <input type="file"> can present an Open panel
        wv.navigationDelegate = self // so external URLs open in the system browser
        webView = wv

        let win = NSWindow(
            contentRect: frame,
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false)
        win.title = "Second Brain"
        win.contentView = wv
        win.delegate = self
        win.setFrameAutosaveName("SecondBrainMainWindow")
        win.center()
        window = win
    }

    /// Brings the window to the front (creating it on first call) and activates the app.
    func show() {
        makeWindowIfNeeded()
        window?.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    /// Loads (or reloads) the dashboard URL.
    func load() {
        makeWindowIfNeeded()
        webView?.load(URLRequest(url: BridgeController.shared.dashboardURL))
        applyZoom(Preferences.pageZoom)
    }

    /// Runs JavaScript in the dashboard page (best-effort; no-op if the view isn't ready).
    func evaluateJavaScript(_ js: String) {
        webView?.evaluateJavaScript(js, completionHandler: nil)
    }

    @objc func reload() {
        webView?.reload()
        applyZoom(Preferences.pageZoom)
    }

    // MARK: - Zoom (⌘+ / ⌘- / ⌘0)

    /// Applies `factor` (clamped to the zoom range) to the web view and persists it.
    private func applyZoom(_ factor: Double) {
        let clamped = min(max(factor, zoomStops.first!), zoomStops.last!)
        if #available(macOS 11.0, *) { webView?.pageZoom = clamped }
        Preferences.pageZoom = clamped
    }

    @objc func zoomIn() {
        let current = Preferences.pageZoom
        let next = zoomStops.first { $0 > current + 0.001 } ?? zoomStops.last!
        applyZoom(next)
    }

    @objc func zoomOut() {
        let current = Preferences.pageZoom
        let prev = zoomStops.last { $0 < current - 0.001 } ?? zoomStops.first!
        applyZoom(prev)
    }

    @objc func resetZoom() {
        applyZoom(1.0)
    }

    // Hide instead of destroy, so page state survives a close → Dock-click reopen.
    func windowShouldClose(_ sender: NSWindow) -> Bool {
        sender.orderOut(nil)
        return false
    }
}

// MARK: - Script bridge (dashboard → native)

extension WebWindow: WKScriptMessageHandler {
    /// Handles messages posted to `window.webkit.messageHandlers.secondBrain`.
    /// Delivered on the main thread, so it's safe to drive UI / the bridge here.
    func userContentController(_ userContentController: WKUserContentController,
                              didReceive message: WKScriptMessage) {
        guard message.name == "secondBrain",
              let body = message.body as? [String: Any],
              let action = body["action"] as? String else { return }
        switch action {
        case "switchEngine":
            if let engine = body["engine"] as? String {
                (NSApp.delegate as? AppDelegate)?.switchEngine(to: engine)
            }
        case "switchModel":
            if let engine = body["engine"] as? String,
               let tier   = body["tier"]   as? String {
                (NSApp.delegate as? AppDelegate)?.switchModel(engine: engine, to: tier)
            }
        default:
            break
        }
    }
}

/// Holds the real script-message handler weakly. A `WKUserContentController`
/// retains its handlers strongly, which would otherwise form a
/// controller → handler → webView → configuration → controller retain cycle.
final class WeakScriptMessageHandler: NSObject, WKScriptMessageHandler {
    private weak var target: WKScriptMessageHandler?
    init(_ target: WKScriptMessageHandler) { self.target = target }
    func userContentController(_ controller: WKUserContentController,
                              didReceive message: WKScriptMessage) {
        target?.userContentController(controller, didReceive: message)
    }
}

// MARK: - External URL routing

extension WebWindow: WKNavigationDelegate {
    /// Any navigation to a non-localhost URL (e.g. a GitHub link in the footer) is
    /// intercepted and handed to the system browser. Everything on localhost is allowed
    /// through so the dashboard itself loads normally.
    func webView(_ webView: WKWebView,
                 decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if let url = navigationAction.request.url,
           let host = url.host,
           host != "localhost" && host != "127.0.0.1" {
            NSWorkspace.shared.open(url)
            decisionHandler(.cancel)
            return
        }
        decisionHandler(.allow)
    }

    /// `window.open()` calls also come here. Open in system browser; never create
    /// a new in-app WebView window.
    func webView(_ webView: WKWebView,
                 createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction,
                 windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = navigationAction.request.url {
            NSWorkspace.shared.open(url)
        }
        return nil
    }
}

// MARK: - File input (<input type="file">)

extension WebWindow: WKUIDelegate {
    /// WKWebView does nothing when the user clicks an `<input type="file">` unless the
    /// host presents a picker. Show an NSOpenPanel (as a sheet on the window) and hand
    /// the chosen URLs back — this is what makes "Choose file…" work inside the app.
    func webView(_ webView: WKWebView,
                 runOpenPanelWith parameters: WKOpenPanelParameters,
                 initiatedByFrame frame: WKFrameInfo,
                 completionHandler: @escaping ([URL]?) -> Void) {
        let panel = NSOpenPanel()
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = parameters.allowsMultipleSelection
        let finish: (NSApplication.ModalResponse) -> Void = { resp in
            completionHandler(resp == .OK ? panel.urls : nil)
        }
        if let win = window {
            panel.beginSheetModal(for: win, completionHandler: finish)
        } else {
            finish(panel.runModal())
        }
    }
}

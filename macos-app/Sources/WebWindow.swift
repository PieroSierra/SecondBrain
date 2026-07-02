import AppKit
import WebKit

/// The single dashboard window: an `NSWindow` hosting a `WKWebView` pointed at the
/// bridge. Closing the red button hides the window (the app keeps running so a Dock
/// click brings it back) rather than tearing down the web view and its state.
final class WebWindow: NSObject, NSWindowDelegate {
    static let shared = WebWindow()

    private(set) var window: NSWindow?
    private var webView: WKWebView?

    private override init() { super.init() }

    private func makeWindowIfNeeded() {
        guard window == nil else { return }

        let frame = NSRect(x: 0, y: 0, width: 1200, height: 820)
        let wv = WKWebView(frame: frame, configuration: WKWebViewConfiguration())
        wv.autoresizingMask = [.width, .height]
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
    }

    @objc func reload() {
        webView?.reload()
    }

    // Hide instead of destroy, so page state survives a close → Dock-click reopen.
    func windowShouldClose(_ sender: NSWindow) -> Bool {
        sender.orderOut(nil)
        return false
    }
}

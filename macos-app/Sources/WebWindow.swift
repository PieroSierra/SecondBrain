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
        let wv = WKWebView(frame: frame, configuration: WKWebViewConfiguration())
        wv.autoresizingMask = [.width, .height]
        if #available(macOS 11.0, *) { wv.pageZoom = Preferences.pageZoom }
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

import AppKit

// Programmatic entry point (no storyboard/nib). A regular Dock app: `.regular`
// activation policy gives it a Dock icon and normal window/menu behavior.
let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()

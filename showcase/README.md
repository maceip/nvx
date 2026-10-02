# NVX Showcase

The macOS Runtime and Snapshots pages use Pie's native SwiftUI/AppKit tab strip.
Build with Xcode 26 or newer (for the macOS 26 glass APIs); the app retains its
macOS 14 deployment target and uses the strip's older-system material fallback.

```sh
cd showcase
swift test
./assemble.sh
open dist/NVXShowcase.app
```

The first window starts with Runtime and Snapshots. The `+` button and Command-T
add an independent Runtime tab. Command-Shift-T or the tab-list menu adds a
Snapshots tab. Click a selected tab again to rename it. Right-click for pinning,
closing, and moving to a new window. Command-W closes the current ordinary tab;
pinned tabs stay open. Command-Shift-[ and Command-Shift-] select adjacent tabs.

Drag tabs to reorder, lift them out to make a native window, or drop them into
another NVX window's tab strip. A window with only one tab cannot lift it or use
the tab as a window-drag surface. Add a companion tab before transferring that
page elsewhere. Closing the last tab creates a fresh Runtime page.

Each tab owns its controller/store and retained hosting view. A transfer moves
those objects without restarting a VM. Snapshot selection, manifest output,
sidebar visibility, and runtime output belong to the tab. Page actions live
inside the retained page, so Run/Stop, Save Snapshot, Choose, Verify, and Resume
remain available in detached windows. Closing a tab/window stops its runtime or
restore; changing selection, pinning, or moving it does not. Run directories
have unique identifiers and pruning skips active runs from other tabs.

## Reused component

`Sources/NVXShowcase/Tabs/Pie/` vendors these existing Pie files so NVX builds
independently of a sibling checkout:

- `PiCode/Features/Tabs/CompactTabStrip.swift`
- `PiCode/Features/Tabs/TabStripGeometry.swift`
- `PiCode/Features/Root/TitlebarControlLayout.swift`

The drag tracking, liquid glass/refraction, springs, early snap thresholds,
drop settling, tear-off morph, pin geometry, hover, focus, and close-width lock
are reused. Geometry and titlebar measurement are unchanged. The strip adapter
adds configurable refresh text/visibility, an address accessibility label,
and a compressed-title layout fix found by the 16-tab stress pass;
NVX's field renames tabs, and only Snapshots offers refresh. The preview comes
from the app's retained page view, with no screen-capture permission request.
Source hashes and the small component delta are recorded in
`Tabs/Pie-source.json` and `Tabs/Pie-adapter.patch` (relative to this directory).

NVX ownership and window creation live in `ShowcaseWorkspace.swift` and
`ShowcaseWindows.swift`; the root composition lives in `ContentView.swift`.
The native Pie checkout and the standalone web implementation are unchanged.

## Validation

`swift test` exercises controller/hosting-view identity, cross-window ownership,
pin boundaries, single-tab protection, close behavior, snapshot page state,
42-tab model stress, native snap thresholds, and independent real subprocesses
with separate logs. The subprocess fixture does not boot a VM or run a payload.

Actual UI interaction is exercised in the isolated macOS CUA test VM; artifacts
are written under `validation/`. See its verification record for the exact
build, completed checks, and screenshots. The host desktop is not driven during
these checks.

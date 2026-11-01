// Cocoa/CoreGraphics through the built-in JXA host. No Apple Events, injected
// App delegate, global input, or access to another application's windows.
function run(arguments) {
  ObjC.import('Cocoa');
  ObjC.import('CoreGraphics');
  // CFArrayRef is toll-free bridged to NSArray; the default JXA signature
  // leaves it as an opaque reference instead of a traversable window list.
  ObjC.bindFunction('CGWindowListCopyWindowInfo', ['id', ['unsigned int', 'unsigned int']]);
  var mode = arguments[0];
  if (mode === 'home') return JSON.stringify({
    foundation_home: ObjC.unwrap($.NSHomeDirectory()),
    file_manager_home: ObjC.unwrap($.NSFileManager.defaultManager.homeDirectoryForCurrentUser.path)
  });
  if (mode === 'foreground') return JSON.stringify({pid: Number($.NSWorkspace.sharedWorkspace.frontmostApplication.processIdentifier)});
  var pid = Number(arguments[1]);
  var app = $.NSRunningApplication.runningApplicationWithProcessIdentifier(pid);
  if (mode === 'restore') return JSON.stringify({restored: Boolean(app.activateWithOptions(0))});
  var entries = ObjC.deepUnwrap($.CGWindowListCopyWindowInfo(17, 0));
  var windows = entries.filter(function(entry) {
    return entry.kCGWindowOwnerPID === pid && entry.kCGWindowLayer === 0;
  }).map(function(entry) {
    return {id: entry.kCGWindowNumber, bounds: entry.kCGWindowBounds,
      title: entry.kCGWindowName || '', onscreen: Boolean(entry.kCGWindowIsOnscreen)};
  });
  return JSON.stringify({pid: pid, running: !Boolean(app.terminated),
    finished_launching: Boolean(app.finishedLaunching),
    bundle_id: ObjC.unwrap(app.bundleIdentifier),
    executable: ObjC.unwrap(app.executableURL.path), windows: windows});
}

# Apple bridge typing surface

These stubs describe only the PyObjC APIs Anteroom calls. PyObjC 13.0 loads
framework exports dynamically and ships no typing stubs. Keep runtime imports
unchanged; do not add catch-all exports or replace signatures with `Any`.

Sources: [PyObjC introduction](https://pyobjc.readthedocs.io/en/latest/core/intro.html)
and [bridge API](https://pyobjc.readthedocs.io/en/latest/api/module-objc.html).
Objective-C output parameters passed as `None` become tuple results. Initializers
and URL parsing can return `None`; null pixel-buffer base addresses instead
return `objc.NULL`. Valid addresses are `objc.varlist` pointers exposing
`as_buffer`. `test_pyobjc_typing.py` checks
negative/positive calls and installed framework metadata without Keychain writes.

This is an application-owned subset, not a complete framework typing package.
Extend it explicitly when adding another framework call. Preserve nullable
results and opaque handles; runtime metadata alone does not establish semantic
nullability for every Objective-C method.

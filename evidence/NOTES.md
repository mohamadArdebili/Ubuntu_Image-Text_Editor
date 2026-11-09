# Inspection ledger

Target: `/usr/bin/gnome-text-editor`, installed package `50.1-0ubuntu0.1`.
SHA-256: `7fb2ef38334b1b11525c7aa8b115b48ad3b7f09f7fc482ddcf3f71ddccb0e229`.

- REA `inspect_native_exports`, Evidence `ev_6b7577a7cc676fc792730dba010d57bf70ce736891c490300d163a165825b1cc`: ELF amd64, 617152 bytes, 1015 dynamic symbol entries examined, no defined exports. This establishes artifact identity, not application behavior.
- REA `open_binary` opened that target with Hopper. `search_strings` for `paste`, Evidence `ev_c69f0dd076c83f76c4b8bf252978ad73215ab0e89fc07d2f49b152f9012c7cdf`, returned zero provider matches. Discovery coverage is unknown; this does not prove clipboard support absent. Native session closed after inspection.
- `gresource extract` recovered `editor-window.ui` and `editor-page.ui` here. The window resource declares AdwApplicationWindow, AdwHeaderBar and AdwTabView. These guided the native layout. Resources are from the upstream GNOME project and retain its licensing; they are not part of the executable implementation.
- Design choice: use GTK TextView paintables and a separate `.jot` format for embedded PNGs. This is new implementation, not decompiled source or a claim of complete feature parity.

Answered: installed target identity, static UI structure, local availability of GTK/libadwaita, image/text storage round trip and prevention of image loss through plain-text saves.
Partially answered: reconstruction fidelity (static UI only).
Unresolved: interactive visual comparison, live desktop clipboard interoperability and GUI tests; sandbox has no usable display connection.

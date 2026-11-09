# Image Text Editor

A small native GTK 4/libadwaita recreation inspired by GNOME Text Editor, with inline images.

Run from this directory:

```sh
./editor.py
```

Uses Ubuntu's system Python, PyGObject, GTK 4 and libadwaita, already present on the inspected machine. No pip packages required.

- Type text normally. Paste a copied image or screenshot with **Ctrl+V**, or use **Insert image** in the header.
- **Ctrl+N** opens a tab; **Ctrl+O** opens a document; **Ctrl+S** saves; **Ctrl+Shift+S** saves under another name.
- **Ctrl+F** finds text; Enter finds the next occurrence. **Ctrl+W** closes the tab.
- Image documents use **`.jot`**, a JSON file containing text and embedded PNG images. Keep this extension when saving images.
- Plain UTF-8 text files remain plain text. Adding an image prompts for a `.jot` destination on save.
- Closing modified tabs or windows prompts to save, discard, or cancel. Saves use GIO atomic replacement.

This is a focused recreation, not a complete GNOME Text Editor fork. It does not include syntax highlighting, session recovery, image resizing, or rich document export. Images display at their original size. Save work explicitly; there is no crash recovery. Image clipboard formats are supported; copying an image file in Files is a different clipboard operation.

## Checks

```sh
/usr/bin/python3 -m unittest -v test_editor
# From an environment with X11 support:
GDK_BACKEND=x11 GSETTINGS_BACKEND=memory xvfb-run -a /usr/bin/python3 -m unittest -v test_editor
```

Storage checks run without a display. GUI checks cover image clipboard paste, document round trips, rejected input and cancelled tab closure, and skip when no display is available. In the build sandbox only storage checks ran successfully; GUI tests could not connect to a display.

## Investigation

See [evidence/NOTES.md](evidence/NOTES.md). Extracted upstream UI resources are reference evidence, not loaded by this app.

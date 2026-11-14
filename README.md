# Ubuntu Image-Text Editor

<img width="1050" height="770" alt="image" src="https://github.com/user-attachments/assets/e63f0281-59ee-4c4b-8416-a2565b3d029f" />
A small native GTK 4/libadwaita recreation inspired by GNOME Text Editor, with inline images.

Run from this directory:

```sh
./editor.py
```

Uses Ubuntu's system Python, PyGObject, GTK 4 and libadwaita, already present on the inspected machine. No pip packages
required.

- Type text normally. Paste a copied image or screenshot with **Ctrl+V**, or use **Insert Image…** in the menu (also
  available in the header) to insert a downloaded image into the current tab.
- Opening a supported image through **Ctrl+O** or a file argument creates a new unsaved image document. Save it as
  `.jot`; the original image stays untouched.
- **Ctrl+N** opens a tab; **Ctrl+O** opens a document; **Ctrl+S** saves; **Ctrl+Shift+S** saves under another name.
- **Ctrl+F** finds text; Enter finds the next occurrence. **Ctrl+W** closes the tab.
- Image documents use **`.jot`**, a JSON file containing text and embedded PNG images. Keep this extension when saving
  images.
- Plain UTF-8 text files remain plain text. Adding an image prompts for a `.jot` destination on save.
- Closing modified tabs or windows prompts to save, discard, or cancel. Saves use GIO atomic replacement.

This is a focused recreation, not a complete GNOME Text Editor fork. It does not include syntax highlighting or rich
document export. Save work explicitly; recovery snapshots supplement normal saving. Image clipboard formats are
supported; copying an image file in Files is a different clipboard operation.

## Checks

```sh
/usr/bin/python3 -m unittest -v test_editor
# From an environment with X11 support:
GDK_BACKEND=x11 GSETTINGS_BACKEND=memory xvfb-run -a /usr/bin/python3 -m unittest -v test_editor
```

Storage checks run without a display. GUI checks cover image clipboard paste, document round trips, rejected input and
cancelled tab closure, and skip when no display is available. In the build sandbox only storage checks ran successfully;
GUI tests could not connect to a display.

## Investigation

See [evidence/NOTES.md](evidence/NOTES.md). Extracted upstream UI resources are reference evidence, not loaded by this
app.

## Display options

The header menu includes **Display Line Numbers** and **Highlight Current Line**. Both default to off and apply to every
tab in the current window, including new tabs. Changes are saved immediately and restored on the next launch. Settings
live in `$XDG_CONFIG_HOME/image-text-editor/settings.json` (normally `~/.config/image-text-editor/settings.json`).
Numbers count document lines, not wrapped rows; the current-line highlight covers the entire logical line. These
decorations do not change saved documents.

## Images and recovery

- Click an image (or place the cursor beside it) to reveal four corner handles. Drag a handle to preview a proportional
  resize; release to apply it, or press **Escape** to cancel. The image stays anchored in the text flow and surrounding
  text reflows on release.
- **Resize Image…** in the header menu resizes the image immediately before or after the cursor. Enter a width in
  pixels; height follows the original aspect ratio. Resizing resamples the embedded image, so repeated resizing can
  reduce quality. Save as `.jot` to keep the resized result; source image files stay unchanged.
- Drag local image files from Files, or an image supplied as an image by another application, onto the editor to insert
  at the drop position. Multiple images insert in file-list order. All dropped files must decode successfully before
  insertion. Text drops retain GTK's normal behavior; remote image URLs and document-file drops are not supported.
- Every five seconds, modified tabs receive private recovery copies under
  `$XDG_STATE_HOME/image-text-editor/recovery/` (normally `~/.local/state/image-text-editor/recovery/`). Text and
  embedded images are included. Original documents are not overwritten by autosave.
- After an unexpected exit, recovery copies automatically reopen as modified **Recovered document** tabs. Use Save As to
  choose their destination. Saving or explicitly discarding a tab removes its recovery copy; Cancel keeps it. Unreadable
  recovery files stay on disk and produce an error.
- Changes since the latest successful snapshot can be lost. Large image documents may briefly pause the editor during
  snapshot encoding; recovery copies are local files, not encrypted storage.

## Font size

Use **Larger Text** and **Smaller Text** in the header menu, or **Ctrl++** and **Ctrl+-**. **Ctrl+=** also increases the size without requiring Shift; numeric-keypad plus/minus work too. Size changes in one-point steps, from 6 to 72 pt (default 12 pt), across all tabs, including new ones. The chosen size is saved with display preferences and restored on launch. This changes editor display only; document text and embedded image sizes stay unchanged.

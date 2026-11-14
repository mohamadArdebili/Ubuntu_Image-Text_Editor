#!/usr/bin/python3
"""Native text editor with embedded clipboard images. Run with system Python."""
import base64
import json
from pathlib import Path
import sys
import uuid
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Graphene', '1.0')
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, GObject, Graphene, Gtk


def load_preferences(path):
    settings = {'line-numbers': False, 'highlight-current-line': False, 'font-size': 12}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data, dict):
            raise ValueError('Expected a settings object')
        for key in ('line-numbers', 'highlight-current-line'):
            if type(data.get(key)) is bool:
                settings[key] = data[key]
        size = data.get('font-size')
        if type(size) is int and 6 <= size <= 72:
            settings['font-size'] = size
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as error:
        print(f'Could not read display settings: {error}', file=sys.stderr)
    return settings


def save_preferences(path, settings):
    path.parent.mkdir(parents=True, exist_ok=True)
    Gio.File.new_for_path(str(path)).replace_contents(
        json.dumps(settings).encode('utf-8'), None, False, Gio.FileCreateFlags.PRIVATE, None)


def resize_texture(texture, width):
    height = max(1, round(texture.get_height() * width / texture.get_width()))
    if not 1 <= width <= 16384 or height > 16384:
        raise ValueError('Image dimensions must be between 1 and 16384 pixels.')
    pixels = Gdk.pixbuf_get_from_texture(texture)
    scaled = pixels.scale_simple(width, height, GdkPixbuf.InterpType.BILINEAR)
    return Gdk.Texture.new_for_pixbuf(scaled)


def corner_width(width, height, dx, dy, sx, sy):
    ratio = height / width
    proposed = round(width + (sx * dx + sy * dy * ratio) / (1 + ratio * ratio))
    return max(1, min(proposed, 16384, int(16384 / ratio)))


def replace_image(buffer, mark, image, width):
    start = buffer.get_iter_at_mark(mark)
    if start.get_paintable() != image:
        raise ValueError('The image changed while resizing.')
    if width == image.get_width():
        return
    resized = resize_texture(image, width)
    end = start.copy()
    end.forward_char()
    buffer.begin_user_action()
    buffer.delete(start, end)
    buffer.insert_paintable(start, resized)
    buffer.end_user_action()


def save_recovery(buffer, path):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    save_preferences(path, {'format': 'image-text-1', 'parts': parts(buffer)})


def parts(buffer):
    result, text = [], []
    pos = buffer.get_start_iter()
    while not pos.is_end():
        image = pos.get_paintable()
        if image:
            if text:
                result.append({'text': ''.join(text)})
                text = []
            result.append({'png': base64.b64encode(image.save_to_png_bytes().get_data()).decode('ascii')})
        else:
            text.append(pos.get_char())
        pos.forward_char()
    if text:
        result.append({'text': ''.join(text)})
    return result


def load(path):
    data = path.read_bytes()
    if path.suffix.lower() != '.jot':
        content_type, _ = Gio.content_type_guess(str(path), data[:4096])
        if Gio.content_type_get_mime_type(content_type).startswith('image/'):
            return [Gdk.Texture.new_from_bytes(GLib.Bytes.new(data))]
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError as error:
        raise ValueError('This file is not a supported image or a UTF-8 text document.') from error
    if path.suffix.lower() != '.jot':
        return [text]
    data = json.loads(text)
    if not isinstance(data, dict) or data.get('format') != 'image-text-1' or not isinstance(data.get('parts'), list):
        raise ValueError('Unsupported image document')
    decoded = []
    for part in data['parts']:
        if not isinstance(part, dict) or len(part) != 1:
            raise ValueError('Invalid document part')
        if 'text' in part and isinstance(part['text'], str):
            decoded.append(part['text'])
        elif 'png' in part and isinstance(part['png'], str):
            decoded.append(Gdk.Texture.new_from_bytes(GLib.Bytes.new(base64.b64decode(part['png'], validate=True))))
        else:
            raise ValueError('Invalid document part')
    return decoded


def save(buffer, path):
    content = parts(buffer)
    if path.suffix.lower() == '.jot':
        text = json.dumps({'format': 'image-text-1', 'parts': content}, ensure_ascii=False)
    else:
        if any('png' in p for p in content):
            raise ValueError('Images require a .jot filename. Use Save As to keep your images.')
        text = ''.join(p['text'] for p in content)
    # GIO replaces atomically and reports failures before the document is marked saved.
    Gio.File.new_for_path(str(path)).replace_contents(text.encode('utf-8'), None, False, Gio.FileCreateFlags.NONE, None)


class LineNumbers(Gtk.Widget):
    def __init__(self, view):
        super().__init__(can_target=False)
        self.view = view

    def do_snapshot(self, snapshot):
        view = self.view
        visible = view.get_visible_rect()
        pos, _ = view.get_line_at_y(visible.y)
        color = view.get_color()
        color.alpha = 0.55
        while True:
            y, _ = view.get_line_yrange(pos)
            if y > visible.y + visible.height:
                break
            _, window_y = view.buffer_to_window_coords(Gtk.TextWindowType.LEFT, 0, y)
            layout = view.create_pango_layout(str(pos.get_line() + 1))
            width, _ = layout.get_pixel_size()
            snapshot.save()
            snapshot.translate(Graphene.Point().init(self.get_width() - width - 10, window_y))
            snapshot.append_layout(layout, color)
            snapshot.restore()
            if pos.get_line() == view.get_buffer().get_line_count() - 1:
                break
            pos.forward_line()


class EditorView(Gtk.TextView):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.highlight_current_line = False
        self.font_css = Gtk.CssProvider()
        self.get_style_context().add_provider(self.font_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.image_drag = None
        self.numbers = LineNumbers(self)
        self.set_gutter(Gtk.TextWindowType.LEFT, self.numbers)
        self.get_buffer().connect('changed', self.refresh_decorations)
        self.get_buffer().connect('notify::cursor-position', self.refresh_decorations)
        self.resize_gesture = Gtk.GestureDrag(button=1)
        self.resize_gesture.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self.resize_gesture.connect('drag-begin', self.resize_begin)
        self.resize_gesture.connect('drag-update', self.resize_update)
        self.resize_gesture.connect('drag-end', self.resize_end)
        self.resize_gesture.connect('cancel', lambda *_: self.cancel_resize())
        self.add_controller(self.resize_gesture)
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect('key-pressed', self.resize_key)
        self.add_controller(keys)

    def set_font_size(self, size):
        self.font_css.load_from_string(f'textview {{ font-size: {size}pt; }}')
        self.queue_resize()
        GLib.idle_add(self.refresh_decorations)

    def selected_image(self):
        buffer = self.get_buffer()
        pos = buffer.get_iter_at_mark(buffer.get_insert())
        if not pos.get_paintable():
            pos.backward_char()
        return pos if pos.get_paintable() else None

    def resize_begin(self, gesture, x, y):
        pos = self.selected_image()
        if pos is not None:
            rect = self.get_iter_location(pos)
            image = pos.get_paintable()
            bx, by = self.window_to_buffer_coords(Gtk.TextWindowType.WIDGET, int(x), int(y))
            for sx, sy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
                cx = rect.x + (image.get_width() if sx == 1 else 0)
                cy = rect.y + (image.get_height() if sy == 1 else 0)
                if abs(bx - cx) <= 7 and abs(by - cy) <= 7:
                    self.image_drag = (self.get_buffer().create_mark(None, pos, True), image, rect, sx, sy, image.get_width())
                    gesture.set_state(Gtk.EventSequenceState.CLAIMED)
                    self.set_cursor_from_name('nwse-resize' if sx == sy else 'nesw-resize')
                    return
        gesture.set_state(Gtk.EventSequenceState.DENIED)

    def resize_update(self, gesture, dx, dy):
        if self.image_drag:
            mark, image, rect, sx, sy, _ = self.image_drag
            width = corner_width(image.get_width(), image.get_height(), dx, dy, sx, sy)
            self.image_drag = (mark, image, rect, sx, sy, width)
            self.queue_draw()

    def cancel_resize(self):
        if self.image_drag:
            self.get_buffer().delete_mark(self.image_drag[0])
            self.image_drag = None
            self.set_cursor_from_name(None)
            self.queue_draw()

    def resize_key(self, controller, key, code, state):
        if self.image_drag and key == Gdk.KEY_Escape:
            self.cancel_resize()
            self.resize_gesture.set_state(Gtk.EventSequenceState.DENIED)
            return True
        return False

    def resize_end(self, gesture, dx, dy):
        if not self.image_drag:
            return
        self.resize_update(gesture, dx, dy)
        mark, image, _, _, _, width = self.image_drag
        try:
            replace_image(self.get_buffer(), mark, image, width)
        except (GLib.Error, ValueError) as error:
            self.get_root().error(error)
        finally:
            self.cancel_resize()

    def refresh_decorations(self, *_):
        digits = len(str(self.get_buffer().get_line_count()))
        width, _ = self.create_pango_layout('0' * digits).get_pixel_size()
        self.numbers.set_size_request(width + 20, -1)
        self.numbers.queue_draw()
        self.queue_draw()

    def do_snapshot_layer(self, layer, snapshot):
        if layer == Gtk.TextViewLayer.BELOW_TEXT and self.highlight_current_line:
            buffer = self.get_buffer()
            pos = buffer.get_iter_at_mark(buffer.get_insert())
            y, height = self.get_line_yrange(pos)
            visible = self.get_visible_rect()
            color = self.get_color()
            color.alpha = 0.07
            snapshot.append_color(color, Graphene.Rect().init(visible.x, y, visible.width, height))
        if layer == Gtk.TextViewLayer.ABOVE_TEXT:
            pos = self.selected_image()
            if pos is None:
                return
            image = pos.get_paintable()
            rect = self.get_iter_location(pos)
            x, y, width, height = rect.x, rect.y, image.get_width(), image.get_height()
            if self.image_drag:
                _, image, rect, sx, sy, width = self.image_drag
                height = max(1, round(image.get_height() * width / image.get_width()))
                x = rect.x + (image.get_width() - width if sx == -1 else 0)
                y = rect.y + (image.get_height() - height if sy == -1 else 0)
            color = self.get_color()
            for rx, ry, rw, rh in ((x, y, width, 1), (x, y + height, width, 1),
                                   (x, y, 1, height), (x + width, y, 1, height)):
                snapshot.append_color(color, Graphene.Rect().init(rx, ry, rw, rh))
            for cx, cy in ((x, y), (x + width, y), (x, y + height), (x + width, y + height)):
                snapshot.append_color(color, Graphene.Rect().init(cx - 5, cy - 5, 10, 10))


class Page(Gtk.ScrolledWindow):
    def __init__(self, owner):
        super().__init__(hexpand=True, vexpand=True)
        self.owner, self.path = owner, None
        self.recovery_path = owner.recovery_dir / f'{uuid.uuid4().hex}.jot'
        self.recovery_pending = False
        self.recovered = False
        self.buffer = Gtk.TextBuffer(enable_undo=True)
        self.view = EditorView(buffer=self.buffer, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR,
                                 left_margin=30, right_margin=30, top_margin=24, bottom_margin=24)
        self.view.set_font_size(owner.font_size)
        self.view.numbers.set_visible(owner.lookup_action('line-numbers').get_state().get_boolean())
        self.view.highlight_current_line = owner.lookup_action('highlight-current-line').get_state().get_boolean()
        self.view.refresh_decorations()
        self.set_child(self.view)
        self.get_vadjustment().connect('value-changed', self.view.refresh_decorations)
        self.view.connect('paste-clipboard', self.paste)
        self.buffer.connect('modified-changed', lambda *_: self.title())
        self.buffer.connect('notify::cursor-position', lambda *_: owner.position(self))
        self.buffer.connect('changed', self.changed)
        target = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        target.set_gtypes([Gdk.FileList, Gdk.Texture])
        target.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        target.connect('drop', self.drop_image)
        self.view.add_controller(target)

    def changed(self, *_):
        self.recovery_pending = True

    def drop_image(self, target, value, x, y):
        try:
            if isinstance(value, Gdk.Texture):
                images = [value]
            else:
                images = []
                for file in value.get_files():
                    path = file.get_path()
                    if path is None:
                        raise ValueError('Drop a local image file.')
                    images.append(Gdk.Texture.new_from_filename(path))
            if not images:
                return False
            bx, by = self.view.window_to_buffer_coords(Gtk.TextWindowType.WIDGET, int(x), int(y))
            _, pos = self.view.get_iter_at_location(bx, by)
            self.buffer.place_cursor(pos)
            self.buffer.begin_user_action()
            for image in images:
                self.insert_image(image)
            self.buffer.end_user_action()
            return True
        except (GLib.Error, ValueError) as error:
            self.owner.error(error)
            return False

    def title(self):
        tab = self.owner.tabs.get_page(self)
        if tab:
            tab.set_title(('• ' if self.buffer.get_modified() else '') + (self.path.name if self.path else ('Recovered document' if self.recovered else 'Untitled')))

    def resize_image(self):
        pos = self.buffer.get_iter_at_mark(self.buffer.get_insert())
        if not pos.get_paintable():
            pos.backward_char()
        image = pos.get_paintable()
        if not image:
            self.owner.error('Place the cursor immediately before or after an image, then choose Resize Image.')
            return
        mark = self.buffer.create_mark(None, pos, True)
        dialog = Adw.MessageDialog(transient_for=self.owner, heading='Resize Image', body='Width in pixels. Aspect ratio is preserved.')
        width = Gtk.SpinButton.new_with_range(1, 16384, 1)
        width.set_value(image.get_width())
        dialog.set_extra_child(width)
        dialog.add_response('cancel', 'Cancel')
        dialog.add_response('resize', 'Resize')
        dialog.set_close_response('cancel')
        def response(d, key):
            try:
                if key == 'resize':
                    replace_image(self.buffer, mark, image, width.get_value_as_int())
            except (GLib.Error, ValueError) as error:
                self.owner.error(error)
            finally:
                self.buffer.delete_mark(mark)
        dialog.connect('response', response)
        dialog.present()

    def insert_image(self, texture):
        self.buffer.begin_user_action()
        self.buffer.delete_selection(True, True)
        self.buffer.insert_paintable(self.buffer.get_iter_at_mark(self.buffer.get_insert()), texture)
        self.buffer.end_user_action()

    def paste(self, view):
        clipboard = view.get_clipboard()
        if not clipboard.get_formats().contain_gtype(Gdk.Texture):
            return
        GObject.signal_stop_emission_by_name(view, 'paste-clipboard')
        mark = self.buffer.create_mark(None, self.buffer.get_iter_at_mark(self.buffer.get_insert()), False)
        def received(source, result):
            try:
                image = source.read_texture_finish(result)
                if image:
                    self.buffer.place_cursor(self.buffer.get_iter_at_mark(mark))
                    self.insert_image(image)
            except GLib.Error as error:
                self.owner.error(error)
            finally:
                self.buffer.delete_mark(mark)
        clipboard.read_texture_async(None, received)


class Window(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title='Image Text Editor', default_width=1000, default_height=720)
        self.preferences_path = Path(GLib.get_user_config_dir()) / 'image-text-editor' / 'settings.json'
        preferences = load_preferences(self.preferences_path)
        self.font_size = preferences['font-size']
        self.recovery_dir = Path(GLib.get_user_state_dir()) / 'image-text-editor' / 'recovery'
        self.recovery_error = False
        self.tabs = Adw.TabView(vexpand=True)
        self.tabs.connect('close-page', self.close_page)
        self.tabs.connect('notify::selected-page', lambda *_: self.current() and self.position(self.current()))
        bar = Adw.HeaderBar()
        for icon, tip, action in [('document-open-symbolic', 'Open (Ctrl+O)', 'open'),
                                  ('list-add-symbolic', 'New tab (Ctrl+N)', 'new')]:
            bar.pack_start(Gtk.Button(icon_name=icon, tooltip_text=tip, action_name='win.' + action))
        for icon, tip, action in [('document-save-symbolic', 'Save (Ctrl+S)', 'save'),
                                  ('insert-image-symbolic', 'Insert image', 'image')]:
            bar.pack_end(Gtk.Button(icon_name=icon, tooltip_text=tip, action_name='win.' + action))
        menu = Gio.Menu()
        for label, action in [('Save As…', 'save-as'), ('Insert Image…', 'image'), ('Resize Image…', 'resize-image'), ('Paste', 'paste'), ('Find…', 'find'), ('Close Tab', 'close')]:
            menu.append(label, 'win.' + action)
        menu.append('Larger Text', 'win.font-increase')
        menu.append('Smaller Text', 'win.font-decrease')
        for label, name in [('Display Line Numbers', 'line-numbers'),
                            ('Highlight Current Line', 'highlight-current-line')]:
            action = Gio.SimpleAction.new_stateful(name, None, GLib.Variant('b', preferences[name]))
            action.connect('activate', lambda a, p: a.change_state(GLib.Variant('b', not a.get_state().get_boolean())))
            action.connect('change-state', self.display_option)
            self.add_action(action)
            menu.append(label, 'win.' + name)
        bar.pack_end(Gtk.MenuButton(icon_name='open-menu-symbolic', menu_model=menu))
        tabbar = Adw.TabBar(view=self.tabs, autohide=True)
        self.search = Gtk.SearchEntry(placeholder_text='Find text', hexpand=True)
        self.search.connect('search-changed', lambda *_: self.find())
        self.search.connect('activate', lambda *_: self.find())
        self.searchbar = Gtk.SearchBar(child=self.search)
        self.searchbar.connect_entry(self.search)
        self.status = Gtk.Label(label='Ln 1, Col 1', xalign=1, margin_end=16, margin_top=5, margin_bottom=5)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for child in (bar, tabbar, self.searchbar, self.tabs, self.status):
            box.append(child)
        self.set_content(box)
        actions = {'new': lambda: self.new(), 'open': lambda: self.choose('open'),
                   'save': lambda: self.save_page(self.current()), 'save-as': lambda: self.choose('save', self.current()),
                   'image': lambda: self.choose('image', self.current()),
                   'resize-image': lambda: self.current().resize_image(),
                   'font-increase': lambda: self.change_font_size(1),
                   'font-decrease': lambda: self.change_font_size(-1),
                   'paste': lambda: self.current().view.emit('paste-clipboard'),
                   'find': self.show_find, 'close': lambda: self.tabs.close_page(self.tabs.get_selected_page())}
        shortcuts = {'new': '<Control>n', 'open': '<Control>o', 'save': '<Control>s',
                     'save-as': '<Control><Shift>s', 'find': '<Control>f', 'close': '<Control>w'}
        for name, callback in actions.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect('activate', lambda a, p, cb=callback: cb())
            self.add_action(action)
            if name in shortcuts:
                app.set_accels_for_action('win.' + name, [shortcuts[name]])
        app.set_accels_for_action('win.font-increase', ['<Control>plus', '<Control>equal', '<Control>KP_Add'])
        app.set_accels_for_action('win.font-decrease', ['<Control>minus', '<Control>KP_Subtract'])
        self.connect('close-request', self.close_window)
        self.restore_recovery()
        if not self.tabs.get_n_pages():
            self.new()

    def restore_recovery(self):
        for path in sorted(self.recovery_dir.glob('*.jot')):
            try:
                content = load(path)
                page = self.new()
                page.recovery_path = path
                page.recovered = True
                for item in content:
                    if isinstance(item, str):
                        page.buffer.insert(page.buffer.get_end_iter(), item)
                    else:
                        page.buffer.insert_paintable(page.buffer.get_end_iter(), item)
                page.buffer.set_modified(True)
                page.title()
            except (GLib.Error, OSError, ValueError) as error:
                self.error(f'Could not restore {path.name}. Recovery file retained: {error}')

    def autosave(self):
        for i in range(self.tabs.get_n_pages()):
            page = self.tabs.get_nth_page(i).get_child()
            if page.buffer.get_modified() and page.recovery_pending:
                try:
                    save_recovery(page.buffer, page.recovery_path)
                    page.recovery_pending = False
                    self.recovery_error = False
                except (GLib.Error, OSError) as error:
                    if not self.recovery_error:
                        self.error(f'Autosave recovery failed: {error}')
                    self.recovery_error = True

    def clear_recovery(self, page):
        try:
            page.recovery_path.unlink(missing_ok=True)
        except OSError as error:
            self.error(f'Could not remove recovery copy: {error}')

    def display_option(self, action, state):
        action.set_state(state)
        for i in range(self.tabs.get_n_pages()):
            view = self.tabs.get_nth_page(i).get_child().view
            if action.get_name() == 'line-numbers':
                view.numbers.set_visible(state.get_boolean())
            else:
                view.highlight_current_line = state.get_boolean()
            view.refresh_decorations()
        self.save_display_preferences()

    def change_font_size(self, step):
        size = max(6, min(72, self.font_size + step))
        if size == self.font_size:
            return
        self.font_size = size
        for i in range(self.tabs.get_n_pages()):
            self.tabs.get_nth_page(i).get_child().view.set_font_size(size)
        self.save_display_preferences()

    def save_display_preferences(self):
        settings = {name: self.lookup_action(name).get_state().get_boolean()
                    for name in ('line-numbers', 'highlight-current-line')}
        settings['font-size'] = self.font_size
        try:
            save_preferences(self.preferences_path, settings)
        except (GLib.Error, OSError) as error:
            self.error(f'Display settings could not be saved: {error}')

    def current(self):
        tab = self.tabs.get_selected_page()
        return tab.get_child() if tab else None

    def new(self):
        page = Page(self)
        tab = self.tabs.append(page)
        self.tabs.set_selected_page(tab)
        page.title()
        page.view.grab_focus()
        return page

    def position(self, page):
        pos = page.buffer.get_iter_at_mark(page.buffer.get_insert())
        self.status.set_text(f'Ln {pos.get_line() + 1}, Col {pos.get_line_offset() + 1}')

    def error(self, error):
        dialog = Adw.MessageDialog(transient_for=self, heading='Could not complete action', body=str(error))
        dialog.add_response('ok', 'OK')
        dialog.present()

    def choose(self, kind, page=None, done=None):
        dialog = Gtk.FileChooserNative(title={'open': 'Open Document', 'save': 'Save Document', 'image': 'Insert Image'}[kind],
                                      transient_for=self, action=Gtk.FileChooserAction.SAVE if kind == 'save' else Gtk.FileChooserAction.OPEN,
                                      accept_label='Save' if kind == 'save' else 'Open', cancel_label='Cancel')
        if kind == 'save':
            has_images = any('png' in p for p in parts(page.buffer))
            name = page.path.name if page.path else 'Untitled.txt'
            if has_images:
                name = str(Path(name).with_suffix('.jot'))
            dialog.set_current_name(name)
        if kind == 'image':
            filter_ = Gtk.FileFilter()
            filter_.set_name('Images')
            filter_.add_pixbuf_formats()
            dialog.add_filter(filter_)
        def response(chooser, result):
            success = False
            try:
                if result == Gtk.ResponseType.ACCEPT:
                    path = chooser.get_file().get_path()
                    if path is None:
                        raise ValueError('Choose a local file')
                    path = Path(path)
                    if kind == 'open':
                        self.open_path(path)
                        success = True
                    elif kind == 'image':
                        page.insert_image(Gdk.Texture.new_from_filename(str(path)))
                        success = True
                    else:
                        success = self.save_page(page, path)
            except (GLib.Error, OSError, ValueError) as error:
                self.error(error)
            finally:
                chooser.destroy()
            if done:
                done(success)
        dialog.connect('response', response)
        dialog.show()

    def open_path(self, path):
        content = load(path)
        imported_image = len(content) == 1 and isinstance(content[0], Gdk.Texture) and path.suffix.lower() != '.jot'
        page = self.new()
        for item in content:
            end = page.buffer.get_end_iter()
            if isinstance(item, str):
                page.buffer.insert(end, item)
            else:
                page.buffer.insert_paintable(end, item)
        page.path = None if imported_image else path
        page.buffer.set_modified(imported_image)
        page.title()
        page.buffer.place_cursor(page.buffer.get_start_iter())

    def save_page(self, page, path=None, done=None):
        path = path or page.path
        if path is None or (path.suffix.lower() != '.jot' and any('png' in p for p in parts(page.buffer))):
            self.choose('save', page, done)
            return False
        try:
            save(page.buffer, path)
            page.path = path
            page.buffer.set_modified(False)
            self.clear_recovery(page)
            page.title()
            if done:
                done(True)
            return True
        except (GLib.Error, OSError, ValueError) as error:
            self.error(error)
            if done:
                done(False)
            return False

    def confirm(self, page, done):
        dialog = Adw.MessageDialog(transient_for=self, heading='Save changes?', body=page.path.name if page.path else 'Untitled document')
        for key, label in [('cancel', 'Cancel'), ('discard', 'Discard'), ('save', 'Save')]:
            dialog.add_response(key, label)
        dialog.set_response_appearance('discard', Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response('save')
        dialog.set_close_response('cancel')
        def response(d, key):
            if key == 'save':
                self.save_page(page, done=done)
            else:
                done(key == 'discard')
        dialog.connect('response', response)
        dialog.present()

    def close_page(self, view, tab):
        page = tab.get_child()
        def finish(ok):
            if ok:
                self.clear_recovery(page)
            view.close_page_finish(tab, ok)
            if ok and view.get_n_pages() == 0:
                self.new()
        if page.buffer.get_modified():
            self.confirm(page, finish)
        else:
            finish(True)
        return True

    def close_window(self, *_):
        dirty = [self.tabs.get_nth_page(i).get_child() for i in range(self.tabs.get_n_pages())
                 if self.tabs.get_nth_page(i).get_child().buffer.get_modified()]
        def next_page(ok=True):
            if not ok:
                return
            if dirty:
                self.confirm(dirty.pop(0), next_page)
            else:
                for i in range(self.tabs.get_n_pages()):
                    self.clear_recovery(self.tabs.get_nth_page(i).get_child())
                self.destroy()
        next_page()
        return True

    def show_find(self):
        self.searchbar.set_search_mode(True)
        self.search.grab_focus()

    def find(self):
        page, query = self.current(), self.search.get_text()
        if not page or not query:
            return
        pos = page.buffer.get_iter_at_mark(page.buffer.get_insert())
        match = pos.forward_search(query, Gtk.TextSearchFlags.TEXT_ONLY, None)
        if not match:
            match = page.buffer.get_start_iter().forward_search(query, Gtk.TextSearchFlags.TEXT_ONLY, None)
        if match:
            page.buffer.select_range(*match)
            page.view.scroll_to_iter(match[0], 0.1, False, 0, 0)


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id='local.ImageTextEditor', flags=Gio.ApplicationFlags.HANDLES_OPEN)

    def do_startup(self):
        Adw.Application.do_startup(self)
        self.autosave_timer = GLib.timeout_add_seconds(5, self.autosave)

    def autosave(self):
        for window in self.get_windows():
            window.autosave()
        return GLib.SOURCE_CONTINUE

    def do_shutdown(self):
        GLib.source_remove(self.autosave_timer)
        Adw.Application.do_shutdown(self)

    def do_activate(self):
        window = self.get_active_window() or Window(self)
        window.present()

    def do_open(self, files, count, hint):
        self.do_activate()
        for file in files:
            try:
                self.get_active_window().open_path(Path(file.get_path()))
            except (GLib.Error, OSError, ValueError, TypeError) as error:
                self.get_active_window().error(error)


if __name__ == '__main__':
    sys.exit(App().run(sys.argv))

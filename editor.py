#!/usr/bin/python3
"""Native text editor with embedded clipboard images. Run with system Python."""
import base64
import json
from pathlib import Path
import sys
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Graphene', '1.0')
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Graphene, Gtk


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
    text = path.read_text(encoding='utf-8')
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
        self.numbers = LineNumbers(self)
        self.set_gutter(Gtk.TextWindowType.LEFT, self.numbers)
        self.get_buffer().connect('changed', self.refresh_decorations)
        self.get_buffer().connect('notify::cursor-position', self.refresh_decorations)

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


class Page(Gtk.ScrolledWindow):
    def __init__(self, owner):
        super().__init__(hexpand=True, vexpand=True)
        self.owner, self.path = owner, None
        self.buffer = Gtk.TextBuffer(enable_undo=True)
        self.view = EditorView(buffer=self.buffer, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR,
                                 left_margin=30, right_margin=30, top_margin=24, bottom_margin=24)
        self.view.numbers.set_visible(owner.lookup_action('line-numbers').get_state().get_boolean())
        self.view.highlight_current_line = owner.lookup_action('highlight-current-line').get_state().get_boolean()
        self.view.refresh_decorations()
        self.set_child(self.view)
        self.get_vadjustment().connect('value-changed', self.view.refresh_decorations)
        self.view.connect('paste-clipboard', self.paste)
        self.buffer.connect('modified-changed', lambda *_: self.title())
        self.buffer.connect('notify::cursor-position', lambda *_: owner.position(self))

    def title(self):
        tab = self.owner.tabs.get_page(self)
        if tab:
            tab.set_title(('• ' if self.buffer.get_modified() else '') + (self.path.name if self.path else 'Untitled'))

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
        for label, action in [('Save As…', 'save-as'), ('Paste', 'paste'), ('Find…', 'find'), ('Close Tab', 'close')]:
            menu.append(label, 'win.' + action)
        for label, name in [('Display Line Numbers', 'line-numbers'),
                            ('Highlight Current Line', 'highlight-current-line')]:
            action = Gio.SimpleAction.new_stateful(name, None, GLib.Variant('b', False))
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
        self.connect('close-request', self.close_window)
        self.new()

    def display_option(self, action, state):
        action.set_state(state)
        for i in range(self.tabs.get_n_pages()):
            view = self.tabs.get_nth_page(i).get_child().view
            if action.get_name() == 'line-numbers':
                view.numbers.set_visible(state.get_boolean())
            else:
                view.highlight_current_line = state.get_boolean()
            view.refresh_decorations()

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
        page = self.new()
        for item in content:
            end = page.buffer.get_end_iter()
            if isinstance(item, str):
                page.buffer.insert(end, item)
            else:
                page.buffer.insert_paintable(end, item)
        page.path = path
        page.buffer.set_modified(False)
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

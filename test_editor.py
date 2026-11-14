import tempfile
import time
import unittest
from unittest.mock import patch
from pathlib import Path
from editor import App, Gdk, GLib, Gtk, Window, load, parts, save, load_preferences, save_preferences, resize_texture, save_recovery, corner_width, replace_image


class EditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Gtk.init_check()
        if Gdk.Display.get_default() is None:
            raise unittest.SkipTest("No display connection; run from a desktop terminal")
        cls.app = App()
        cls.app.register(None)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        config = patch('editor.GLib.get_user_config_dir', return_value=str(self.root))
        config.start()
        self.addCleanup(config.stop)
        state = patch('editor.GLib.get_user_state_dir', return_value=str(self.root / 'state'))
        state.start()
        self.addCleanup(state.stop)
        self.window = Window(self.app)
        self.page = self.window.current()
        self.texture = Gdk.MemoryTexture.new(2, 2, Gdk.MemoryFormat.R8G8B8A8,
                                            GLib.Bytes.new(bytes([255, 0, 0, 255]) * 4), 8)

    def tearDown(self):
        self.window.destroy()
        self.temp.cleanup()

    def test_roundtrip_and_loss_guard(self):
        buffer = self.page.buffer
        buffer.set_text('Before فارسی\n')
        buffer.place_cursor(buffer.get_end_iter())
        self.page.insert_image(self.texture)
        buffer.insert(buffer.get_end_iter(), '\nAfter\ufffc')
        path = self.root / 'test.jot'
        save(buffer, path)
        self.window.open_path(path)
        self.assertEqual(parts(buffer), parts(self.window.current().buffer))
        plain = self.root / 'existing.txt'
        plain.write_text('Keep me')
        with self.assertRaises(ValueError):
            save(buffer, plain)
        self.assertEqual(plain.read_text(), 'Keep me')
        buffer.set_text('plain\nفارسی')
        save(buffer, plain)
        self.assertEqual(load(plain), ['plain\nفارسی'])

    def test_clipboard(self):
        clipboard = self.page.view.get_clipboard()
        clipboard.set(self.texture)
        self.page.view.emit('paste-clipboard')
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not parts(self.page.buffer):
            GLib.MainContext.default().iteration(False)
            time.sleep(.01)
        self.assertIn('png', parts(self.page.buffer)[0])

    def test_invalid_document_does_not_create_tab(self):
        path = self.root / 'bad.jot'
        for text in ['{}', '{"format":"image-text-1","parts":[{"png":"!"}]}']:
            path.write_text(text)
            count = self.window.tabs.get_n_pages()
            with self.assertRaises((ValueError, GLib.Error)):
                self.window.open_path(path)
            self.assertEqual(self.window.tabs.get_n_pages(), count)

    def test_open_image_creates_unsaved_document(self):
        source = Path(__file__).parent / 'tests/fixtures/image.jpg'
        original = source.read_bytes()
        self.window.open_path(source)
        page = self.window.current()
        self.assertIsNone(page.path)
        self.assertTrue(page.buffer.get_modified())
        self.assertIn('png', parts(page.buffer)[0])
        destination = self.root / 'imported.jot'
        self.assertTrue(self.window.save_page(page, destination))
        self.assertEqual(load(destination)[0].get_width(), 2)
        self.assertEqual(source.read_bytes(), original)

    def test_close_cancel_keeps_modified_tab(self):
        self.page.buffer.set_text('unsaved')
        self.window.confirm = lambda page, done: done(False)
        self.window.tabs.close_page(self.window.tabs.get_selected_page())
        self.assertEqual(self.window.tabs.get_n_pages(), 1)
        self.assertTrue(self.page.buffer.get_modified())

    def test_display_options_apply_to_existing_and_new_tabs(self):
        self.page.buffer.set_text('Wrapped text ' * 30 + '\n\n')
        self.page.buffer.set_modified(False)
        original = parts(self.page.buffer)
        for name in ('line-numbers', 'highlight-current-line'):
            action = self.window.lookup_action(name)
            self.assertFalse(action.get_state().get_boolean())
            action.activate(None)
            self.assertTrue(action.get_state().get_boolean())
        other = self.window.new()
        for page in (self.page, other):
            self.assertTrue(page.view.numbers.get_visible())
            self.assertTrue(page.view.highlight_current_line)
        self.page.buffer.place_cursor(self.page.buffer.get_end_iter())
        self.assertFalse(self.page.buffer.get_modified())
        self.assertEqual(parts(self.page.buffer), original)
        for name in ('line-numbers', 'highlight-current-line'):
            self.window.lookup_action(name).activate(None)
        for page in (self.page, other, self.window.new()):
            self.assertFalse(page.view.numbers.get_visible())
            self.assertFalse(page.view.highlight_current_line)

    def test_display_options_survive_window_restart(self):
        for name in ('line-numbers', 'highlight-current-line'):
            self.window.lookup_action(name).activate(None)
        self.window.destroy()
        self.window = Window(self.app)
        for name in ('line-numbers', 'highlight-current-line'):
            self.assertTrue(self.window.lookup_action(name).get_state().get_boolean())
        self.assertTrue(self.window.current().view.numbers.get_visible())
        self.assertTrue(self.window.current().view.highlight_current_line)
        self.assertFalse(self.window.current().buffer.get_modified())

    def test_font_size_actions_and_persistence(self):
        self.page.buffer.set_text('Text فارسی')
        self.page.buffer.set_modified(False)
        original = parts(self.page.buffer)
        other = self.window.new()
        self.window.lookup_action('font-increase').activate(None)
        self.assertEqual(self.window.font_size, 13)
        for page in (self.page, other, self.window.new()):
            self.assertIn('13pt', page.view.font_css.to_string())
        self.assertEqual(parts(self.page.buffer), original)
        self.assertFalse(self.page.buffer.get_modified())
        self.window.lookup_action('line-numbers').activate(None)
        self.assertEqual(load_preferences(self.window.preferences_path)['font-size'], 13)
        for action, keys in [('font-increase', ('<Control>plus', '<Control>equal', '<Control>KP_Add')),
                             ('font-decrease', ('<Control>minus', '<Control>KP_Subtract'))]:
            self.assertEqual(set(self.app.get_accels_for_action('win.' + action)), set(keys))
        self.window.destroy()
        self.window = Window(self.app)
        self.assertEqual(self.window.font_size, 13)
        self.assertTrue(self.window.lookup_action('line-numbers').get_state().get_boolean())
        self.window.lookup_action('font-decrease').activate(None)
        self.assertEqual(self.window.font_size, 12)
        self.window.change_font_size(-1000)
        self.assertEqual(self.window.font_size, 6)
        self.window.lookup_action('font-decrease').activate(None)
        self.assertEqual(self.window.font_size, 6)
        self.window.change_font_size(1000)
        self.assertEqual(self.window.font_size, 72)
        self.window.lookup_action('font-increase').activate(None)
        self.assertEqual(self.window.font_size, 72)

    def test_recovery_survives_crash_and_clears_on_save(self):
        self.page.buffer.set_text('Recover me فارسی')
        self.page.insert_image(self.texture)
        expected = parts(self.page.buffer)
        self.window.autosave()
        recovery = self.page.recovery_path
        self.assertTrue(recovery.exists())
        self.window.destroy()
        self.window = Window(self.app)
        restored = self.window.current()
        self.assertEqual(parts(restored.buffer), expected)
        self.assertTrue(restored.buffer.get_modified())
        self.assertIsNone(restored.path)
        self.assertTrue(self.window.save_page(restored, self.root / 'saved.jot'))
        self.assertFalse(recovery.exists())

    def test_recovery_cancel_preserves_and_discard_removes(self):
        self.page.buffer.set_text('unsaved')
        self.window.autosave()
        recovery = self.page.recovery_path
        self.window.confirm = lambda page, done: done(False)
        self.window.tabs.close_page(self.window.tabs.get_selected_page())
        self.assertTrue(recovery.exists())
        self.window.confirm = lambda page, done: done(True)
        self.window.tabs.close_page(self.window.tabs.get_selected_page())
        self.assertFalse(recovery.exists())

    def test_drop_texture_and_invalid_file(self):
        self.assertTrue(self.page.drop_image(None, self.texture, 0, 0))
        before = parts(self.page.buffer)
        from editor import Gio
        invalid = Gdk.FileList.new_from_list([Gio.File.new_for_path(str(self.root / 'missing.jpg'))])
        with patch.object(self.window, 'error') as error:
            self.assertFalse(self.page.drop_image(None, invalid, 0, 0))
            error.assert_called_once()
        self.assertEqual(parts(self.page.buffer), before)

    def test_corner_drag_commit_and_cancel(self):
        image = resize_texture(self.texture, 80)
        self.page.insert_image(image)
        self.page.buffer.set_modified(False)
        view = self.page.view
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = 30, 24, 80, 80
        from unittest.mock import Mock
        gesture = Mock()
        with patch.object(view, 'window_to_buffer_coords', side_effect=lambda win, x, y: (x, y)), \
                patch.object(view, 'get_iter_location', return_value=rect):
            view.resize_begin(gesture, 110, 104)
            self.assertIsNotNone(view.image_drag)
            view.resize_update(gesture, 20, 20)
            self.assertFalse(self.page.buffer.get_modified())
            view.resize_end(gesture, 20, 20)
            self.assertEqual(view.selected_image().get_paintable().get_width(), 100)
            self.assertTrue(self.page.buffer.get_modified())
            self.assertTrue(self.page.recovery_pending)
            self.page.buffer.set_modified(False)
            view.resize_begin(gesture, 30, 24)
            view.resize_update(gesture, -20, -20)
            self.assertTrue(view.resize_key(None, Gdk.KEY_Escape, 0, 0))
            view.resize_end(gesture, -20, -20)
            self.assertEqual(view.selected_image().get_paintable().get_width(), 100)
            self.assertFalse(self.page.buffer.get_modified())
            view.resize_begin(gesture, 500, 500)
            self.assertIsNone(view.image_drag)


class StorageTests(unittest.TestCase):
    def test_corner_geometry_and_image_replacement(self):
        for sx, sy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
            self.assertEqual(corner_width(100, 50, sx * 20, sy * 10, sx, sy), 120)
            self.assertEqual(corner_width(100, 50, -sx * 20, -sy * 10, sx, sy), 80)
        self.assertEqual(corner_width(100, 200, 1e6, 1e6, 1, 1), 8192)
        self.assertEqual(corner_width(100, 50, -1e6, -1e6, 1, 1), 1)
        image = Gdk.Texture.new_from_filename(str(Path(__file__).parent / 'tests/fixtures/image.jpg'))
        buffer = Gtk.TextBuffer()
        buffer.set_text('before')
        buffer.insert_paintable(buffer.get_end_iter(), image)
        buffer.insert(buffer.get_end_iter(), 'after')
        pos = buffer.get_iter_at_offset(6)
        mark = buffer.create_mark(None, pos, True)
        buffer.set_modified(False)
        replace_image(buffer, mark, image, image.get_width())
        self.assertFalse(buffer.get_modified())
        replace_image(buffer, mark, image, 10)
        self.assertTrue(buffer.get_modified())
        self.assertEqual(parts(buffer)[0], {'text': 'before'})
        self.assertEqual(parts(buffer)[-1], {'text': 'after'})
        self.assertEqual(buffer.get_iter_at_mark(mark).get_paintable().get_width(), 10)
        before = parts(buffer)
        with self.assertRaises(ValueError):
            replace_image(buffer, mark, image, 20)
        self.assertEqual(parts(buffer), before)
        buffer.delete_mark(mark)

    def test_resize_and_recovery_roundtrip(self):
        image = Gdk.Texture.new_from_filename(str(Path(__file__).parent / 'tests/fixtures/image.jpg'))
        resized = resize_texture(image, 10)
        self.assertEqual((resized.get_width(), resized.get_height()), (10, 10))
        wide = Gdk.MemoryTexture.new(4, 2, Gdk.MemoryFormat.R8G8B8A8,
                                     GLib.Bytes.new(bytes([255, 0, 0, 255]) * 8), 16)
        self.assertEqual(resize_texture(wide, 6).get_height(), 3)
        for width in (0, -1, 16385):
            with self.assertRaises(ValueError):
                resize_texture(image, width)
        buffer = Gtk.TextBuffer()
        buffer.set_text('Recovered text فارسی\n')
        buffer.insert_paintable(buffer.get_end_iter(), resized)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'recovery/session.jot'
            save_recovery(buffer, path)
            restored = load(path)
            self.assertEqual(restored[0], 'Recovered text فارسی\n')
            self.assertEqual(restored[1].get_width(), 10)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            buffer.set_text('new snapshot')
            save_recovery(buffer, path)
            self.assertEqual(load(path), ['new snapshot'])
            original = path.read_bytes()
            with patch('editor.Gio.File.new_for_path', side_effect=OSError('Disk full')):
                with self.assertRaises(OSError):
                    save_recovery(buffer, path)
            self.assertEqual(path.read_bytes(), original)

    def test_preferences_roundtrip_and_invalid_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config/settings.json'
            defaults = {'line-numbers': False, 'highlight-current-line': False, 'font-size': 12}
            self.assertEqual(load_preferences(path), defaults)
            for settings in ({'line-numbers': True, 'highlight-current-line': True}, defaults):
                save_preferences(path, settings)
                self.assertEqual(load_preferences(path), {**defaults, **settings})
            path.write_text('{"line-numbers": true, "highlight-current-line": "false"}')
            self.assertEqual(load_preferences(path), {**defaults, 'line-numbers': True})
            for size in (6, 13, 72, 0, 73, '18', True, None, 12.5):
                save_preferences(path, {**defaults, 'font-size': size})
                expected = size if type(size) is int and 6 <= size <= 72 else 12
                self.assertEqual(load_preferences(path)['font-size'], expected)
            for invalid in ('{broken', '[]', '\ufffd'):
                path.write_text(invalid)
                with patch('sys.stderr'):
                    self.assertEqual(load_preferences(path), defaults)
            path.unlink()
            path.parent.rmdir()
            path.parent.write_text('not a directory')
            with self.assertRaises(OSError):
                save_preferences(path, defaults)

    def test_image_files_are_decoded_before_text(self):
        jpeg = (Path(__file__).parent / 'tests/fixtures/image.jpg').read_bytes()
        with tempfile.TemporaryDirectory() as directory:
            for name in ('download.jpg', 'download.JPEG', 'download'):
                with self.subTest(name=name):
                    path = Path(directory) / name
                    path.write_bytes(jpeg)
                    image, = load(path)
                    self.assertIsInstance(image, Gdk.Texture)
                    self.assertEqual((image.get_width(), image.get_height()), (2, 2))
                    self.assertEqual(path.read_bytes(), jpeg)
            png = Path(directory) / 'screenshot.png'
            image.save_to_png(str(png))
            self.assertIsInstance(load(png)[0], Gdk.Texture)
            broken = Path(directory) / 'broken.jpg'
            broken.write_bytes(b'\xff\xd8\xff\xe0broken')
            with self.assertRaises(GLib.Error):
                load(broken)
            binary = Path(directory) / 'binary.dat'
            binary.write_bytes(b'\xff\x00unsupported')
            with self.assertRaisesRegex(ValueError, 'UTF-8 text document'):
                load(binary)

    def test_storage_without_display(self):
        buffer = Gtk.TextBuffer()
        buffer.set_text('Before فارسی\n')
        image = Gdk.MemoryTexture.new(1, 1, Gdk.MemoryFormat.R8G8B8A8,
                                      GLib.Bytes.new(bytes([255, 0, 0, 255])), 4)
        buffer.insert_paintable(buffer.get_end_iter(), image)
        buffer.insert(buffer.get_end_iter(), '\nAfter\ufffc')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'document.jot'
            save(buffer, path)
            restored = Gtk.TextBuffer()
            for item in load(path):
                if isinstance(item, str):
                    restored.insert(restored.get_end_iter(), item)
                else:
                    restored.insert_paintable(restored.get_end_iter(), item)
            self.assertEqual(parts(buffer), parts(restored))
            plain = path.with_suffix('.txt')
            plain.write_text('Keep me')
            with self.assertRaises(ValueError):
                save(buffer, plain)
            self.assertEqual(plain.read_text(), 'Keep me')
            buffer.set_text('text only\nفارسی')
            save(buffer, plain)
            self.assertEqual(load(plain), ['text only\nفارسی'])
            path.write_text('{}')
            with self.assertRaises(ValueError):
                load(path)
            path.write_text('{"format":"image-text-1","parts":[{"png":"!"}]}')
            with self.assertRaises(ValueError):
                load(path)


if __name__ == '__main__':
    unittest.main()

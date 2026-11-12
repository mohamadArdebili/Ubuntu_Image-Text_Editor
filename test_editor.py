import tempfile
import time
import unittest
from pathlib import Path
from editor import App, Gdk, GLib, Gtk, Window, load, parts, save


class EditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Gtk.init_check()
        if Gdk.Display.get_default() is None:
            raise unittest.SkipTest("No display connection; run from a desktop terminal")
        cls.app = App()
        cls.app.register(None)

    def setUp(self):
        self.window = Window(self.app)
        self.page = self.window.current()
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
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


class StorageTests(unittest.TestCase):
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

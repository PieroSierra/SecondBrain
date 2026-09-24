from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))
import bridge  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"png-body"
JPEG = b"\xff\xd8\xff\xe0" + b"jpeg-body"
GIF = b"GIF89a" + b"gif-body"
WEBP = b"RIFF\x10\x00\x00\x00WEBPVP8 " + b"webp-body"

BOUNDARY = "testboundary"
CTYPE = f"multipart/form-data; boundary={BOUNDARY}"

THREAD = (
    '<!-- sb:thread id="2026-09-24_thread-x" created="2026-09-24" -->\n\n'
    "# X\n\n"
    '<!-- sb:turn role="user" ts="2026-09-24" -->\n'
    "## You\n\n"
    "What is this?\n\n"
    '<!-- sb:turn role="assistant" ts="2026-09-24" -->\n'
    "## Second Brain\n\n"
    "An answer.\n\n"
    "<!-- sb:thread-end -->\n"
)


def multipart(fields: dict[str, str], files: list[tuple[str, str, bytes]]) -> bytes:
    out = b""
    for name, value in fields.items():
        out += (
            f"--{BOUNDARY}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n"
        ).encode()
    for name, filename, body in files:
        out += (
            f"--{BOUNDARY}\r\n"
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode() + body + b"\r\n"
    return out + f"--{BOUNDARY}--\r\n".encode()


class ParsingTests(unittest.TestCase):
    def test_parse_multipart_keeps_every_file_in_order(self) -> None:
        body = multipart(
            {"kind": "thread-start", "args": "{}"},
            [("image", "image.png", PNG), ("image", "image.png", JPEG)],
        )
        files, fields = bridge._parse_multipart(CTYPE, body)
        self.assertEqual(fields, {"kind": "thread-start", "args": "{}"})
        self.assertEqual(
            files, [("image", "image.png", PNG), ("image", "image.png", JPEG)]
        )

    def test_single_file_wrapper_returns_first_file(self) -> None:
        body = multipart({"context": "c"}, [("file", "a.png", PNG), ("file", "b.png", GIF)])
        filename, data, fields = bridge._parse_multipart_pdf(CTYPE, body)
        self.assertEqual((filename, data, fields), ("a.png", PNG, {"context": "c"}))

    def test_single_file_wrapper_requires_a_file(self) -> None:
        with self.assertRaises(bridge.UploadError):
            bridge._parse_multipart_pdf(CTYPE, multipart({"context": "c"}, []))

    def test_sniff_image_ext(self) -> None:
        self.assertEqual(bridge._sniff_image_ext(PNG), ".png")
        self.assertEqual(bridge._sniff_image_ext(JPEG), ".jpg")
        self.assertEqual(bridge._sniff_image_ext(GIF), ".gif")
        self.assertEqual(bridge._sniff_image_ext(WEBP), ".webp")
        self.assertIsNone(bridge._sniff_image_ext(b"plain text renamed to .png"))
        self.assertIsNone(bridge._sniff_image_ext(b"<svg xmlns='x'></svg>"))

    def test_validate_images_uses_sniffed_extension(self) -> None:
        out = bridge._validate_images([("image", "photo.png", JPEG)])
        self.assertEqual(out, [(JPEG, ".jpg")])

    def test_validate_images_limits(self) -> None:
        with self.assertRaises(bridge.UploadError):
            bridge._validate_images([("image", "t.png", b"text")])
        with self.assertRaises(bridge.UploadError):
            bridge._validate_images([("image", "a.png", PNG)] * (bridge._MAX_IMAGES + 1))
        with mock.patch.object(bridge, "_MAX_IMAGE_BYTES", 10):
            with self.assertRaises(bridge.UploadError):
                bridge._validate_images([("image", "a.png", PNG)])
        with mock.patch.object(bridge, "_MAX_UPLOAD_AGENTIC_BYTES", len(PNG) + 1):
            with self.assertRaises(bridge.UploadError):
                bridge._validate_images([("image", "a.png", PNG), ("image", "b.png", PNG)])


class PromptTests(unittest.TestCase):
    def test_query_puts_image_flags_before_question(self) -> None:
        prompt, err = bridge._format_prompt(
            "thread-start",
            {"question": 'what is "this"?', "image_paths": ["/v/a.png", "/v/b.jpg"]},
        )
        self.assertIsNone(err)
        self.assertEqual(
            prompt,
            '/second-brain-query --image "/v/a.png" --image "/v/b.jpg" '
            '"what is \\"this\\"?"',
        )

    def test_query_without_images_is_unchanged(self) -> None:
        prompt, _ = bridge._format_prompt("thread-start", {"question": "q"})
        self.assertEqual(prompt, '/second-brain-query "q"')

    def test_follow_up_puts_image_flags_between_thread_and_question(self) -> None:
        prompt, err = bridge._format_prompt(
            "thread-reply",
            {
                "question": "and this?",
                "thread_file": "outputs/2026-09-24_thread-x.md",
                "image_paths": ["/v/a.png"],
            },
        )
        self.assertIsNone(err)
        self.assertEqual(
            prompt,
            '/second-brain-follow-up --thread "outputs/2026-09-24_thread-x.md" '
            '--image "/v/a.png" "and this?"',
        )

    def test_file_import_passes_source_name(self) -> None:
        prompt = bridge._build_file_import(
            {"file_path": "/v/.uploads/x.png", "context": "", "source_name": "Screen Shot.png"}
        )
        self.assertEqual(
            prompt,
            '/second-brain-import-file "/v/.uploads/x.png" --source-name "Screen Shot.png"',
        )

    def test_codex_and_opencode_attach_images_last(self) -> None:
        captured = {}

        def fake_capture(argv, timeout):
            captured["argv"] = argv
            return None, "stopped"

        with mock.patch.object(bridge, "_run_capture", fake_capture):
            bridge.run_codex('/second-brain-query "q"', {"timeout": 5}, ["/a.png", "/b.png"])
            self.assertEqual(captured["argv"][-4:], ["-i", "/a.png", "-i", "/b.png"])
            bridge.run_opencode('/second-brain-query "q"', {"timeout": 5}, ["/a.png"])
            self.assertEqual(captured["argv"][-2:], ["-f", "/a.png"])


class VaultTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp.name)
        for sub in ("raw", "dashboard/.uploads", "wiki", "outputs"):
            (self.vault / sub).mkdir(parents=True)
        self.globals = mock.patch.multiple(
            bridge,
            VAULT_ROOT=self.vault,
            DASHBOARD_DIR=self.vault / "dashboard",
            RAW_DIR=self.vault / "raw",
            WIKI_DIR=self.vault / "wiki",
            OUTPUTS_DIR=self.vault / "outputs",
            ATTACHMENTS_DIR=self.vault / "outputs" / "attachments",
            INGEST_MANIFEST=self.vault / "raw" / ".ingest-manifest.json",
        )
        self.globals.start()
        self.handler = object.__new__(bridge.DashboardHandler)

    def tearDown(self) -> None:
        self.globals.stop()
        self.temp.cleanup()

    @property
    def uploads(self) -> Path:
        return self.vault / "dashboard" / ".uploads"

    @property
    def attachments(self) -> Path:
        return self.vault / "outputs" / "attachments"

    def stage(self, *bodies: bytes) -> list[Path]:
        return [bridge._stage_image(b, bridge._sniff_image_ext(b)) for b in bodies]

    def post(self, body: bytes) -> tuple[int, dict]:
        """Drive _handle_run_multipart with a raw multipart body."""
        self.handler.headers = {"Content-Length": str(len(body)), "Content-Type": CTYPE}
        self.handler.rfile = io.BytesIO(body)
        sent = {}

        def fake_response(_handler, status, payload):
            sent["status"], sent["payload"] = status, payload

        with mock.patch.object(bridge, "_json_response", fake_response):
            self.handler._handle_run_multipart()
        return sent["status"], sent["payload"]


class ThreadAttachmentTests(VaultTestCase):
    def test_insert_after_first_user_turn(self) -> None:
        out = bridge._insert_turn_images(THREAD, ["![image 1](attachments/x-1.png)"])
        self.assertIn(
            "What is this?\n\n![image 1](attachments/x-1.png)\n\n"
            '<!-- sb:turn role="assistant"',
            out,
        )

    def test_insert_after_latest_user_turn_before_end_marker(self) -> None:
        text = THREAD.replace(
            "<!-- sb:thread-end -->",
            '<!-- sb:turn role="user" ts="2026-09-24" -->\n## You\n\nAnd this?\n\n'
            "<!-- sb:thread-end -->",
        )
        out = bridge._insert_turn_images(text, ["![image 2](attachments/x-2.png)"])
        self.assertTrue(out.endswith(
            "And this?\n\n![image 2](attachments/x-2.png)\n\n<!-- sb:thread-end -->\n"
        ))
        self.assertEqual(out.count("![image"), 1)

    def test_attach_copies_and_numbers_after_existing(self) -> None:
        thread = self.vault / "outputs" / "2026-09-24_thread-x.md"
        thread.write_text(THREAD, encoding="utf-8")
        self.attachments.mkdir()
        (self.attachments / "2026-09-24_thread-x-1.png").write_bytes(PNG)
        staged = self.stage(JPEG, GIF)

        created = bridge._attach_thread_images("outputs/2026-09-24_thread-x.md", staged)

        self.assertEqual(created, [
            "outputs/attachments/2026-09-24_thread-x-2.jpg",
            "outputs/attachments/2026-09-24_thread-x-3.gif",
        ])
        text = thread.read_text(encoding="utf-8")
        self.assertIn("![image 2](attachments/2026-09-24_thread-x-2.jpg)", text)
        self.assertIn("![image 3](attachments/2026-09-24_thread-x-3.gif)", text)

    def test_attach_is_all_or_nothing(self) -> None:
        thread = self.vault / "outputs" / "2026-09-24_thread-x.md"
        thread.write_text(THREAD, encoding="utf-8")
        staged = self.stage(PNG, JPEG, GIF)
        real_copy = bridge.shutil.copyfile
        calls = []

        def flaky_copy(src, dst):
            calls.append(dst)
            if len(calls) == 2:
                raise OSError("disk full")
            return real_copy(src, dst)

        with mock.patch.object(bridge.shutil, "copyfile", flaky_copy):
            with self.assertRaises(OSError):
                bridge._attach_thread_images("outputs/2026-09-24_thread-x.md", staged)
        self.assertEqual(list(self.attachments.iterdir()), [])
        self.assertEqual(thread.read_text(encoding="utf-8"), THREAD)

    def test_thread_start_with_images_attaches_and_cleans_staging(self) -> None:
        def fake_run(prompt, cfg, images=None):
            self.assertIn("--image", prompt)
            self.assertEqual(len(images), 2)
            (self.vault / "outputs" / "2026-09-24_thread-x.md").write_text(THREAD, encoding="utf-8")
            return 200, {"result": "ok", "is_error": False}

        body = multipart(
            {"kind": "thread-start", "args": json.dumps({"question": "What is this?"})},
            [("image", "image.png", PNG), ("image", "image.png", PNG)],
        )
        with mock.patch.object(bridge, "run_skill", fake_run):
            status, payload = self.post(body)

        self.assertEqual(status, 200)
        self.assertEqual(payload["output_file"], "outputs/2026-09-24_thread-x.md")
        self.assertEqual(sorted(p.name for p in self.attachments.iterdir()), [
            "2026-09-24_thread-x-1.png", "2026-09-24_thread-x-2.png",
        ])
        self.assertEqual(list(self.uploads.iterdir()), [])

    def test_image_only_question_gets_default_text(self) -> None:
        seen = {}

        def fake_run(prompt, cfg, images=None):
            seen["prompt"] = prompt
            return 200, {"stopped": True}

        body = multipart(
            {"kind": "thread-start", "args": json.dumps({"question": "  "})},
            [("image", "image.png", PNG)],
        )
        with mock.patch.object(bridge, "run_skill", fake_run):
            self.post(body)
        self.assertIn(bridge._IMAGE_ONLY_QUESTION, seen["prompt"])

    def test_stopped_run_leaves_no_images(self) -> None:
        def fake_run(prompt, cfg, images=None):
            (self.vault / "outputs" / "2026-09-24_thread-x.md").write_text(THREAD, encoding="utf-8")
            return 200, {"stopped": True}

        body = multipart(
            {"kind": "thread-start", "args": json.dumps({"question": "q"})},
            [("image", "a.png", PNG)],
        )
        with mock.patch.object(bridge, "run_skill", fake_run):
            status, payload = self.post(body)

        self.assertEqual((status, payload.get("stopped")), (200, True))
        self.assertFalse(self.attachments.exists())
        self.assertEqual(list(self.uploads.iterdir()), [])

    def test_rejects_bad_image_before_running(self) -> None:
        body = multipart(
            {"kind": "thread-start", "args": json.dumps({"question": "q"})},
            [("image", "a.png", b"not an image")],
        )
        with mock.patch.object(bridge, "run_skill") as run:
            status, payload = self.post(body)
        self.assertEqual(status, 400)
        run.assert_not_called()
        self.assertEqual(list(self.uploads.iterdir()), [])

    def test_rejects_unsupported_kind(self) -> None:
        body = multipart({"kind": "ingest", "args": "{}"}, [("image", "a.png", PNG)])
        status, _ = self.post(body)
        self.assertEqual(status, 400)


class PasteWithImagesTests(VaultTestCase):
    REPLY = "```json\n" + json.dumps({"images": [
        {"index": 1, "title": "Flow diagram", "description": "A flow.",
         "visible_text": "Start -> End", "content_date": ""},
        {"index": 2, "title": "Chart", "description": "A chart.",
         "visible_text": "", "content_date": "2026-03-01"},
    ]}) + "\n```"

    def run_paste(self, reply, markdown: str = "My notes\n\nLine two"):
        def fake_run(prompt, cfg, images=None):
            self.assertTrue(prompt.startswith("/second-brain-describe-images --image"))
            if markdown:
                self.assertIn("--context-file", prompt)
            if isinstance(reply, dict):
                return 200, reply
            return 200, {"result": reply, "is_error": False}

        body = multipart(
            {"kind": "md-add",
             "args": json.dumps({"markdown": markdown, "title_hint": "Design notes"})},
            [("image", "image.png", PNG), ("image", "image.png", JPEG)],
        )
        with mock.patch.object(bridge, "run_skill", fake_run):
            return self.post(body)

    def test_writes_one_note_with_text_verbatim_and_originals(self) -> None:
        status, payload = self.run_paste(self.REPLY)
        self.assertEqual(status, 200, payload)
        folder = self.vault / "raw" / "images"
        names = sorted(p.name for p in folder.iterdir())
        [note_name] = [n for n in names if n.endswith(".md")]
        stem = note_name[:-3]
        self.assertEqual(names, sorted([note_name, f"{stem}__1.png", f"{stem}__2.jpg"]))
        note = (folder / note_name).read_text(encoding="utf-8")
        self.assertIn("title: Design notes", note)
        self.assertIn("content_date: 2026-03-01", note)
        self.assertIn(f"  - {stem}__1.png", note)
        self.assertIn("My notes\n\nLine two", note)
        self.assertIn(
            f"### Image 1 — Flow diagram\n\n![image 1]({stem}__1.png)\n\nA flow.", note
        )
        self.assertIn("Start -> End", note)
        self.assertEqual(payload["created_files"][0], f"raw/images/{note_name}")
        self.assertEqual(len(payload["created_files"]), 3)
        self.assertEqual(list(self.uploads.iterdir()), [])

    def test_images_without_text(self) -> None:
        status, payload = self.run_paste(self.REPLY, markdown="")
        self.assertEqual(status, 200, payload)
        self.assertEqual(len(payload["created_files"]), 3)

    def test_bad_json_writes_nothing(self) -> None:
        status, payload = self.run_paste("I could not see the images.")
        self.assertEqual((status, payload["error"]), (422, "bad_describe"))
        self.assertFalse((self.vault / "raw" / "images").exists())
        self.assertEqual(list(self.uploads.iterdir()), [])

    def test_wrong_image_count_writes_nothing(self) -> None:
        one = "```json\n" + json.dumps({"images": [{"description": "x"}]}) + "\n```"
        status, _ = self.run_paste(one)
        self.assertEqual(status, 422)
        self.assertFalse((self.vault / "raw" / "images").exists())

    def test_stopped_writes_nothing(self) -> None:
        status, payload = self.run_paste({"stopped": True})
        self.assertEqual((status, payload.get("stopped")), (200, True))
        self.assertFalse((self.vault / "raw" / "images").exists())
        self.assertEqual(list(self.uploads.iterdir()), [])

    def test_failed_copy_writes_nothing(self) -> None:
        real_copy = bridge.shutil.copyfile
        calls = []

        def flaky_copy(src, dst):
            calls.append(dst)
            if len(calls) == 2:
                raise OSError("disk full")
            return real_copy(src, dst)

        with mock.patch.object(bridge.shutil, "copyfile", flaky_copy):
            status, _ = self.run_paste(self.REPLY)
        self.assertEqual(status, 500)
        self.assertEqual(list((self.vault / "raw" / "images").iterdir()), [])


class KeepOriginalTests(VaultTestCase):
    def test_copies_original_next_to_note(self) -> None:
        folder = self.vault / "raw" / "images"
        folder.mkdir()
        note = folder / "2026-09-24_diagram.md"
        note.write_text("---\nsource: x\ntitle: Diagram\n---\n\n# Diagram\n", encoding="utf-8")
        [staged] = self.stage(PNG)

        created = bridge._keep_image_original(staged, ["raw/images/2026-09-24_diagram.md"])

        self.assertEqual(created, ["raw/images/2026-09-24_diagram.png"])
        self.assertEqual((folder / "2026-09-24_diagram.png").read_bytes(), PNG)
        self.assertIn(
            "title: Diagram\noriginal: 2026-09-24_diagram.png\n---", note.read_text()
        )

    def test_no_note_no_copy(self) -> None:
        [staged] = self.stage(PNG)
        self.assertEqual(bridge._keep_image_original(staged, []), [])


class StaticPathTests(VaultTestCase):
    def test_static_never_serves_staging_or_dot_folders(self) -> None:
        [staged] = self.stage(PNG)
        (self.vault / "dashboard" / "app.js").write_text("x")
        self.assertIsNotNone(bridge._safe_static_path("/static/app.js"))
        self.assertIsNone(bridge._safe_static_path(f"/static/.uploads/{staged.name}"))
        self.assertIsNone(bridge._safe_static_path("/static/lib/../.uploads/x.png"))


class AttachmentRouteTests(VaultTestCase):
    def get(self, name: str) -> tuple[int, bytes]:
        sent = {"status": None}
        self.handler.wfile = io.BytesIO()
        self.handler.send_response = lambda code: sent.__setitem__("status", code)
        self.handler.send_header = lambda *a: None
        self.handler.end_headers = lambda: None

        def fake_response(_handler, status, payload):
            sent["status"] = status

        with mock.patch.object(bridge, "_json_response", fake_response):
            self.handler._serve_attachment(name)
        return sent["status"], self.handler.wfile.getvalue()

    def test_serves_image(self) -> None:
        self.attachments.mkdir()
        (self.attachments / "t-1.png").write_bytes(PNG)
        self.assertEqual(self.get("t-1.png"), (200, PNG))

    def test_rejects_unsafe_names(self) -> None:
        self.attachments.mkdir()
        (self.attachments / "t.svg").write_text("<svg/>")
        (self.vault / "outputs" / "t.md").write_text("x")
        for name in ("../t.md", "..%2Ft.md", "sub/t-1.png", "t.svg", "t.md",
                     ".hidden.png", "missing.png"):
            status, _ = self.get(name)
            self.assertEqual(status, 404, name)

    def test_deleting_thread_deletes_its_attachments(self) -> None:
        (self.vault / "outputs" / "2026-09-24_thread-x.md").write_text(THREAD)
        self.attachments.mkdir()
        for name in ("2026-09-24_thread-x-1.png", "2026-09-24_thread-x-2.jpg",
                     "2026-09-24_thread-x-2-1.png"):
            (self.attachments / name).write_bytes(PNG)

        with mock.patch.object(bridge, "_json_response", lambda *a: None):
            self.handler._delete_output_file("2026-09-24_thread-x.md")

        self.assertEqual(
            [p.name for p in self.attachments.iterdir()], ["2026-09-24_thread-x-2-1.png"]
        )


if __name__ == "__main__":
    unittest.main()

"""In-memory PDF regression tests for the extraction contract."""

import unittest

import fitz

from backend.pdf_parser import MAX_PARAGRAPH_CHARS, PDFExtractionError, extract_pdf


class PDFParserTests(unittest.TestCase):
    def make_pdf(self, draw, metadata=None, toc=None):
        document = fitz.open()
        draw(document)
        if metadata:
            document.set_metadata(metadata)
        if toc:
            document.set_toc(toc)
        data = document.tobytes()
        document.close()
        return data

    def test_paragraphs_headings_and_wrapped_hyphenation(self):
        def draw(document):
            page = document.new_page()
            page.insert_text((50, 65), "A Study of Paper Reading", fontsize=20, fontname="hebo")
            page.insert_text((50, 115), "1 INTRODUCTION", fontsize=13, fontname="hebo")
            page.insert_text((50, 145),
                             "This paragraph describes trans-\nlation across wrapped lines and preserves meaning.\n"
                             "The next sentence stays in the same paragraph.", fontsize=11)
            page.insert_text((50, 225), "A separate paragraph starts after a clear vertical gap.", fontsize=11)
        result = extract_pdf(self.make_pdf(draw), "paper.pdf")
        self.assertEqual(result["title"], "A Study of Paper Reading")
        paragraphs = result["paragraphs"]
        body = [paragraph for paragraph in paragraphs if paragraph["kind"] == "paragraph"]
        self.assertEqual(len(body), 2)
        self.assertIn("translation across wrapped lines", body[0]["text"])
        self.assertIn("same paragraph.", body[0]["text"])
        self.assertTrue(any(item["title"] == "1 INTRODUCTION" for item in result["outline"]))
        self.assertEqual([paragraph["id"] for paragraph in paragraphs], [f"p-{index + 1}" for index in range(len(paragraphs))])
        self.assertTrue(all(paragraph["translation"] is None and paragraph["error"] is None for paragraph in paragraphs))
        self.assertTrue(all(paragraph["section_id"] for paragraph in paragraphs))

    def test_two_columns_read_complete_left_before_right(self):
        def draw(document):
            page = document.new_page(width=600, height=800)
            page.insert_text((150, 60), "Reading Across Two Columns", fontsize=18, fontname="hebo")
            # Draw interleaved in PDF object order to ensure geometry wins.
            for index in range(4):
                page.insert_text((45, 130 + index * 15), f"LEFT {index + 1} text with enough prose.", fontsize=11)
                page.insert_text((325, 130 + index * 15), f"RIGHT {index + 1} text with enough prose.", fontsize=11)
        result = extract_pdf(self.make_pdf(draw), "columns.pdf")
        text = " ".join(paragraph["text"] for paragraph in result["paragraphs"])
        self.assertLess(text.index("LEFT 4"), text.index("RIGHT 1"))
        self.assertLess(text.index("Columns"), text.index("LEFT 1"))
        self.assertEqual(len([item for item in result["paragraphs"] if item["kind"] == "paragraph"]), 2)

    def test_repeated_headers_and_page_numbers_removed(self):
        def draw(document):
            for index in range(3):
                page = document.new_page()
                page.insert_text((50, 32), "Journal of Example Research - 2026", fontsize=9)
                page.insert_text((50, 150), f"A unique body paragraph appears on page {index + 1}.", fontsize=11)
                page.insert_text((290, 820), str(index + 1), fontsize=9)
        result = extract_pdf(self.make_pdf(draw, metadata={"title": "A Verified Metadata Title"}), "paper.pdf")
        self.assertEqual(result["title"], "A Verified Metadata Title")
        self.assertEqual(len(result["paragraphs"]), 3)
        self.assertNotIn("Journal", " ".join(item["text"] for item in result["paragraphs"]))
        self.assertEqual([item["page"] for item in result["paragraphs"]], [1, 2, 3])

    def test_pdf_bookmarks_link_to_matching_paragraphs(self):
        def draw(document):
            page = document.new_page()
            page.insert_text((50, 120), "1 Introduction", fontsize=14, fontname="hebo")
            page.insert_text((50, 150), "This first section has some selectable prose.", fontsize=11)
            page = document.new_page()
            page.insert_text((50, 120), "2.1 Evaluation", fontsize=14, fontname="hebo")
            page.insert_text((50, 150), "This evaluation describes our experiments.", fontsize=11)
        result = extract_pdf(self.make_pdf(draw, toc=[[1, "Introduction", 1], [2, "Evaluation", 2]]), "paper.pdf")
        self.assertEqual([(item["title"], item["level"], item["page"]) for item in result["outline"]],
                         [("Introduction", 1, 1), ("Evaluation", 2, 2)])
        by_id = {item["id"]: item for item in result["paragraphs"]}
        for entry in result["outline"]:
            paragraph = by_id[entry["paragraph_id"]]
            self.assertIn(entry["title"], paragraph["text"])
            self.assertEqual(paragraph["section_id"], entry["id"])

    def test_scanned_or_blank_document_has_actionable_error(self):
        data = self.make_pdf(lambda document: document.new_page())
        with self.assertRaisesRegex(PDFExtractionError, "OCR"):
            extract_pdf(data, "scan.pdf")

    def test_invalid_and_encrypted_documents(self):
        with self.assertRaisesRegex(PDFExtractionError, "为空"):
            extract_pdf(b"", "empty.pdf")
        with self.assertRaisesRegex(PDFExtractionError, "损坏"):
            extract_pdf(b"not a pdf at all", "broken.pdf")
        document = fitz.open()
        document.new_page().insert_text((50, 100), "Secret selectable text")
        data = document.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="reader")
        document.close()
        with self.assertRaisesRegex(PDFExtractionError, "加密"):
            extract_pdf(data, "encrypted.pdf")

    def test_mixed_document_warns_about_image_only_page(self):
        def draw(document):
            document.new_page().insert_text((50, 100), "A selectable paragraph that can be translated.", fontsize=11)
            document.new_page()
        result = extract_pdf(self.make_pdf(draw), "mixed.pdf")
        self.assertEqual(result["page_count"], 2)
        self.assertIn("2", result["warnings"][0])
        self.assertIn("OCR", result["warnings"][0])

    def test_numbered_heading_without_bold_font_is_in_outline(self):
        def draw(document):
            page = document.new_page()
            page.insert_text((50, 100), "1 Introduction", fontsize=11)
            page.insert_text((50, 140), "This paragraph has ordinary body text and a complete sentence.", fontsize=11)
            page.insert_text((50, 190), "2.1 Our Analytical Framework", fontsize=11)
            page.insert_text((50, 230), "A second section describes the scientific framework.", fontsize=11)
        result = extract_pdf(self.make_pdf(draw), "plain.pdf")
        self.assertEqual([(entry["title"], entry["level"]) for entry in result["outline"]],
                         [("1 Introduction", 1), ("2.1 Our Analytical Framework", 2)])

    def test_page_limit_has_actionable_error(self):
        data = self.make_pdf(lambda document: [document.new_page() for _ in range(501)])
        with self.assertRaisesRegex(PDFExtractionError, "500 页"):
            extract_pdf(data, "oversized.pdf")

    def test_long_paragraph_is_chunked_without_losing_text(self):
        def draw(document):
            page = document.new_page(width=1200, height=2200)
            text = " ".join(["This sentence explains a reproducible scientific experiment."] * 100)
            remaining = page.insert_textbox(fitz.Rect(50, 60, 1150, 2150), text, fontsize=11)
            self.assertGreaterEqual(remaining, 0)
        result = extract_pdf(self.make_pdf(draw), "long.pdf")
        bodies = [paragraph for paragraph in result["paragraphs"] if paragraph["kind"] == "paragraph"]
        self.assertGreaterEqual(len(bodies), 2)
        self.assertTrue(all(len(paragraph["text"]) <= MAX_PARAGRAPH_CHARS for paragraph in bodies))
        joined = " ".join(paragraph["text"] for paragraph in bodies)
        self.assertEqual(joined.count("reproducible scientific experiment."), 100)


if __name__ == "__main__":
    unittest.main()

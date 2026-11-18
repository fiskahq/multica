from io import BytesIO
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from docx_text import DocumentError, MAX_XML, extract
from service import MAX_FILE, Store, scan

NS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'


def document(body, namespace=NS):
    return f'<w:document xmlns:w="{namespace}"><w:body>{body}</w:body></w:document>'


def package(xml, extras=None):
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        archive.writestr('_rels/.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        archive.writestr('word/document.xml', xml)
        for name, data in (extras or {}).items():
            archive.writestr(name, data)
    return buffer.getvalue()


class DocxTests(unittest.TestCase):
    def test_preserves_split_runs_hyperlink_text_unicode_tabs_breaks_and_tables(self):
        xml = document('<w:p><w:r><w:t>Dav</w:t></w:r><w:r><w:t>id: résumé</w:t><w:tab/><w:t>00:10</w:t><w:br/></w:r>'
                       '<w:hyperlink><w:r><w:t>Review notes</w:t></w:r></w:hyperlink></w:p>'
                       '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Owner</w:t></w:r></w:p></w:tc>'
                       '<w:tc><w:p><w:r><w:t>David</w:t></w:r></w:p></w:tc></w:tr></w:tbl>')
        self.assertEqual(extract(package(xml), MAX_FILE), 'David: résumé\t00:10\nReview notes\nOwner\tDavid')

    def test_current_text_includes_insertions_but_not_deleted_text_or_field_codes(self):
        xml = document('<w:p><w:del><w:r><w:delText>obsolete</w:delText><w:t>also deleted</w:t></w:r></w:del>'
                       '<w:r><w:instrText>INCLUDETEXT remote</w:instrText></w:r>'
                       '<w:ins><w:r><w:t>Approved wording</w:t></w:r></w:ins></w:p>')
        self.assertEqual(extract(package(xml), MAX_FILE), 'Approved wording')

    def test_strict_ooxml_and_utf16_xml_are_supported(self):
        xml = '<?xml version="1.0" encoding="UTF-16"?>' + document('<w:p><w:r><w:t>Bonjour</w:t></w:r></w:p>', 'http://purl.oclc.org/ooxml/wordprocessingml/main')
        self.assertEqual(extract(package(xml.encode('utf-16')), MAX_FILE), 'Bonjour')

    def test_entities_are_rejected_for_utf8_and_utf16(self):
        xml = '<!DOCTYPE document [<!ENTITY x SYSTEM "file:///etc/passwd">]>' + document('<w:p><w:r><w:t>&x;</w:t></w:r></w:p>')
        for value in (xml, ('<?xml version="1.0" encoding="UTF-16"?>' + xml).encode('utf-16')):
            with self.subTest(encoding=type(value)), self.assertRaisesRegex(DocumentError, 'dtd_or_entities'):
                extract(package(value), MAX_FILE)

    def test_archive_xml_and_extracted_text_limits(self):
        with patch('docx_text.MAX_DOCX', 4), self.assertRaisesRegex(DocumentError, 'file_too_large'):
            extract(b'too large', MAX_FILE)
        xml = document('<w:p><w:r><w:t>' + 'a' * 200 + '</w:t></w:r></w:p>')
        with patch('docx_text.MAX_XML', 100), self.assertRaisesRegex(DocumentError, 'xml_too_large'):
            extract(package(xml), MAX_FILE)
        with self.assertRaisesRegex(DocumentError, 'text_too_large'):
            extract(package(xml), 100)

    def test_bad_archives_and_xml_have_safe_errors(self):
        for raw in (b'not a zip', package('<invalid secret="not-for-logs">')):
            with self.subTest(raw=raw[:4]), self.assertRaises(DocumentError) as caught:
                extract(raw, MAX_FILE)
            self.assertNotIn('not-for-logs', str(caught.exception))
        with self.assertRaisesRegex(DocumentError, 'no_readable_text'):
            extract(package(document('<w:p/>')), MAX_FILE)

    def test_missing_and_duplicate_document_parts_are_rejected(self):
        import warnings
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            archive.writestr('word/other.xml', '<nothing/>')
        with self.assertRaisesRegex(DocumentError, 'missing_or_duplicate'):
            extract(buffer.getvalue(), MAX_FILE)
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            raw = package(document(''), {'word/document.xml': document('')})
        with self.assertRaisesRegex(DocumentError, 'missing_or_duplicate'):
            extract(raw, MAX_FILE)

    def test_scan_ingests_docx_deduplicates_extracted_text_and_continues_past_bad_docx(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); inbox = root / 'inbox'; inbox.mkdir()
            text = 'David: Please document the decision.'
            (inbox / 'bad.docx').write_bytes(b'not a document')
            (inbox / 'call.DOCX').write_bytes(package(document('<w:p><w:r><w:t>' + text + '</w:t></w:r></w:p>'),
                                                       {'word/media/picture.bin': os.urandom(MAX_FILE + 100)}))
            (inbox / 'copy.txt').write_text(text)
            (inbox / '~$call.docx').write_bytes(b'Office lock file')
            for path in inbox.iterdir(): os.utime(path, (0, 0))
            store = Store(root / 'queue.sqlite3')
            try:
                errors = scan(store, inbox)
                self.assertEqual(errors, [{'file': 'bad.docx', 'error': 'docx_invalid_archive'}])
                rows = store.db.execute('SELECT name,text FROM files').fetchall()
                self.assertEqual([tuple(r) for r in rows], [('call.DOCX', text)])
            finally:
                store.db.close()


if __name__ == '__main__':
    unittest.main()

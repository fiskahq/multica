"""Read bounded Word document text without extracting files or resolving relationships."""
from io import BytesIO
import zipfile
import zlib
from xml.parsers import expat

MAX_DOCX = 10 * 1024 * 1024
MAX_XML = 8 * 1024 * 1024
WORD_NAMESPACES = {
    'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
    'http://purl.oclc.org/ooxml/wordprocessingml/main',
}


class DocumentError(ValueError):
    """A safe diagnostic code, never document contents or parser output."""


def extract(raw, max_text):
    if len(raw) > MAX_DOCX:
        raise DocumentError('docx_file_too_large')
    try:
        with zipfile.ZipFile(BytesIO(raw)) as archive:
            matches = [i for i in archive.infolist() if i.filename == 'word/document.xml']
            if len(matches) != 1:
                raise DocumentError('docx_missing_or_duplicate_document')
            member = matches[0]
            if member.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                raise DocumentError('docx_unsupported_compression')
            if member.flag_bits & 1:
                raise DocumentError('docx_encrypted_document')
            if member.file_size > MAX_XML:
                raise DocumentError('docx_document_xml_too_large')
            with archive.open(member) as source:
                xml = source.read(MAX_XML + 1)
            if len(xml) > MAX_XML:
                raise DocumentError('docx_document_xml_too_large')
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError, EOFError, zlib.error) as error:
        raise DocumentError('docx_invalid_archive') from error

    parser = expat.ParserCreate(namespace_separator='}')
    pieces = []
    size = depth = nodes = text_depth = excluded_depth = 0
    root_seen = False

    def word(name):
        namespace, _, local = name.rpartition('}')
        return local if namespace in WORD_NAMESPACES else ''

    def append(value):
        nonlocal size
        size += len(value.encode('utf-8'))
        if size > max_text:
            raise DocumentError('docx_extracted_text_too_large')
        pieces.append(value)

    def start(name, attrs):
        nonlocal depth, nodes, text_depth, excluded_depth, root_seen
        depth += 1
        nodes += 1
        if depth > 128 or nodes > 200000:
            raise DocumentError('docx_document_too_complex')
        tag = word(name)
        if depth == 1:
            if tag != 'document':
                raise DocumentError('docx_invalid_document_root')
            root_seen = True
        if tag in ('del', 'moveFrom') and not excluded_depth:
            excluded_depth = depth
        if excluded_depth:
            return
        if tag == 't':
            text_depth = depth
        elif tag == 'tab':
            append('\t')
        elif tag in ('br', 'cr'):
            append('\n')

    def end(name):
        nonlocal depth, text_depth, excluded_depth
        tag = word(name)
        if not excluded_depth:
            if tag == 'p':
                append('\n')
            elif tag == 'tc':
                if pieces and pieces[-1] == '\n':
                    pieces[-1] = '\t'
            elif tag == 'tr':
                if pieces and pieces[-1] == '\t':
                    pieces[-1] = '\n'
        if depth == text_depth:
            text_depth = 0
        if depth == excluded_depth:
            excluded_depth = 0
        depth -= 1

    def characters(value):
        if text_depth and not excluded_depth:
            append(value)

    def reject(*args):
        raise DocumentError('docx_dtd_or_entities_not_allowed')

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = characters
    parser.StartDoctypeDeclHandler = reject
    parser.EntityDeclHandler = reject
    parser.ExternalEntityRefHandler = reject
    try:
        parser.Parse(xml, True)
    except expat.ExpatError as error:
        raise DocumentError('docx_invalid_xml') from error
    text = ''.join(pieces).strip()
    if not root_seen or not text:
        raise DocumentError('docx_no_readable_text')
    return text

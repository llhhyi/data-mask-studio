"""Patch editable Word text without rebuilding styles, relationships or media."""

from io import BytesIO
import re
from xml.dom import Node
from zipfile import BadZipFile, ZipFile

from defusedxml.minidom import parseString

from .models import NarrativeError, TextUnit

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
XML = "http://www.w3.org/XML/1998/namespace"
PART = re.compile(r"word/(?:document|header\d*|footer\d*|footnotes|endnotes)\.xml\Z")
MAX_EXPANDED = 256 * 1024 * 1024


class WordDocument:
    def __init__(self, raw: bytes, check):
        self.parts, self.documents, self.changed = {}, {}, set()
        self.units, self.skipped, self.image_count = [], [], 0
        try:
            with ZipFile(BytesIO(raw)) as archive:
                self.infos = archive.infolist()
                self.comment = archive.comment
                names = [info.filename for info in self.infos]
                if (len(names) != len(set(names)) or len(names) > 10000
                        or sum(info.file_size for info in self.infos) > MAX_EXPANDED
                        or "word/document.xml" not in names or "[Content_Types].xml" not in names
                        or any("vbaproject" in name.casefold() or name.startswith("_xmlsignatures/") for name in names)):
                    raise NarrativeError("narrative.error.word")
                if sum(info.file_size for info in self.infos if info.filename.endswith((".xml", ".rels"))) > 32 * 1024 * 1024:
                    raise NarrativeError("narrative.error.size")
                for info in self.infos:
                    check()
                    if info.flag_bits & 1 or info.file_size > MAX_EXPANDED:
                        raise NarrativeError("narrative.error.word")
                    self.parts[info.filename] = archive.read(info)
                    if len(self.parts[info.filename]) != info.file_size:
                        raise NarrativeError("narrative.error.word")
            types = parseString(self.parts["[Content_Types].xml"], forbid_dtd=True)
            if not any(node.getAttribute("ContentType") == "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
                       for node in types.getElementsByTagNameNS("http://schemas.openxmlformats.org/package/2006/content-types", "Override")):
                raise NarrativeError("narrative.error.word")
            for name, data in self.parts.items():
                if not name.endswith((".xml", ".rels")):
                    continue
                check()
                if len(data) > 32 * 1024 * 1024:
                    raise NarrativeError("narrative.error.size")
                document = parseString(data, forbid_dtd=True)
                self.documents[name] = document
                seen = set()
                if PART.fullmatch(name):
                    for namespace in (W, A):
                        for paragraph in document.getElementsByTagNameNS(namespace, "p"):
                            self._paragraph(name, paragraph, namespace, seen, check)
                # Non-editable locations and hyperlink targets are reported, never rewritten.
                for node in document.getElementsByTagName("*"):
                    if node not in seen and node.localName in {"t", "delText", "instrText"}:
                        self.skipped.append(_text(node))
                    if node.attributes:
                        self.skipped.extend(node.getAttribute(attr) for attr in node.attributes.keys())
                    if node.namespaceURI == W and node.localName in {"drawing", "pict"}:
                        self.image_count += 1
        except NarrativeError:
            raise
        except Exception:
            # Parser/ZIP messages may include document contents; expose a stable reason only.
            raise NarrativeError("narrative.error.word") from None

    def _paragraph(self, part, paragraph, namespace, seen, check):
        group = []

        def flush():
            if group:
                nodes = tuple(group)
                text = "".join(_text(node) for node in nodes)
                self.units.append(TextUnit(text, lambda patches, nodes=nodes: self._patch(part, nodes, patches)))
                group.clear()

        def visit(node):
            check()
            if node is not paragraph and node.localName == "p" and node.namespaceURI in {W, A}:
                flush()
                return
            if node.namespaceURI == namespace and node.localName == "t":
                group.append(node)
                seen.add(node)
                return
            if node.namespaceURI in {W, A} and node.localName in {
                "tab", "br", "cr", "instrText", "delText", "del", "fldChar", "drawing", "pict",
                "noBreakHyphen", "softHyphen", "sym", "footnoteReference", "endnoteReference",
                "commentReference", "object", "moveFrom",
            }:
                flush()
                return
            for child in node.childNodes:
                if child.nodeType == Node.ELEMENT_NODE:
                    visit(child)

        visit(paragraph)
        flush()

    def _patch(self, part, nodes, patches):
        positions, offset = [], 0
        for node in nodes:
            original = _text(node)
            positions.append((node, offset, offset + len(original)))
            offset += len(original)
        line_nodes = set()
        for start, end, value in reversed(patches):
            if any((ord(char) < 32 and char not in "\t\r\n") or 0xD800 <= ord(char) <= 0xDFFF or ord(char) in {0xFFFE, 0xFFFF} for char in value):
                raise NarrativeError("narrative.error.value")
            for node, left, right in positions:
                if right <= start or left >= end:
                    continue
                text = _text(node)
                first = max(0, start - left)
                last = min(right - left, end - left)
                text = text[:first] + (value if left <= start < right else "") + text[last:]
                while node.firstChild:
                    node.removeChild(node.firstChild)
                node.appendChild(node.ownerDocument.createTextNode(text))
                node.setAttributeNS(XML, "xml:space", "preserve")
                if any(char in value for char in "\r\n\t"):
                    line_nodes.add(node)
        if patches:
            for node in line_nodes:
                text = _text(node)
                if node.namespaceURI == W and any(char in text for char in "\r\n\t"):
                    pieces = re.split(r"(\r\n|\r|\n|\t)", text)
                    node.firstChild.data = pieces[0]
                    previous = node
                    for piece in pieces[1:]:
                        if piece in {"\r\n", "\r", "\n", "\t"}:
                            local = "tab" if piece == "\t" else "br"
                            qualified_name = f"{node.prefix}:{local}" if node.prefix else local
                            child = node.ownerDocument.createElementNS(W, qualified_name)
                        else:
                            child = node.cloneNode(deep=False)
                            child.appendChild(node.ownerDocument.createTextNode(piece))
                        node.parentNode.insertBefore(child, previous.nextSibling)
                        previous = child
            self.changed.add(part)

    def serialize(self, check) -> bytes:
        output = BytesIO()
        with ZipFile(output, "w") as archive:
            archive.comment = self.comment
            for info in self.infos:
                check()
                raw = (self.documents[info.filename].toxml(encoding="utf-8")
                       if info.filename in self.changed else self.parts[info.filename])
                archive.writestr(info, raw)
        return output.getvalue()


def _text(node):
    return "".join(child.data for child in node.childNodes if child.nodeType in {Node.TEXT_NODE, Node.CDATA_SECTION_NODE})

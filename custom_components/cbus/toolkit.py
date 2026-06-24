"""Parse C-Bus Toolkit project files for group address labels.

Direct-CNI mode has no project database on the bus, so group *names* must come
from somewhere. C-Bus Toolkit can export/back up a project as a ``.cbz`` (a ZIP
containing one ``.xml``) or a raw ``.xml``. This module reads either and returns
the lighting-application group names, so the config flow can pre-fill them.

Dependency-free: uses only the Python standard library (``zipfile`` +
``xml.etree``), so no lxml is required.
"""

from __future__ import annotations

import io
import logging
import zipfile
from xml.etree import ElementTree

from .const import LIGHTING_APPLICATION

_LOGGER = logging.getLogger(__name__)

# Names Toolkit uses for unconfigured groups; not worth importing.
_DEFAULT_NAMES = {"", "<Unused>", "Unused"}


def _child_text(element: ElementTree.Element, name: str) -> str | None:
    """Return the text of the first direct child whose tag ends with ``name``.

    Tolerant of XML namespaces (matches on the local tag name).
    """
    for child in element:
        if child.tag.endswith(name) and child.text is not None:
            return child.text.strip()
    return None


def parse_toolkit_labels(raw: bytes) -> dict[int, str]:
    """Extract ``{group_address: name}`` for the lighting application.

    :param raw: Raw bytes of a ``.cbz`` (ZIP) or ``.xml`` Toolkit project file.
    :returns: Mapping of lighting group address to its Toolkit tag name.
              Default/unused group names are skipped.
    """
    xml_bytes = raw
    # A ZIP file starts with the magic bytes "PK".
    if raw[:2] == b"PK":
        try:
            archive = zipfile.ZipFile(io.BytesIO(raw))
        except zipfile.BadZipFile:
            _LOGGER.warning("Project file looked like a ZIP but could not be read")
            return {}
        xml_members = [n for n in archive.namelist() if n.lower().endswith(".xml")]
        if not xml_members:
            _LOGGER.warning("No .xml found inside the .cbz archive")
            return {}
        xml_bytes = archive.read(xml_members[0])

    try:
        root = ElementTree.fromstring(xml_bytes)  # noqa: S314 - local trusted file
    except ElementTree.ParseError as err:
        _LOGGER.warning("Could not parse Toolkit project XML: %s", err)
        return {}

    labels: dict[int, str] = {}
    for app in root.iter():
        if not app.tag.endswith("Application"):
            continue
        app_addr = _child_text(app, "Address")
        if app_addr is None or not app_addr.isdigit():
            continue
        if int(app_addr) != LIGHTING_APPLICATION:
            continue

        for grp in app.iter():
            if not grp.tag.endswith("Group"):
                continue
            grp_addr = _child_text(grp, "Address")
            if grp_addr is None or not grp_addr.isdigit():
                continue
            gid = int(grp_addr)
            name = (_child_text(grp, "TagName") or "").strip()
            if name in _DEFAULT_NAMES or name == f"Group {gid}":
                continue
            labels[gid] = name

    return labels


def parse_toolkit_file(path: str) -> dict[int, str]:
    """Read a Toolkit project file from ``path`` and return its group labels.

    :raises OSError: if the file cannot be read.
    """
    with open(path, "rb") as handle:
        return parse_toolkit_labels(handle.read())

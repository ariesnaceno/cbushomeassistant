"""Tests for the C-Bus Toolkit project-file label parser.

Runs without Home Assistant or hardware. Loads the parser module directly
(avoiding the package __init__, which imports homeassistant).
Run: ``python3 tests/test_toolkit.py``.
"""

import importlib.util
import io
import os
import sys
import types
import zipfile

_HERE = os.path.dirname(__file__)
_PKG = os.path.abspath(os.path.join(_HERE, "..", "custom_components", "cbus"))

# Load const and toolkit as a tiny standalone package so relative imports work.
_pkg = types.ModuleType("cbuspkg")
_pkg.__path__ = [_PKG]
sys.modules["cbuspkg"] = _pkg
for _name in ("const", "toolkit"):
    _spec = importlib.util.spec_from_file_location(
        f"cbuspkg.{_name}", os.path.join(_PKG, f"{_name}.py")
    )
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules[f"cbuspkg.{_name}"] = _mod
    _spec.loader.exec_module(_mod)

toolkit = sys.modules["cbuspkg.toolkit"]


# Lighting app (56) with real names, a default/unused group, plus a non-lighting
# application (Trigger Control, 202) that must be ignored.
SAMPLE_XML = b"""<Installation><Project><Network>
<TagName>Local</TagName><Address>254</Address>
<Interface><InterfaceType>cni</InterfaceType></Interface>
<Application><OID>a</OID><TagName>Lighting</TagName><Address>56</Address>
  <Group><OID>g1</OID><TagName>Kitchen</TagName><Address>4</Address></Group>
  <Group><OID>g2</OID><TagName>Living Room</TagName><Address>1</Address></Group>
  <Group><OID>g3</OID><TagName>&lt;Unused&gt;</TagName><Address>7</Address></Group>
  <Group><OID>g4</OID><TagName>Group 20</TagName><Address>20</Address></Group>
</Application>
<Application><OID>b</OID><TagName>Trigger Control</TagName><Address>202</Address>
  <Group><OID>g5</OID><TagName>Scene A</TagName><Address>80</Address></Group>
</Application>
</Network></Project></Installation>"""

EXPECTED = {4: "Kitchen", 1: "Living Room"}


def check(label, got, expected):
    status = "ok" if got == expected else "FAIL"
    print(f"[{status}] {label}: {got!r}")
    assert got == expected, f"{label}: expected {expected!r}, got {got!r}"


def main() -> None:
    # 1. Raw XML: only lighting groups with real names; default/unused skipped,
    #    Trigger Control (202) excluded.
    check("raw xml", toolkit.parse_toolkit_labels(SAMPLE_XML), EXPECTED)

    # 2. CBZ (zip wrapping one .xml) parses identically.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("HOME.xml", SAMPLE_XML)
    check("cbz zip", toolkit.parse_toolkit_labels(buf.getvalue()), EXPECTED)

    # 3. Garbage / non-XML returns empty (no crash).
    check("garbage", toolkit.parse_toolkit_labels(b"not xml at all"), {})

    # 4. Empty ZIP with no xml -> empty.
    empty = io.BytesIO()
    with zipfile.ZipFile(empty, "w") as zf:
        zf.writestr("readme.txt", b"hi")
    check("zip without xml", toolkit.parse_toolkit_labels(empty.getvalue()), {})

    print("\nAll Toolkit parser tests passed.")


if __name__ == "__main__":
    main()

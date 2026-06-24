# Vendored dependency: `cbus` (libcbus)

This directory contains a vendored copy of the **C-Bus protocol layer** from the
`cbus` / libcbus project by Michael Farrell (micolous):

- Upstream: https://github.com/micolous/cbus
- Docs: https://cbus.readthedocs.io/

Only the protocol modules required to talk to a C-Bus PCI/CNI are included
(`cbus/__init__.py`, `cbus/common.py`, and the `cbus/protocol/` tree). The
`daemon` (cmqttd / MQTT), `toolkit`, and `tools` packages are **not** vendored.

## License

This vendored code is licensed under the **GNU Lesser General Public License
v3.0 or later (LGPL-3.0-or-later)** — see `COPYING` and `COPYING.LESSER` in this
directory. It is redistributed unmodified.

The rest of the `cbushomeassistant` repository is MIT-licensed. The LGPL applies
only to the contents of this `vendor/` directory. Per the LGPL, you may replace
this vendored copy with your own version of the `cbus` library.

The files here are imported at runtime by adding this `vendor/` directory to
`sys.path` (see `../pci.py`); they are not modified.

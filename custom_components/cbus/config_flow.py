"""Config flow for the C-Bus (direct CNI/PCI) integration."""

from __future__ import annotations

import asyncio
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_COVER_GROUPS,
    CONF_GROUPS,
    CONF_PORT,
    CONF_PROJECT_FILE,
    CONF_SWITCH_GROUPS,
    DEFAULT_PORT,
    DOMAIN,
)
from .toolkit import parse_toolkit_file


def _parse_groups(raw: str) -> dict[int, str]:
    """Parse 'group:Friendly Name' lines into {group_id: name}."""
    groups: dict[int, str] = {}
    for chunk in raw.replace("\n", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" in chunk:
            number, name = chunk.split(":", 1)
        else:
            number, name = chunk, f"C-Bus Group {chunk.strip()}"
        number = number.strip()
        if number.isdigit():
            groups[int(number)] = name.strip()
    return groups


def _build_options(user_input: dict[str, Any]) -> dict[str, Any]:
    """Build the per-platform group option maps from user input."""
    return {
        key: {str(k): v for k, v in _parse_groups(user_input.get(key, "")).items()}
        for key in (CONF_GROUPS, CONF_SWITCH_GROUPS, CONF_COVER_GROUPS)
    }


async def _async_can_connect(host: str, port: int) -> bool:
    """Return True if a TCP connection to the CNI can be opened."""
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=5
        )
    except (OSError, asyncio.TimeoutError):
        return False
    writer.close()
    try:
        await writer.wait_closed()
    except OSError:
        pass
    return True


class CBusConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial setup of a direct CNI connection."""

    VERSION = 2

    def __init__(self) -> None:
        """Hold state between the connect and group-confirmation steps."""
        self._conn: dict[str, Any] = {}
        self._typed: dict[str, Any] = {}
        self._discovered: dict[int, str] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect CNI connection details, group lists, and optional project."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST]
            port = user_input[CONF_PORT]
            project_path = (user_input.get(CONF_PROJECT_FILE) or "").strip()

            if not await _async_can_connect(host, port):
                errors["base"] = "cannot_connect"
            elif project_path:
                try:
                    self._discovered = await self.hass.async_add_executor_job(
                        parse_toolkit_file, project_path
                    )
                except OSError:
                    errors["base"] = "invalid_project_file"

            if not errors:
                await self.async_set_unique_id(f"{host}:{port}")
                self._abort_if_unique_id_configured()
                self._conn = {CONF_HOST: host, CONF_PORT: port}
                self._typed = user_input

                if self._discovered:
                    # Let the user confirm/assign the auto-detected names.
                    return await self.async_step_groups()

                return self.async_create_entry(
                    title=f"C-Bus CNI ({host})",
                    data=self._conn,
                    options=_build_options(user_input),
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST): str,
                vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
                vol.Optional(CONF_GROUPS, default=""): str,
                vol.Optional(CONF_SWITCH_GROUPS, default=""): str,
                vol.Optional(CONF_COVER_GROUPS, default=""): str,
                vol.Optional(CONF_PROJECT_FILE, default=""): str,
            }
        )
        return self.async_show_form(
            step_id="user", data_schema=schema, errors=errors
        )

    async def async_step_groups(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm/assign the group names auto-detected from the project file."""
        if user_input is not None:
            return self.async_create_entry(
                title=f"C-Bus CNI ({self._conn[CONF_HOST]})",
                data=self._conn,
                options=_build_options(user_input),
            )

        # Merge anything the user typed on the first page with discovered names
        # (typed names win), and pre-fill the lights box.
        merged = dict(self._discovered)
        merged.update(_parse_groups(self._typed.get(CONF_GROUPS, "")))
        lights = "\n".join(f"{gid}:{name}" for gid, name in sorted(merged.items()))

        schema = vol.Schema(
            {
                vol.Optional(CONF_GROUPS, default=lights): str,
                vol.Optional(
                    CONF_SWITCH_GROUPS,
                    default=self._typed.get(CONF_SWITCH_GROUPS, ""),
                ): str,
                vol.Optional(
                    CONF_COVER_GROUPS,
                    default=self._typed.get(CONF_COVER_GROUPS, ""),
                ): str,
            }
        )
        return self.async_show_form(
            step_id="groups",
            data_schema=schema,
            description_placeholders={"count": str(len(self._discovered))},
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the host/port (e.g. to point at the C-Bus CNI Relay)."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST]
            port = user_input[CONF_PORT]
            if not await _async_can_connect(host, port):
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={CONF_HOST: host, CONF_PORT: port},
                    unique_id=f"{host}:{port}",
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=entry.data[CONF_HOST]): str,
                vol.Required(
                    CONF_PORT, default=entry.data.get(CONF_PORT, DEFAULT_PORT)
                ): int,
            }
        )
        return self.async_show_form(
            step_id="reconfigure", data_schema=schema, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow handler."""
        return CBusOptionsFlow(entry)


_KIND_TO_KEY = {
    "light": CONF_GROUPS,
    "switch": CONF_SWITCH_GROUPS,
    "cover": CONF_COVER_GROUPS,
}


class CBusOptionsFlow(OptionsFlow):
    """Menu-driven editor for C-Bus groups: add (manually or from a Toolkit
    file), remove, then save."""

    def __init__(self, entry: ConfigEntry) -> None:
        """Load editable working copies of the current group maps."""
        self._entry = entry
        opts = entry.options
        # Working copies, mutated as the user adds/removes, saved at the end.
        self._groups: dict[str, dict[str, str]] = {
            "light": dict(opts.get(CONF_GROUPS, {})),
            "switch": dict(opts.get(CONF_SWITCH_GROUPS, {})),
            "cover": dict(opts.get(CONF_COVER_GROUPS, {})),
        }
        # Group {address: name} discovered from a Toolkit file, if loaded.
        self._discovered: dict[int, str] = {}

    # ------------------------------------------------------------------
    # Menu
    # ------------------------------------------------------------------
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the main menu with a summary of what's configured."""
        summary = (
            f"{len(self._groups['light'])} light(s), "
            f"{len(self._groups['switch'])} switch(es), "
            f"{len(self._groups['cover'])} cover(s)"
        )
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "pick_toolkit",
                "add_light",
                "add_switch",
                "add_cover",
                "remove",
                "save",
            ],
            description_placeholders={"summary": summary},
        )

    # ------------------------------------------------------------------
    # Add from a C-Bus Toolkit project file (pick from a checklist)
    # ------------------------------------------------------------------
    async def async_step_pick_toolkit(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a Toolkit .cbz/.xml path and parse its groups."""
        errors: dict[str, str] = {}
        if user_input is not None:
            path = (user_input.get(CONF_PROJECT_FILE) or "").strip()
            try:
                self._discovered = await self.hass.async_add_executor_job(
                    parse_toolkit_file, path
                )
            except OSError:
                errors["base"] = "invalid_project_file"
            if not errors and not self._discovered:
                errors["base"] = "no_groups_found"
            if not errors:
                return await self.async_step_pick()

        schema = vol.Schema({vol.Required(CONF_PROJECT_FILE): str})
        return self.async_show_form(
            step_id="pick_toolkit", data_schema=schema, errors=errors
        )

    async def async_step_pick(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show a checklist of discovered groups; add the ticked ones."""
        if user_input is not None:
            kind = user_input["type"]
            for addr in user_input.get("groups", []):
                self._groups[kind][str(addr)] = self._discovered.get(
                    int(addr), f"C-Bus Group {addr}"
                )
            return await self.async_step_init()

        options = [
            selector.SelectOptionDict(value=str(addr), label=f"{addr} — {name}")
            for addr, name in sorted(self._discovered.items())
        ]
        schema = vol.Schema(
            {
                vol.Required("groups"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=options,
                        multiple=True,
                        mode=selector.SelectSelectorMode.LIST,
                    )
                ),
                vol.Required("type", default="light"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=["light", "switch", "cover"],
                        translation_key="cbus_kind",
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="pick",
            data_schema=schema,
            description_placeholders={"count": str(len(self._discovered))},
        )

    # ------------------------------------------------------------------
    # Add one group manually (Add Light / Switch / Cover buttons)
    # ------------------------------------------------------------------
    async def async_step_add_light(self, user_input=None) -> ConfigFlowResult:
        """Add a single light group."""
        return await self._async_add_one("light", "add_light", user_input)

    async def async_step_add_switch(self, user_input=None) -> ConfigFlowResult:
        """Add a single switch group."""
        return await self._async_add_one("switch", "add_switch", user_input)

    async def async_step_add_cover(self, user_input=None) -> ConfigFlowResult:
        """Add a single cover group."""
        return await self._async_add_one("cover", "add_cover", user_input)

    async def _async_add_one(
        self, kind: str, step_id: str, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        """Shared handler: enter an address + name, then back to the menu."""
        if user_input is not None:
            addr = int(user_input["address"])
            name = (user_input.get("name") or "").strip() or f"C-Bus Group {addr}"
            self._groups[kind][str(addr)] = name
            return await self.async_step_init()

        schema = vol.Schema(
            {
                vol.Required("address"): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=255, step=1, mode=selector.NumberSelectorMode.BOX
                    )
                ),
                vol.Optional("name", default=""): str,
            }
        )
        return self.async_show_form(step_id=step_id, data_schema=schema)

    # ------------------------------------------------------------------
    # Remove groups
    # ------------------------------------------------------------------
    async def async_step_remove(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Tick existing groups to remove them."""
        if user_input is not None:
            for token in user_input.get("remove", []):
                kind, addr = token.split(":", 1)
                self._groups[kind].pop(addr, None)
            return await self.async_step_init()

        options: list[selector.SelectOptionDict] = []
        for kind in ("light", "switch", "cover"):
            for addr, name in sorted(
                self._groups[kind].items(), key=lambda kv: int(kv[0])
            ):
                options.append(
                    selector.SelectOptionDict(
                        value=f"{kind}:{addr}", label=f"[{kind}] {addr} — {name}"
                    )
                )
        if not options:
            # Nothing configured yet — just go back to the menu.
            return await self.async_step_init()

        schema = vol.Schema(
            {
                vol.Optional("remove", default=[]): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=options,
                        multiple=True,
                        mode=selector.SelectSelectorMode.LIST,
                    )
                )
            }
        )
        return self.async_show_form(step_id="remove", data_schema=schema)

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------
    async def async_step_save(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Persist the working copies as the entry options."""
        return self.async_create_entry(
            title="",
            data={
                CONF_GROUPS: self._groups["light"],
                CONF_SWITCH_GROUPS: self._groups["switch"],
                CONF_COVER_GROUPS: self._groups["cover"],
            },
        )

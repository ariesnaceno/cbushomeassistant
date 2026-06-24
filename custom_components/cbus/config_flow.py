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

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow handler."""
        return CBusOptionsFlow(entry)


class CBusOptionsFlow(OptionsFlow):
    """Allow editing the C-Bus group lists after setup."""

    def __init__(self, entry: ConfigEntry) -> None:
        """Store the entry being edited."""
        self._entry = entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit the light/switch/cover group lists."""
        if user_input is not None:
            return self.async_create_entry(title="", data=_build_options(user_input))

        def _as_text(key: str) -> str:
            return "\n".join(
                f"{k}:{v}" for k, v in self._entry.options.get(key, {}).items()
            )

        schema = vol.Schema(
            {
                vol.Optional(CONF_GROUPS, default=_as_text(CONF_GROUPS)): str,
                vol.Optional(
                    CONF_SWITCH_GROUPS, default=_as_text(CONF_SWITCH_GROUPS)
                ): str,
                vol.Optional(
                    CONF_COVER_GROUPS, default=_as_text(CONF_COVER_GROUPS)
                ): str,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)

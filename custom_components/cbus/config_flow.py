"""Config flow for the C-Bus (C-Gate) integration."""

from __future__ import annotations

import logging
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

from .cgate import CGateClient, CGateError
from .const import (
    CONF_COMMAND_PORT,
    CONF_NETWORK,
    CONF_PROJECT,
    CONF_STATUS_PORT,
    DEFAULT_COMMAND_PORT,
    DEFAULT_NETWORK,
    DEFAULT_STATUS_PORT,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

# Stored in entry.options: newline/comma separated "group:Friendly Name" lines.
CONF_GROUPS = "groups"
CONF_SWITCH_GROUPS = "switch_groups"
CONF_COVER_GROUPS = "cover_groups"


def _parse_groups(raw: str) -> dict[int, str]:
    """Parse the user's group definition text into {group_id: name}."""
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
    """Build the options dict (per-platform group maps) from user input."""
    return {
        CONF_GROUPS: {
            str(k): v for k, v in _parse_groups(user_input.get(CONF_GROUPS, "")).items()
        },
        CONF_SWITCH_GROUPS: {
            str(k): v
            for k, v in _parse_groups(user_input.get(CONF_SWITCH_GROUPS, "")).items()
        },
        CONF_COVER_GROUPS: {
            str(k): v
            for k, v in _parse_groups(user_input.get(CONF_COVER_GROUPS, "")).items()
        },
    }


class CBusConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial setup of a C-Gate connection."""

    VERSION = 1

    def __init__(self) -> None:
        """Hold connection data between the connect and confirm steps."""
        self._conn: dict[str, Any] = {}
        self._discovered: dict[int, str] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect connection details and verify C-Gate is reachable."""
        errors: dict[str, str] = {}

        if user_input is not None:
            client = CGateClient(
                host=user_input[CONF_HOST],
                command_port=user_input[CONF_COMMAND_PORT],
                status_port=user_input[CONF_STATUS_PORT],
                project=user_input[CONF_PROJECT],
                network=user_input[CONF_NETWORK],
            )
            try:
                # Validate by opening the command connection, then auto-detect
                # the lighting groups defined in the C-Bus Toolkit project.
                await client._connect_command()  # noqa: SLF001
                self._discovered = await client.async_discover_lighting_groups()
            except CGateError:
                errors["base"] = "cannot_connect"
            finally:
                await client._close_command()  # noqa: SLF001

            if not errors:
                await self.async_set_unique_id(
                    f"{user_input[CONF_HOST]}-{user_input[CONF_PROJECT]}"
                )
                self._abort_if_unique_id_configured()
                self._conn = {
                    CONF_HOST: user_input[CONF_HOST],
                    CONF_COMMAND_PORT: user_input[CONF_COMMAND_PORT],
                    CONF_STATUS_PORT: user_input[CONF_STATUS_PORT],
                    CONF_PROJECT: user_input[CONF_PROJECT],
                    CONF_NETWORK: user_input[CONF_NETWORK],
                }
                return await self.async_step_groups()

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST): str,
                vol.Required(CONF_PROJECT): str,
                vol.Required(CONF_NETWORK, default=DEFAULT_NETWORK): int,
                vol.Required(
                    CONF_COMMAND_PORT, default=DEFAULT_COMMAND_PORT
                ): int,
                vol.Required(CONF_STATUS_PORT, default=DEFAULT_STATUS_PORT): int,
            }
        )
        return self.async_show_form(
            step_id="user", data_schema=schema, errors=errors
        )

    async def async_step_groups(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm/edit the auto-detected groups and assign platform types.

        The lights box is pre-filled with everything discovered from the
        C-Bus Toolkit project. The user can trim it and move any entries into
        the switch or cover boxes before finishing.
        """
        if user_input is not None:
            return self.async_create_entry(
                title=f"C-Bus ({self._conn[CONF_PROJECT]})",
                data=self._conn,
                options=_build_options(user_input),
            )

        prefilled = "\n".join(
            f"{gid}:{name}" for gid, name in sorted(self._discovered.items())
        )
        schema = vol.Schema(
            {
                vol.Optional(CONF_GROUPS, default=prefilled): str,
                vol.Optional(CONF_SWITCH_GROUPS, default=""): str,
                vol.Optional(CONF_COVER_GROUPS, default=""): str,
            }
        )
        count = len(self._discovered)
        return self.async_show_form(
            step_id="groups",
            data_schema=schema,
            description_placeholders={"count": str(count)},
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow handler."""
        return CBusOptionsFlow(entry)


class CBusOptionsFlow(OptionsFlow):
    """Allow editing the list of C-Bus light groups after setup."""

    # Form field for the "re-scan from C-Gate" checkbox.
    CONF_RESCAN = "rescan"

    def __init__(self, entry: ConfigEntry) -> None:
        """Store the entry being edited."""
        self._entry = entry
        # Holds light-group text to pre-fill after a re-scan, if requested.
        self._rescanned_lights: str | None = None

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit the group list, optionally re-scanning C-Gate first."""
        if user_input is not None:
            if user_input.get(self.CONF_RESCAN):
                # Merge freshly discovered groups into whatever the user has,
                # then re-show the form for confirmation (don't save yet).
                self._rescanned_lights = await self._merge_discovered(
                    user_input.get(CONF_GROUPS, "")
                )
                return await self._show_form(user_input)
            return self.async_create_entry(title="", data=_build_options(user_input))

        return await self._show_form(None)

    async def _show_form(
        self, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        """Render the options form, prefilled from options or a re-scan."""

        def _stored(key: str) -> str:
            return "\n".join(
                f"{k}:{v}" for k, v in self._entry.options.get(key, {}).items()
            )

        def _current(key: str) -> str:
            if user_input is not None:
                return user_input.get(key, "")
            return _stored(key)

        lights = (
            self._rescanned_lights
            if self._rescanned_lights is not None
            else _current(CONF_GROUPS)
        )
        schema = vol.Schema(
            {
                vol.Optional(CONF_GROUPS, default=lights): str,
                vol.Optional(
                    CONF_SWITCH_GROUPS, default=_current(CONF_SWITCH_GROUPS)
                ): str,
                vol.Optional(
                    CONF_COVER_GROUPS, default=_current(CONF_COVER_GROUPS)
                ): str,
                vol.Optional(self.CONF_RESCAN, default=False): bool,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)

    async def _merge_discovered(self, existing_text: str) -> str:
        """Re-scan C-Gate and merge new groups into the existing light list.

        Existing names are preserved; only groups not already present in any
        of the three lists are appended, so a re-scan never clobbers the
        user's manual edits or their switch/cover assignments.
        """
        client: CGateClient | None = self.hass.data.get(DOMAIN, {}).get(
            self._entry.entry_id
        )
        if client is None:
            return existing_text

        try:
            discovered = await client.async_discover_lighting_groups()
        except CGateError:
            return existing_text

        existing = _parse_groups(existing_text)
        # Groups already assigned to switches/covers shouldn't reappear here.
        assigned = set(existing)
        assigned |= {int(k) for k in self._entry.options.get(CONF_SWITCH_GROUPS, {})}
        assigned |= {int(k) for k in self._entry.options.get(CONF_COVER_GROUPS, {})}
        for gid, name in sorted(discovered.items()):
            if gid not in assigned:
                existing[gid] = name
        return "\n".join(f"{k}:{v}" for k, v in sorted(existing.items()))

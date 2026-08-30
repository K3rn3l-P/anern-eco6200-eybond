from __future__ import annotations

import logging

from ...const import PROTOCOL_PI18
from ..decoders.enums import ChargeSourcePrioritySetting
from ..decoders.pi18 import decode_pi18_response
from ..decoders.voltronic import decode_direct_response
from ..protocols.eybond_dongle import (
    send_eybond_bytes,
    send_eybond_set_command,
    send_eybond_voltronic,
)
from ..protocols.modbus_rtu import build_write_single_frame, parse_write_response
from .base import BaseAdapter

_LOGGER = logging.getLogger(__name__)

# Charger source priority lives in holding register 0x1399 and only answers on
# Modbus slave id 5 (id 1 gets no reply at all). The dongle forwards raw bytes
# to the serial line untouched, so the PI30 devcode in the envelope is
# irrelevant here. See docs/impianto-solare/dess-local-solar-priority-modbus.md
_CHARGER_PRIORITY_REGISTER = 0x1399
_MODBUS_SLAVE_ID = 5

# PCP00/01/02 map 1:1 onto the register values, and QPIRI decodes those back to
# the same names, so the select stays consistent in both directions.
_CHARGER_PRIORITY_VALUES = {
    ChargeSourcePrioritySetting.UTILITY_FIRST: 0,
    ChargeSourcePrioritySetting.SOLAR_FIRST: 1,
    ChargeSourcePrioritySetting.SOLAR_AND_UTILITY: 2,
}

class EyBondAdapter(BaseAdapter):
    """Adapter for EyBond dongle, supporting PI30 and PI18."""

    def __init__(self, uri: str, timeout: float = 30.0, strict_crc: bool = False):
        super().__init__(uri, timeout, strict_crc)
        self.is_pi18 = uri.startswith("eybond-pi18://")
        self.protocol = PROTOCOL_PI18 if self.is_pi18 else None

    async def get_data(self, command: str) -> dict:
        response = await send_eybond_voltronic(
            self.uri, command, self.timeout, protocol=self.protocol
        )
        if not response:
            return {}

        try:
            if self.is_pi18:
                return decode_pi18_response(command, response) or {}

            # For PI30, decode to ASCII first
            body, _, _ = response.partition(b"\r")
            ascii_resp = body.decode("ascii", errors="ignore")
            return decode_direct_response(command, ascii_resp) or {}
        except Exception as err:
            _LOGGER.debug("EyBondAdapter decode failed: %s", err)
            return {}

    async def set_data(self, command: str) -> dict:
        return await send_eybond_set_command(
            self.uri, command, self.timeout, protocol=self.protocol
        )

    async def set_charge_source_priority(
        self, mode: ChargeSourcePrioritySetting
    ) -> dict:
        """Write the charger priority over Modbus instead of PCPxx.

        On the Anern ECO-6200 the PCP command is accepted but only partly
        applied: PCP00 answers (ACK and leaves the register untouched, so
        "Solar priority" is unreachable over PI30. The register write is what
        the vendor cloud itself sends, and it sets all three modes.
        """
        if self.is_pi18:
            return await super().set_charge_source_priority(mode)

        value = _CHARGER_PRIORITY_VALUES.get(mode)
        if value is None:
            return await super().set_charge_source_priority(mode)

        frame = build_write_single_frame(
            _CHARGER_PRIORITY_REGISTER, value, unit_id=_MODBUS_SLAVE_ID
        )
        response = await send_eybond_bytes(
            self.uri, frame, self.timeout, context=f"PCP-modbus={value}"
        )
        if response is None:
            return {"error": "no response"}
        result = parse_write_response(response, unit_id=_MODBUS_SLAVE_ID)
        _LOGGER.debug(
            "EyBond charger priority %s -> reg 0x%04X=%d: sent=%s reply=%s (%s)",
            mode.name, _CHARGER_PRIORITY_REGISTER, value,
            frame.hex(" "), response.hex(" "), result,
        )
        return result

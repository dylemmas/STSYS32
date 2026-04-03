"""Public protocol layer API for STASYS ESP32 communication."""

from stasys.protocol.commands import (
    cmd_get_config,
    cmd_get_info,
    cmd_set_config,
    cmd_start_session,
    cmd_stop_session,
)
from stasys.protocol.crc import crc16
from stasys.protocol.flow_control import FlowControl
from stasys.protocol.packets import (
    DataRawSample,
    EvtSensorHealth,
    EvtSessionStarted,
    EvtSessionStopped,
    EvtShotDetected,
    PacketType,
    ParsedPacket,
    RawPacket,
    RspAck,
    RspConfig,
    RspError,
    RspInfo,
    recoil_axis_name,
    raw_to_accel_ms2,
    raw_to_gyro_dps,
    raw_to_temp_c,
)
from stasys.protocol.parser import ProtocolParser

__all__ = [
    # CRC
    "crc16",
    # Packet types
    "PacketType",
    "ParsedPacket",
    # Packets
    "DataRawSample",
    "EvtSensorHealth",
    "EvtSessionStarted",
    "EvtSessionStopped",
    "EvtShotDetected",
    "RawPacket",
    "RspAck",
    "RspConfig",
    "RspError",
    "RspInfo",
    # Conversions
    "raw_to_accel_ms2",
    "raw_to_gyro_dps",
    "raw_to_temp_c",
    "recoil_axis_name",
    # Parser
    "ProtocolParser",
    # Commands
    "cmd_get_config",
    "cmd_get_info",
    "cmd_set_config",
    "cmd_start_session",
    "cmd_stop_session",
    # Flow control
    "FlowControl",
]

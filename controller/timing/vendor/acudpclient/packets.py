"""AC UDP packet definitions."""
import logging

from controller.timing.vendor.acudpclient.types import (
    UINT8, UINT16, UINT32, BOOL, FLOAT, VECTOR3F, UTF32, ASCII,
    ACUDPConditionalStruct, ACUDPPacketDataArray,
)
from controller.timing.vendor.acudpclient.protocol import ACUDPConst
from controller.timing.vendor.acudpclient.packet_base import ACUDPPacket, ACUDPPacketData


LOG = logging.getLogger("ac_udp_packets")


class Version(ACUDPPacket):
    _type = ACUDPConst.ACSP_VERSION
    _bytes = (
        ('proto_version', UINT8),
    )


class CarUpdate(ACUDPPacket):
    _type = ACUDPConst.ACSP_CAR_UPDATE
    _bytes = (
        ('car_id', UINT8),
        ('pos', VECTOR3F),
        ('vel', VECTOR3F),
        ('gear', UINT8),
        ('engine_rpm', UINT16),
        ('normalized_spline_pos', FLOAT),
    )


def cond_other_car_id(packet):
    return packet.ev_type == ACUDPConst.ACSP_CE_COLLISION_WITH_CAR


class ClientEvent(ACUDPPacket):
    _type = ACUDPConst.ACSP_CLIENT_EVENT
    _bytes = (
        ('ev_type', UINT8),
        ('car_id', UINT8),
        ('other_car_id', ACUDPConditionalStruct(UINT8,
                                                cond_func=cond_other_car_id,
                                                default=255)),
        ('impact_speed', FLOAT),
        ('world_pos', VECTOR3F),
        ('rel_pos', VECTOR3F),
    )


class CarInfo(ACUDPPacket):
    _type = ACUDPConst.ACSP_CAR_INFO
    _bytes = (
        ('car_id', UINT8),
        ('is_connected', BOOL),
        ('car_model', UTF32),
        ('car_skin', UTF32),
        ('driver_name', UTF32),
        ('driver_team', UTF32),
        ('driver_guid', UTF32),
    )


class Chat(ACUDPPacket):
    _type = ACUDPConst.ACSP_CHAT
    _bytes = (
        ('car_id', UINT8),
        ('message', UTF32),
    )


class LeaderboardEntry(ACUDPPacketData):
    _bytes = (
        ('rcar_id', UINT8),
        ('rtime', UINT32),
        ('rlaps', UINT16),
        ('has_completed_flag', BOOL),
    )


class LapCompleted(ACUDPPacket):
    _type = ACUDPConst.ACSP_LAP_COMPLETED
    _bytes = (
        ('car_id', UINT8),
        ('lap_time', UINT32),
        ('cuts', UINT8),
        ('cars', ACUDPPacketDataArray(LeaderboardEntry)),
        ('grip_level', FLOAT),
    )


class EndSession(ACUDPPacket):
    _type = ACUDPConst.ACSP_END_SESSION
    _bytes = (
        ('filename', UTF32),
    )


class ClientLoaded(ACUDPPacket):
    _type = ACUDPConst.ACSP_CLIENT_LOADED
    _bytes = (
        ('car_id', UINT8),
    )


class ConnectionClosed(ACUDPPacket):
    _type = ACUDPConst.ACSP_CONNECTION_CLOSED
    _bytes = (
        ('driver_name', UTF32),
        ('driver_guid', UTF32),
        ('car_id', UINT8),
        ('car_model', ASCII),
        ('car_skin', ASCII),
    )


class Error(ACUDPPacket):
    _type = ACUDPConst.ACSP_ERROR
    _bytes = (
        ('message', UTF32),
    )


class NewConnection(ACUDPPacket):
    _type = ACUDPConst.ACSP_NEW_CONNECTION
    _bytes = (
        ('driver_name', UTF32),
        ('driver_guid', UTF32),
        ('car_id', UINT8),
        ('car_model', ASCII),
        ('car_skin', ASCII),
    )


class SessionInfo(ACUDPPacket):
    _type = ACUDPConst.ACSP_SESSION_INFO
    _bytes = (
        ('proto_version', UINT8),
        ('session_index', UINT8),
        ('current_sess_index', UINT8),
        ('session_count', UINT8),
        ('server_name', UTF32),
        ('track_name', ASCII),
        ('track_config', ASCII),
        ('name', ASCII),
        ('session_type', UINT8),
        ('time', UINT16),
        ('laps', UINT16),
        ('wait_time', UINT16),
        ('ambient_temp', UINT8),
        ('track_temp', UINT8),
        ('weather_graph', ASCII),
        ('elapsed_ms', UINT32),
    )


class NewSession(ACUDPPacket):
    _type = ACUDPConst.ACSP_NEW_SESSION
    _bytes = (
        ('proto_version', UINT8),
        ('session_index', UINT8),
        ('current_sess_index', UINT8),
        ('session_count', UINT8),
        ('server_name', UTF32),
        ('track_name', ASCII),
        ('track_config', ASCII),
        ('name', ASCII),
        ('session_type', UINT8),
        ('time', UINT16),
        ('laps', UINT16),
        ('wait_time', UINT16),
        ('ambient_temp', UINT8),
        ('track_temp', UINT8),
        ('weather_graph', ASCII),
        ('elapsed_ms', UINT32),
    )

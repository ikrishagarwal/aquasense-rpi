"""Strict configuration, with optional safe YAML parsing and no inheritance."""

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID


class ConfigError(ValueError):
    pass


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if not isinstance(key, str):
            raise ConfigError("configuration keys must be strings")
        if key in result:
            raise ConfigError("duplicate configuration key")
        result[key] = value
    return result


# Exact allowed keys and types. None denotes an unresolved board-specific value.
SCHEMA: dict[str, Any] = {
    "schema_version": int, "mode": str, "device_id": (str, type(None)),
    "hardware_verified": bool,
    "pins_bcm": dict.fromkeys(("flow", "temperature", "tank_float", "proximity",
        "drain_driver", "illumination", "shutter", "i2c_sda", "i2c_scl"), int),
    "drain": {"enabled": bool, "driver_active_high": (bool, type(None)),
        "physical_enable_pin_bcm": (int, type(None)),
        "independent_cutoff_verified": bool, "max_on_s": (int, float, type(None)),
        "quiet_period_s": (int, float, type(None))},
    "flow": {"pulses_per_litre": (int, float), "calibrated": bool,
        "merge_timeout_s": (int, float), "max_session_s": (int, float)},
    "sensors": {"adc_address": (int, type(None)), "adc_supply_v": (int, float),
        "adc_channels": dict.fromkeys(("tds_in", "tds_out", "ph_approx"), int),
        "tds_poll_s": (int, float), "temperature_poll_s": (int, float),
        "ph_poll_s": (int, float), "tds_calibration": type(None),
        "ph_calibration": type(None), "float_ok_level": (bool, type(None)),
        "float_debounce_s": (int, float, type(None)),
        "shutter_open_level": (bool, type(None))},
    "vision": {"enabled": bool, "deadline_s": (int, float), "burst_frames": int,
        "width": int, "height": int, "threads": int, "max_pending": int,
        "camera_orientation": (int, type(None)), "model_path": (str, type(None)), "socket_path": str},
    "mqtt": {"enabled": bool, "broker": (str, type(None)), "verify_tls": bool,
        "telemetry_s": (int, float), "outbox_batch_size": int, "port": int,
        "ca_file": (str, type(None)), "username_env": str, "password_env": str},
    "storage": {"path": str, "writer_queue_capacity": int, "synchronous": str},
    "alerts": {"tds_out_threshold_ppm": (int, float, type(None)),
        "filter_capacity_litres": (int, float, type(None)),
        "filter_alert_fraction": (int, float), "tds_hysteresis_ppm": (int, float, type(None)),
        "debounce_readings": int},
}


def _validate(value: Any, spec: Any, path: str = "config") -> None:
    if isinstance(spec, dict):
        if not isinstance(value, dict) or value.keys() != spec.keys():
            raise ConfigError(f"{path}: missing or unknown keys")
        for key, child in spec.items():
            _validate(value[key], child, f"{path}.{key}")
    elif type(value) not in (spec if isinstance(spec, tuple) else (spec,)):
        raise ConfigError(f"{path}: invalid type")
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            finite = math.isfinite(value)
        except OverflowError:
            finite = False
        if not finite:
            raise ConfigError(f"{path}: must be finite")


@dataclass(frozen=True)
class MQTTConfig:
    host: str
    port: int
    ca_file: str | None
    username_env: str
    password_env: str
    batch_size: int


@dataclass(frozen=True)
class Config:
    mode: str
    device_id: str | None
    database: Path
    writer_capacity: int
    pulses_per_litre: float
    calibrated: bool
    merge_timeout_s: float
    max_session_s: float
    tds_poll_s: float
    temperature_poll_s: float
    ph_poll_s: float
    recognition_deadline_s: float
    mqtt: MQTTConfig | None
    vision_socket: Path | None
    telemetry_s: float
    tds_threshold: float | None
    tds_hysteresis: float | None
    alert_debounce: int
    filter_capacity: float | None
    filter_fraction: float


def load_config(path: Path) -> Config:
    try:
        with path.open("rb") as stream:
            data = stream.read(65537)
        if len(data) > 65536:
            raise ConfigError("configuration exceeds 64 KiB")
        try:
            raw = json.loads(data, object_pairs_hook=_pairs)
        except json.JSONDecodeError:
            try:
                import yaml
            except ImportError as exc:
                raise ConfigError('non-JSON YAML requires optional PyYAML dependency') from exc

            class StrictLoader(yaml.SafeLoader):
                pass

            def mapping(loader: Any, node: Any) -> dict[str, Any]:
                return _pairs([(loader.construct_object(key, deep=True),
                                loader.construct_object(value, deep=True))
                               for key, value in node.value])

            StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
            try:
                if any(isinstance(token, yaml.tokens.AliasToken) for token in yaml.scan(data)):
                    raise ConfigError('YAML aliases are not supported')
                raw = yaml.load(data, Loader=StrictLoader)
            except yaml.YAMLError as exc:
                raise ConfigError('invalid YAML configuration') from exc
    except ConfigError:
        raise
    except (OSError, UnicodeError, RecursionError, ValueError) as exc:
        raise ConfigError("cannot read configuration") from exc
    _validate(raw, SCHEMA)
    if raw["schema_version"] != 1 or raw["mode"] not in ("simulation", "hardware"):
        raise ConfigError("unsupported schema_version or mode")
    if raw["device_id"] is not None:
        try:
            if str(UUID(raw["device_id"])) != raw["device_id"]:
                raise ValueError("noncanonical UUID")
        except ValueError as exc:
            raise ConfigError("device_id must be a registered device UUID") from exc
    pins = list(raw["pins_bcm"].values())
    enable = raw["drain"]["physical_enable_pin_bcm"]
    if enable is not None:
        pins.append(enable)
    if any(p < 0 or p > 27 for p in pins) or len(set(pins)) != len(pins):
        raise ConfigError("invalid or conflicting BCM pins")
    for section in ("flow", "sensors", "vision", "mqtt", "alerts"):
        for key, value in raw[section].items():
            if type(value) in (int, float) and value <= 0:
                # Orientation 0 is meaningful; ADC address is checked separately.
                if key != "camera_orientation":
                    raise ConfigError(f"{section}.{key}: must be positive")
    flow = raw["flow"]
    if flow["max_session_s"] <= flow["merge_timeout_s"]:
        raise ConfigError("maximum session must exceed merge timeout")
    if not 1 <= raw["storage"]["writer_queue_capacity"] <= 4096:
        raise ConfigError("writer_queue_capacity must be 1..4096")
    if not raw["storage"]["path"] or raw["storage"]["synchronous"] != "FULL":
        raise ConfigError("storage requires a path and FULL durability")
    if not raw["mqtt"]["verify_tls"] or raw["mqtt"]["outbox_batch_size"] > 20:
        raise ConfigError("TLS verification required; batch must be <=20")
    if raw["vision"]["max_pending"] != 1:
        raise ConfigError("vision max_pending must be 1")
    if not 0 < raw["alerts"]["filter_alert_fraction"] <= 1:
        raise ConfigError("filter_alert_fraction must be in (0, 1]")
    channels = list(raw["sensors"]["adc_channels"].values())
    if len(set(channels)) != len(channels) or any(c not in range(4) for c in channels):
        raise ConfigError("invalid or conflicting ADC channels")
    address = raw['sensors']['adc_address']
    if address is not None and address not in range(0x48, 0x4C):
        raise ConfigError('ADS1115 address must be 0x48..0x4B')
    orientation = raw['vision']['camera_orientation']
    if orientation is not None and orientation not in (0, 90, 180, 270):
        raise ConfigError('invalid camera orientation')
    if (raw['vision']['burst_frames'], raw['vision']['width'],
            raw['vision']['height'], raw['vision']['threads']) != (3, 640, 480, 1):
        raise ConfigError('only the initial bounded camera contract is supported')
    if raw["sensors"]["adc_supply_v"] != 3.3:
        raise ConfigError("only proposed 3.3 V ADC contract is supported")
    for key in ("max_on_s", "quiet_period_s"):
        value = raw["drain"][key]
        if value is not None and value <= 0:
            raise ConfigError(f"drain.{key}: must be positive")
    if raw["drain"]["enabled"]:
        raise ConfigError("physical drain actuation is unavailable")
    if raw["mode"] == "hardware":
        raise ConfigError("hardware profile blocked pending verified adapters and bench")
    mqtt = None
    if not 1 <= raw['mqtt']['port'] <= 65535:
        raise ConfigError('invalid MQTT port')
    if raw['mqtt']['enabled']:
        if raw['device_id'] is None or not raw['mqtt']['broker']:
            raise ConfigError('MQTT requires registered device UUID and broker')
        mqtt = MQTTConfig(raw['mqtt']['broker'], raw['mqtt']['port'], raw['mqtt']['ca_file'],
                          raw['mqtt']['username_env'], raw['mqtt']['password_env'], raw['mqtt']['outbox_batch_size'])
    if not raw['vision']['socket_path'].startswith('/'):
        raise ConfigError('vision socket path must be absolute')
    if raw['vision']['model_path'] is not None:
        raise ConfigError('real model adapter unavailable; IPC simulation only')
    if raw['alerts']['tds_out_threshold_ppm'] is not None:
        hysteresis = raw['alerts']['tds_hysteresis_ppm']
        if hysteresis is None or hysteresis >= raw['alerts']['tds_out_threshold_ppm']:
            raise ConfigError('TDS threshold requires lower positive hysteresis')
    database = Path(raw["storage"]["path"])
    if not database.is_absolute():
        database = path.resolve().parent.parent / database
    return Config(raw["mode"], raw["device_id"], database,
        raw["storage"]["writer_queue_capacity"], float(flow["pulses_per_litre"]),
        flow["calibrated"], float(flow["merge_timeout_s"]), float(flow["max_session_s"]), float(raw["sensors"]["tds_poll_s"]),
        float(raw["sensors"]["temperature_poll_s"]), float(raw["sensors"]["ph_poll_s"]),
        float(raw["vision"]["deadline_s"]), mqtt,
        Path(raw["vision"]["socket_path"]) if raw["vision"]["enabled"] else None,
        float(raw['mqtt']['telemetry_s']), raw['alerts']['tds_out_threshold_ppm'],
        raw['alerts']['tds_hysteresis_ppm'], raw['alerts']['debounce_readings'],
        raw['alerts']['filter_capacity_litres'], float(raw['alerts']['filter_alert_fraction']))

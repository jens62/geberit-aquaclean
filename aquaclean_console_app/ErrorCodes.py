"""
Centralized error code definitions for Geberit AquaClean Console App.

Error Code Format: E[Category][Number]
- E = Error prefix
- Category = 1 digit (0-9)
- Number = 3 digits (001-999)

Categories:
- E0xxx: BLE Connection Errors
- E1xxx: ESP32 Proxy Errors
- E2xxx: Recovery Protocol Errors
- E3xxx: Command Execution Errors
- E4xxx: API/HTTP Errors
- E5xxx: MQTT Errors
- E6xxx: Configuration Errors
- E7xxx: Internal/System Errors
"""

from typing import NamedTuple
from datetime import datetime
import json


class ErrorCode(NamedTuple):
    """Error code definition with metadata."""
    code: str
    message: str
    category: str
    severity: str  # "ERROR", "WARNING", "CRITICAL"


# ============================================================================
# E0000 - Success Code
# ============================================================================

E0000 = ErrorCode("E0000", "No error", "BLE", "INFO")

# ============================================================================
# E0xxx - BLE Connection Errors (E0001-E0999)
# ============================================================================
E0001 = ErrorCode("E0001", "BLE device not found (local adapter)", "BLE", "ERROR")
E0002 = ErrorCode("E0002", "BLE device not found (ESP32 proxy)", "BLE", "ERROR")
E0003 = ErrorCode("E0003", "BLE connection timeout", "BLE", "ERROR")
E0004 = ErrorCode("E0004", "GATT service not found", "BLE", "ERROR")
E0005 = ErrorCode("E0005", "GATT characteristics not found", "BLE", "ERROR")
E0006 = ErrorCode("E0006", "Characteristic read failed", "BLE", "ERROR")
E0007 = ErrorCode("E0007", "Characteristic write failed", "BLE", "ERROR")
E0008 = ErrorCode("E0008", "BLE disconnected unexpectedly", "BLE", "WARNING")
E0009 = ErrorCode("E0009", "Start notify failed", "BLE", "ERROR")

# ============================================================================
# E1xxx - ESP32 Proxy Errors
# ============================================================================

E1001 = ErrorCode("E1001", "ESP32 proxy connection timeout", "ESP32", "ERROR")
E1002 = ErrorCode("E1002", "ESP32 proxy connection failed", "ESP32", "ERROR")
E1003 = ErrorCode("E1003", "ESP32 BLE connection error", "ESP32", "ERROR")
E1004 = ErrorCode("E1004", "ESP32 log streaming failed", "ESP32", "WARNING")
E1005 = ErrorCode("E1005", "ESP32 device info failed", "ESP32", "WARNING")
E1006 = ErrorCode("E1006", "ESP32 service fetch failed", "ESP32", "ERROR")
E1007 = ErrorCode("E1007", "ESP32 write timeout", "ESP32", "ERROR")
E1008 = ErrorCode("E1008", "ESP32 notification worker error", "ESP32", "ERROR")

# ============================================================================
# E2xxx - Recovery Protocol Errors
# ============================================================================

E2001 = ErrorCode("E2001", "Recovery: Device won't disappear (ESP32)", "RECOVERY", "WARNING")
E2002 = ErrorCode("E2002", "Recovery: Device won't reappear (ESP32)", "RECOVERY", "ERROR")
E2003 = ErrorCode("E2003", "Recovery: Device won't disappear (local)", "RECOVERY", "WARNING")
E2004 = ErrorCode("E2004", "Recovery: Device won't reappear (local)", "RECOVERY", "ERROR")
E2005 = ErrorCode("E2005", "Recovery: ESP32 proxy connection failed", "RECOVERY", "ERROR")

# ============================================================================
# E3xxx - Command Execution Errors
# ============================================================================

E3001 = ErrorCode("E3001", "Command failed: BLE not connected", "COMMAND", "ERROR")
E3002 = ErrorCode("E3002", "Command failed: Unknown command", "COMMAND", "ERROR")
E3003 = ErrorCode("E3003", "Command failed: Execution error", "COMMAND", "ERROR")
E3004 = ErrorCode("E3004", "MQTT command: Toggle anal failed", "COMMAND", "ERROR")
E3005 = ErrorCode("E3005", "MQTT command: Set connection failed", "COMMAND", "ERROR")
E3006 = ErrorCode("E3006", "MQTT command: Set poll interval failed", "COMMAND", "ERROR")
E3007 = ErrorCode("E3007", "MQTT command: Disconnect failed", "COMMAND", "ERROR")

# ============================================================================
# E4xxx - API/HTTP Errors
# ============================================================================

E4001 = ErrorCode("E4001", "Invalid BLE connection mode", "API", "ERROR")
E4002 = ErrorCode("E4002", "Invalid poll interval", "API", "ERROR")
E4003 = ErrorCode("E4003", "BLE client not connected", "API", "ERROR")
E4004 = ErrorCode("E4004", "SSE timeout (heartbeat)", "API", "WARNING")

# ============================================================================
# E5xxx - MQTT Errors
# ============================================================================

E5001 = ErrorCode("E5001", "MQTT connection failed", "MQTT", "ERROR")
E5002 = ErrorCode("E5002", "MQTT publish failed", "MQTT", "ERROR")
E5003 = ErrorCode("E5003", "MQTT disconnect failed", "MQTT", "WARNING")
E5004 = ErrorCode("E5004", "MQTT invalid poll interval value", "MQTT", "WARNING")

# ============================================================================
# E6xxx - Configuration Errors
# ============================================================================

E6001 = ErrorCode("E6001", "Config: Poll interval parse failed", "CONFIG", "WARNING")
E6002 = ErrorCode("E6002", "Config: API poll interval parse failed", "CONFIG", "WARNING")

# ============================================================================
# E7xxx - Internal/System Errors
# ============================================================================

E7001 = ErrorCode("E7001", "Shutdown timeout", "SYSTEM", "WARNING")
E7002 = ErrorCode("E7002", "Poll loop error", "SYSTEM", "WARNING")
E7003 = ErrorCode("E7003", "Service discovery error (fatal)", "SYSTEM", "CRITICAL")
E7004 = ErrorCode("E7004", "General exception", "SYSTEM", "ERROR")


class ErrorManager:
    """Utility class for formatting errors across different interfaces."""

    @staticmethod
    def to_json(error_code: ErrorCode, details: str = None, include_timestamp: bool = True) -> str:
        """
        Format error as JSON string for MQTT.

        Args:
            error_code: ErrorCode instance
            details: Optional additional error details to append to message
            include_timestamp: Include ISO timestamp in JSON (default: True)

        Returns:
            JSON string: {"code": "E0003", "message": "...", "timestamp": "..."}
        """
        message = error_code.message
        if details:
            message = f"{message}: {details}"

        data = {
            "code": error_code.code,
            "message": message
        }

        if include_timestamp:
            data["timestamp"] = datetime.utcnow().isoformat() + "Z"

        return json.dumps(data)

    @staticmethod
    def to_dict(error_code: ErrorCode, details: str = None) -> dict:
        """
        Format error as dictionary for REST API responses.

        Args:
            error_code: ErrorCode instance
            details: Optional additional error details

        Returns:
            dict: {"code": "E0003", "message": "..."}
        """
        message = error_code.message
        if details:
            message = f"{message}: {details}"

        return {
            "code": error_code.code,
            "message": message
        }

    @staticmethod
    def to_cli(error_code: ErrorCode, details: str = None) -> str:
        """
        Format error for CLI output.

        Args:
            error_code: ErrorCode instance
            details: Optional additional error details

        Returns:
            str: "ERROR [E0003]: BLE connection timeout"
        """
        message = error_code.message
        if details:
            message = f"{message}: {details}"

        severity = error_code.severity
        return f"{severity} [{error_code.code}]: {message}"

    @staticmethod
    def to_sse_state(error_code: ErrorCode, details: str = None) -> dict:
        """
        Format error for SSE state updates (webapp).

        Args:
            error_code: ErrorCode instance
            details: Optional additional error details

        Returns:
            dict: Fields to merge into device_state
                {
                    "ble_error_code": "E0003",
                    "ble_error_message": "BLE connection timeout"
                }
        """
        message = error_code.message
        if details:
            message = f"{message}: {details}"

        return {
            "ble_error_code": error_code.code,
            "ble_error_message": message
        }

    @staticmethod
    def clear_error() -> str:
        """Return JSON for "no error" state (MQTT)."""
        return ErrorManager.to_json(E0000, include_timestamp=False)

    @staticmethod
    def clear_error_sse() -> dict:
        """Return fields to clear error in SSE state."""
        return {
            "ble_error_code": None,
            "ble_error_message": None
        }


# Convenience function for backward compatibility
def format_error_json(error_code: ErrorCode, details: str = None) -> str:
    """Format error as JSON string (MQTT-compatible)."""
    return ErrorManager.to_json(error_code, details)

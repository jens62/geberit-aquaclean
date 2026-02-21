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
    severity: str   # "INFO", "WARNING", "ERROR", "CRITICAL"
    hint: str = ""  # User-facing resolution instructions
    doc_url: str = ""  # Future: link to documentation page


# ============================================================================
# E0000 - Success Code
# ============================================================================

E0000 = ErrorCode("E0000", "No error", "BLE", "INFO")

# ============================================================================
# E0xxx - BLE Connection Errors (E0001-E0999)
# ============================================================================

E0001 = ErrorCode(
    "E0001", "BLE device not found (local adapter)", "BLE", "ERROR",
    hint="Ensure the Geberit AquaClean is powered on and within BLE range. "
         "Check that your Bluetooth adapter is enabled and recognised by the OS.",
)
E0002 = ErrorCode(
    "E0002", "BLE device not found (ESP32 proxy)", "BLE", "ERROR",
    hint="Ensure the Geberit AquaClean is powered on and within BLE range of the ESP32 proxy. "
         "Try moving the ESP32 closer to the device.",
)
E0003 = ErrorCode(
    "E0003", "BLE connection timeout", "BLE", "ERROR",
    hint="The device did not respond in time. "
         "Try power cycling the Geberit AquaClean (unplug for 10 seconds). "
         "If using an ESP32 proxy, also power cycle it.",
)
E0004 = ErrorCode(
    "E0004", "GATT service not found", "BLE", "ERROR",
    hint="The expected BLE service was not found on the device. "
         "Power cycle the Geberit AquaClean. "
         "If the problem persists, verify the device address in config.ini [BLE].",
)
E0005 = ErrorCode(
    "E0005", "GATT characteristics not found", "BLE", "ERROR",
    hint="BLE characteristics were not found on the device. "
         "Power cycle the Geberit AquaClean.",
)
E0006 = ErrorCode(
    "E0006", "Characteristic read failed", "BLE", "ERROR",
    hint="Reading from the device failed. "
         "Power cycle the Geberit AquaClean. "
         "If using an ESP32 proxy, also power cycle it.",
)
E0007 = ErrorCode(
    "E0007", "Characteristic write failed", "BLE", "ERROR",
    hint="Writing to the device failed. "
         "Power cycle the Geberit AquaClean. "
         "If using an ESP32 proxy, also power cycle it.",
)
E0008 = ErrorCode(
    "E0008", "BLE disconnected unexpectedly", "BLE", "WARNING",
    hint="The Geberit AquaClean dropped the BLE connection. "
         "The app will attempt to reconnect automatically. "
         "If this happens frequently, check BLE signal strength or move the adapter closer.",
)
E0009 = ErrorCode(
    "E0009", "Start notify failed", "BLE", "ERROR",
    hint="Failed to subscribe to BLE notifications. "
         "Power cycle the Geberit AquaClean. "
         "If using an ESP32 proxy, also power cycle it.",
)

# ============================================================================
# E1xxx - ESP32 Proxy Errors
# ============================================================================

E1001 = ErrorCode(
    "E1001", "ESP32 proxy connection timeout", "ESP32", "ERROR",
    hint="The ESP32 did not respond in time. "
         "Ensure it is powered on and connected to the network. "
         "Try power cycling the ESP32 (unplug for 10 seconds).",
)
E1002 = ErrorCode(
    "E1002", "ESP32 proxy connection failed", "ESP32", "ERROR",
    hint="Cannot reach the ESP32 proxy. "
         "Verify the IP address and port in config.ini [ESPHOME]. "
         "Ensure the ESP32 is powered on and on the same network as this device.",
)
E1003 = ErrorCode(
    "E1003", "ESP32 BLE connection error", "ESP32", "ERROR",
    hint="The ESP32 encountered a BLE error connecting to the Geberit. "
         "Try power cycling the ESP32. "
         "If the Geberit appears unresponsive, power cycle it too.",
)
E1004 = ErrorCode(
    "E1004", "ESP32 log streaming failed", "ESP32", "WARNING",
    hint="Log streaming from the ESP32 is unavailable. "
         "This is non-critical — the main BLE connection is unaffected. "
         "Check that the ESP32 is reachable on the network.",
)
E1005 = ErrorCode(
    "E1005", "ESP32 device info failed", "ESP32", "WARNING",
    hint="Could not fetch device information from the ESP32. "
         "Non-critical — the app continues with defaults. "
         "Try power cycling the ESP32 if other issues occur.",
)
E1006 = ErrorCode(
    "E1006", "ESP32 service fetch failed", "ESP32", "ERROR",
    hint="Failed to retrieve BLE services via the ESP32. "
         "Power cycle the ESP32 and the Geberit AquaClean.",
)
E1007 = ErrorCode(
    "E1007", "ESP32 write timeout", "ESP32", "ERROR",
    hint="Writing to the Geberit via the ESP32 timed out. "
         "Power cycle the ESP32. "
         "If the problem persists, check network stability between the host and the ESP32.",
)
E1008 = ErrorCode(
    "E1008", "ESP32 notification worker error", "ESP32", "ERROR",
    hint="Internal error in the ESP32 notification handler. "
         "Power cycle the ESP32. "
         "If the problem persists, restart the app.",
)

# ============================================================================
# E2xxx - Recovery Protocol Errors
# ============================================================================

E2001 = ErrorCode(
    "E2001", "Recovery: Device won't disappear (ESP32)", "RECOVERY", "WARNING",
    hint="The Geberit is still advertising after 2 minutes. "
         "Power cycle the toilet (unplug for at least 10 seconds) to force a full restart. "
         "Recovery will proceed automatically once it goes offline.",
)
E2002 = ErrorCode(
    "E2002", "Recovery: Device won't reappear (ESP32)", "RECOVERY", "ERROR",
    hint="The Geberit did not come back online after 2 minutes. "
         "Ensure it is powered on and within BLE range of the ESP32 proxy. "
         "Check the toilet's power supply.",
)
E2003 = ErrorCode(
    "E2003", "Recovery: Device won't disappear (local)", "RECOVERY", "WARNING",
    hint="The Geberit is still advertising after 2 minutes. "
         "Power cycle the toilet (unplug for at least 10 seconds) to force a full restart. "
         "Recovery will proceed automatically once it goes offline.",
)
E2004 = ErrorCode(
    "E2004", "Recovery: Device won't reappear (local)", "RECOVERY", "ERROR",
    hint="The Geberit did not come back online after 2 minutes. "
         "Ensure it is powered on and within BLE range of the host's Bluetooth adapter. "
         "Check the toilet's power supply.",
)
E2005 = ErrorCode(
    "E2005", "Recovery: ESP32 proxy connection failed", "RECOVERY", "ERROR",
    hint="The ESP32 proxy is unreachable. "
         "Power cycle the ESP32 (unplug for 10 seconds) and ensure it is back on the network. "
         "The app is currently using local Bluetooth as a fallback — "
         "a local BT adapter must be present on this device.",
)

# ============================================================================
# E3xxx - Command Execution Errors
# ============================================================================

E3001 = ErrorCode(
    "E3001", "Command failed: BLE not connected", "COMMAND", "ERROR",
    hint="The Geberit is not connected. "
         "Wait for the app to reconnect, or click 'Reconnect' in the webapp.",
)
E3002 = ErrorCode(
    "E3002", "Command failed: Unknown command", "COMMAND", "ERROR",
    hint="An unrecognised command was sent. "
         "Check your automation script or API call for typos in the command name.",
)
E3003 = ErrorCode(
    "E3003", "Command failed: Execution error", "COMMAND", "ERROR",
    hint="The command could not be executed on the device. "
         "Ensure the Geberit is connected and try again.",
)
E3004 = ErrorCode(
    "E3004", "MQTT command: Toggle anal failed", "COMMAND", "ERROR",
    hint="The MQTT command to toggle the anal shower failed. "
         "Ensure the device is connected and try again.",
)
E3005 = ErrorCode(
    "E3005", "MQTT command: Set connection failed", "COMMAND", "ERROR",
    hint="Failed to change the BLE connection mode via MQTT. "
         "Valid values are 'persistent' and 'on-demand'.",
)
E3006 = ErrorCode(
    "E3006", "MQTT command: Set poll interval failed", "COMMAND", "ERROR",
    hint="Failed to set the poll interval via MQTT. "
         "Ensure the value is a valid number in seconds (use 0 to disable polling).",
)
E3007 = ErrorCode(
    "E3007", "MQTT command: Disconnect failed", "COMMAND", "ERROR",
    hint="The disconnect command failed. The app will continue running. "
         "Check the application logs for details.",
)

# ============================================================================
# E4xxx - API/HTTP Errors
# ============================================================================

E4001 = ErrorCode(
    "E4001", "Invalid BLE connection mode", "API", "ERROR",
    hint="Valid values are 'persistent' and 'on-demand'. "
         "Update your request and try again.",
)
E4002 = ErrorCode(
    "E4002", "Invalid poll interval", "API", "ERROR",
    hint="Poll interval must be a number in seconds. "
         "Use 0 to disable polling. Negative values are not allowed.",
)
E4003 = ErrorCode(
    "E4003", "BLE client not connected", "API", "ERROR",
    hint="The Geberit is not connected. "
         "Wait for the app to reconnect, or use the 'Reconnect' button / POST /connect endpoint.",
)
E4004 = ErrorCode(
    "E4004", "SSE timeout (heartbeat)", "API", "WARNING",
    hint="The webapp's event stream timed out and was closed. "
         "Reload the page — the browser will reconnect automatically.",
)

# ============================================================================
# E5xxx - MQTT Errors
# ============================================================================

E5001 = ErrorCode(
    "E5001", "MQTT connection failed", "MQTT", "ERROR",
    hint="Cannot connect to the MQTT broker. "
         "Verify the server address, port, and credentials in config.ini [MQTT]. "
         "Ensure the broker is running and reachable on the network.",
)
E5002 = ErrorCode(
    "E5002", "MQTT publish failed", "MQTT", "WARNING",
    hint="A message could not be published to MQTT. "
         "Check the broker connection and network stability. "
         "The app will retry on the next event.",
)
E5003 = ErrorCode(
    "E5003", "MQTT disconnect failed", "MQTT", "WARNING",
    hint="Non-critical. The MQTT connection did not close cleanly. "
         "The broker will handle the stale connection automatically.",
)
E5004 = ErrorCode(
    "E5004", "MQTT invalid poll interval value", "MQTT", "WARNING",
    hint="The poll interval received via MQTT is not a valid number. "
         "Send a numeric value in seconds (e.g. 10 or 10.5). Use 0 to disable polling.",
)

# ============================================================================
# E6xxx - Configuration Errors
# ============================================================================

E6001 = ErrorCode(
    "E6001", "Config: Poll interval parse failed", "CONFIG", "WARNING",
    hint="The poll interval in config.ini could not be read. "
         "Check that [POLL] interval is a valid number. The app will use the default value.",
)
E6002 = ErrorCode(
    "E6002", "Config: API poll interval parse failed", "CONFIG", "WARNING",
    hint="The poll interval value could not be parsed. "
         "Ensure you are sending a valid number in seconds.",
)

# ============================================================================
# E7xxx - Internal/System Errors
# ============================================================================

E7001 = ErrorCode(
    "E7001", "Shutdown timeout", "SYSTEM", "WARNING",
    hint="The app did not shut down within the expected time. "
         "This is usually harmless. Check logs if the issue occurs repeatedly.",
)
E7002 = ErrorCode(
    "E7002", "Poll loop error", "SYSTEM", "WARNING",
    hint="An error occurred in the background polling loop. "
         "The app will continue. Check the application logs for details.",
)
E7003 = ErrorCode(
    "E7003", "Service discovery error (fatal)", "SYSTEM", "CRITICAL",
    hint="A critical BLE error occurred and the app has stopped. "
         "Restart the app. If the problem persists, power cycle the Geberit AquaClean "
         "and check that the Bluetooth adapter is working.",
)
E7004 = ErrorCode(
    "E7004", "General exception", "SYSTEM", "ERROR",
    hint="An unexpected error occurred. "
         "Check the application logs for details. "
         "Restart the app if problems persist.",
)


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
            JSON string: {"code": "E0003", "message": "...", "hint": "...", "timestamp": "..."}
        """
        message = error_code.message
        if details:
            message = f"{message}: {details}"

        data = {
            "code": error_code.code,
            "message": message,
            "hint": error_code.hint,
        }

        if error_code.doc_url:
            data["doc_url"] = error_code.doc_url

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
            dict: {"code": "E0003", "message": "...", "hint": "..."}
        """
        message = error_code.message
        if details:
            message = f"{message}: {details}"

        result = {
            "code": error_code.code,
            "message": message,
            "hint": error_code.hint,
        }
        if error_code.doc_url:
            result["doc_url"] = error_code.doc_url
        return result

    @staticmethod
    def to_cli(error_code: ErrorCode, details: str = None) -> str:
        """
        Format error for CLI output.

        Args:
            error_code: ErrorCode instance
            details: Optional additional error details

        Returns:
            str: "ERROR [E0003]: BLE connection timeout\n  Hint: ..."
        """
        message = error_code.message
        if details:
            message = f"{message}: {details}"

        severity = error_code.severity
        result = f"{severity} [{error_code.code}]: {message}"
        if error_code.hint:
            result += f"\n  Hint: {error_code.hint}"
        return result

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
                    "ble_error_message": "BLE connection timeout",
                    "ble_error_hint": "...",
                }
        """
        message = error_code.message
        if details:
            message = f"{message}: {details}"

        return {
            "ble_error_code": error_code.code,
            "ble_error_message": message,
            "ble_error_hint": error_code.hint,
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
            "ble_error_message": None,
            "ble_error_hint": None,
        }


# Convenience function for backward compatibility
def format_error_json(error_code: ErrorCode, details: str = None) -> str:
    """Format error as JSON string (MQTT-compatible)."""
    return ErrorManager.to_json(error_code, details)

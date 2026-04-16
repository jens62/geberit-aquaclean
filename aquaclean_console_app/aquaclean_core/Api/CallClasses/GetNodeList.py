import logging
logger = logging.getLogger(__name__)

from aquaclean_console_app.aquaclean_core.Api.Attributes.ApiCallAttribute import ApiCallAttribute


class GetNodeList:
    """
    GetNodeList — proc 0x05, ctx 0x01, no payload.

    Source: thomas-bingel C# tmp.txt:
        [Api(Context = 1, Procedure = 0x05)]
        NodeList GetNodeList();

    NodeList DTO (NodeList.cs):
        [Length=1]   int A    — count of node IDs
        [Length=128] byte[] B — supported node/procedure IDs, zero-padded

    Total response: 129 bytes.

    Live result (2026-04-16, AquaClean Mera Comfort):
        count=12, node_ids=[0x03,0x04,0x05,0x06,0x07,0x08,0x09,0x0A,0x0B,0x0C,0x0E,0x0F]
    These correspond to ctx=0x01 procedure codes supported by the device.
    """

    api_call_attribute = ApiCallAttribute(0x01, 0x05, 0x01)

    def __init__(self):
        logger.trace("GetNodeList: __init__")

    def get_api_call_attribute(self) -> ApiCallAttribute:
        logger.trace("GetNodeList: get_api_call_attribute")
        return self.api_call_attribute

    def get_payload(self):
        logger.trace("GetNodeList: get_payload")
        return bytearray()

    def result(self, data: bytes) -> dict:
        logger.trace("GetNodeList: result, len=%d", len(data) if data else 0)
        if not data:
            return {"count": 0, "node_ids": []}
        count = data[0]
        node_ids = [
            f"0x{data[i]:02X}"
            for i in range(1, 1 + min(count, len(data) - 1))
        ]
        logger.debug("GetNodeList: count=%d, node_ids=%s", count, node_ids)
        return {"count": count, "node_ids": node_ids}

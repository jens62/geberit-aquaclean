"""Geberit Ble20 command identifier constants.

All constants derived from the Geberit vendor application source.
"""
from enum import IntEnum


class CommandId(IntEnum):
    Inventory                         = 0x00
    InventoryCount                    = 0x01
    InventoryData                     = 0x02
    DatapointInfo                     = 0x05
    DatapointInfoData                 = 0x06
    DatapointInfoError                = 0x07
    ReadCmd                           = 0x10
    ReadAns                           = 0x11
    ReadError                         = 0x12
    ReadDefault                       = 0x13
    ReadDefaultData                   = 0x14
    ReadDefaultError                  = 0x15
    WriteCmd                          = 0x20
    WriteAck                          = 0x21
    WriteError                        = 0x22
    ResetDatapoint                    = 0x23
    ResetDatapointAck                 = 0x24
    ResetDatapointError               = 0x25
    NotifyEnable                      = 0x30
    NotifyDisable                     = 0x31
    NotifyAck                         = 0x32
    NotifyError                       = 0x33
    NotifyData                        = 0x34
    Unlock                            = 0x40
    UnlockAck                         = 0x41
    UnlockError                       = 0x42
    EventStorageInventory             = 0x50
    EventStorageInventoryCount        = 0x51
    EventStorageInventoryData         = 0x52
    ReadEventStorageWithIndexCmd      = 0x55
    ReadEventStorageCount             = 0x56
    ReadEventStorageData              = 0x57
    ReadEventStorageError             = 0x58
    ReadEventStorageWithTimeWindowCmd = 0x59
    ReadEventStorageCauses            = 0x60
    ReadEventStorageCausesCount       = 0x61
    ReadEventStorageCausesData        = 0x62
    ReadEventStorageCausesError       = 0x63
    ListInventoryCmd                  = 0x70
    ListInventoryCount                = 0x71
    ListInventoryData                 = 0x72
    ReadListCmd                       = 0x73
    ReadListAck                       = 0x74
    ReadListData                      = 0x75
    ListNotify                        = 0x76
    ListNotifyAck                     = 0x77
    ListNotifyData                    = 0x78
    TunnelFrame                       = 0xD0
    DeviceStatusData                  = 0xE0
    LinkTestWrite                     = 0xF0
    LinkTestNotify                    = 0xF1
    LoopbackRequest                   = 0xF2
    LoopbackResponse                  = 0xF3
    CapabilitiesCmd                   = 0xFD
    CapabilitiesAck                   = 0xFE

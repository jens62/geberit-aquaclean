
from aquaclean_console_app.aquaclean_core.Clients                                    import AquaCleanClient
from aquaclean_console_app.aquaclean_core.Clients                                    import AquaCleanBaseClient


class AquaCleanClientFactory:
    def __init__(self, bluetooth_le_connector):
        self.bluetooth_le_connector = bluetooth_le_connector

    def create_client(self):
        return AquaCleanClient.AquaCleanClient(self.bluetooth_le_connector)

    def create_base_client(self):
        return AquaCleanBaseClient.AquaCleanBaseClient(self.bluetooth_le_connector)


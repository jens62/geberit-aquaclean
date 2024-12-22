
from dataclasses import dataclass

@dataclass
class DeviceIdentification:
    sap_number: str
    serial_number: str
    production_date: str
    description: str

    def __init__(self, sap_number: bytes = [None] * 12, serial_number: bytes = [None] * 20, production_date: bytes = [None] * 10, description: bytes = [None] * 40):
        self.sap_number = sap_number
        self.serial_number = serial_number
        self.production_date = production_date
        self.description = description

    def __str__(self):
        return f"DeviceIdentification: SapNumber={self.sap_number}, SerialNumber={self.serial_number}, ProductionDate={self.production_date}, Description={self.description}"

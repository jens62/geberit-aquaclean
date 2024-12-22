from abc import ABC, abstractmethod

class IApiCall(ABC):
    @abstractmethod
    def get_payload(self) -> bytes:
        pass

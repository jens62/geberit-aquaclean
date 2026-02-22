from dataclasses import dataclass

# geberit-aquaclean/aquaclean-core/Api/CallClasses/Dtos/StatisticsDescale.cs

@dataclass
class StatisticsDescale:
    unposted_shower_cycles: int
    days_until_next_descale: int
    days_until_shower_restricted: int
    shower_cycles_until_confirmation: int
    date_time_at_last_descale: int
    date_time_at_last_descale_prompt: int
    number_of_descale_cycles: int

    def __init__(
        self,
        unposted_shower_cycles: int = 0,
        days_until_next_descale: int = 0,
        days_until_shower_restricted: int = 0,
        shower_cycles_until_confirmation: int = 0,
        date_time_at_last_descale: int = 0,
        date_time_at_last_descale_prompt: int = 0,
        number_of_descale_cycles: int = 0,
    ):
        self.unposted_shower_cycles = unposted_shower_cycles
        self.days_until_next_descale = days_until_next_descale
        self.days_until_shower_restricted = days_until_shower_restricted
        self.shower_cycles_until_confirmation = shower_cycles_until_confirmation
        self.date_time_at_last_descale = date_time_at_last_descale
        self.date_time_at_last_descale_prompt = date_time_at_last_descale_prompt
        self.number_of_descale_cycles = number_of_descale_cycles

    def __str__(self):
        return (
            f"StatisticsDescale: "
            f"UnpostedShowerCycles={self.unposted_shower_cycles}, "
            f"DaysUntilNextDescale={self.days_until_next_descale}, "
            f"DaysUntilShowerRestricted={self.days_until_shower_restricted}, "
            f"ShowerCyclesUntilConfirmation={self.shower_cycles_until_confirmation}, "
            f"DateTimeAtLastDescale={self.date_time_at_last_descale}, "
            f"DateTimeAtLastDescalePrompt={self.date_time_at_last_descale_prompt}, "
            f"NumberOfDescaleCycles={self.number_of_descale_cycles}"
        )

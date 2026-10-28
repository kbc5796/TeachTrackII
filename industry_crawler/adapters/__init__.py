"""Provider registry. Add adapters here without changing source routing."""
from .workday import WorkdayAdapter
from .greenhouse import GreenhouseAdapter
from .lever import LeverAdapter
from .html import HTMLAdapter
from .smartrecruiters import SmartRecruitersAdapter

ADAPTERS = {"Workday": WorkdayAdapter, "Greenhouse": GreenhouseAdapter,
            "Lever": LeverAdapter, "SmartRecruiters": SmartRecruitersAdapter}


def get_adapter(platform, spider):
    return ADAPTERS.get(platform, HTMLAdapter)(spider)

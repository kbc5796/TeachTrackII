import pytest

from industry_crawler.spiders.platforms import (
    detect_platform, greenhouse_board, lever_company, workday_api_base,
)


@pytest.mark.parametrize("host,platform", [
    ("a.myworkdayjobs.com", "Workday"), ("a.myworkdaysite.com", "Workday"),
    ("boards.greenhouse.io", "Greenhouse"), ("jobs.lever.co", "Lever"),
    ("careers.smartrecruiters.com", "SmartRecruiters"), ("a.icims.com", "iCIMS"),
    ("a.taleo.net", "Taleo"), ("a.oraclecloud.com", "Oracle Cloud"),
    ("a.successfactors.eu", "SuccessFactors"), ("a.eightfold.ai", "Eightfold"),
    ("myworkdayjobs.com.evil.com", "Custom"), ("notlever.co", "Custom"),
])
def test_platform(host, platform):
    assert detect_platform("https://" + host) == platform


def test_embedded_platform():
    assert detect_platform("https://example.org", '<iframe src="https://jobs.lever.co/example">') == "Lever"


def test_api_helpers():
    assert workday_api_base("https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite") == (
        "https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/NVIDIAExternalCareerSite")
    assert greenhouse_board("https://boards.greenhouse.io/embed/job_board?for=example") == "example"
    assert lever_company("https://jobs.lever.co/example") == "example"


def test_workdaysite_recruiting_tenant():
    assert workday_api_base("https://wd5.myworkdaysite.com/en-US/recruiting/devonenergy/Careers") == (
        "https://wd5.myworkdaysite.com/wday/cxs/devonenergy/Careers")
    assert workday_api_base("https://wd5.myworkdaysite.com/recruiting/devonenergy") is None

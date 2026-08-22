import pytest

from linkedin_intelligence.utils import load_icp
from linkedin_intelligence.ingest.classifier import classify_position, classify_company
from linkedin_intelligence.ingest.parsers import ConnectionRecord


@pytest.fixture()
def config():
    return {
        "icp": load_icp(),
        "taxonomy": {
            "classify_position": classify_position,
            "classify_company": classify_company,
            "tax": None,
        },
    }


@pytest.fixture()
def conn():
    return ConnectionRecord(
        first_name="Ahmed", last_name="AMINE", url="https://www.linkedin.com/in/ahmed-amine",
        email="", company="OpenAI ", position="AI Engineer", connected_on="14 Aug 2026",
    )

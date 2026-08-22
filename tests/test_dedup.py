"""Deduplication: identity resolution must be deterministic and merge-aware."""

from linkedin_intelligence.ingest.parsers import ConnectionRecord
from linkedin_intelligence.ingest.deduplicator import deduplicate


def _conn(first, last, url, company="Acme", position="Engineer", date="1 Jan 2026"):
    return ConnectionRecord(first_name=first, last_name=last, url=url,
                            email="", company=company, position=position,
                            connected_on=date)


def test_exact_url_duplicates_are_merged():
    a = _conn("Sara", "Ali", "https://www.linkedin.com/in/sara-ali")
    b = _conn("Sara", "Ali", "https://www.linkedin.com/in/sara-ali",
              company="", position="")
    unique, log, report = deduplicate([a, b])
    assert report["raw_records"] == 2
    assert report["unique_prospects"] == 1
    assert report["duplicates_removed"] == 1
    assert log[0]["strategy"] == "exact_url"


def test_name_company_matching_is_normalized():
    a = _conn("Omar", "Benali", "https://www.linkedin.com/in/omar-a",
              company="  Stripe  ", position="Data Scientist")
    b = _conn("Omar", "Benali", "https://www.linkedin.com/in/omar-b",
              company="Stripe", position="Data Scientist")
    unique, log, report = deduplicate([a, b])
    assert report["unique_prospects"] == 1
    assert log[0]["strategy"] == "name_company"


def test_distinct_people_are_kept():
    a = _conn("Yasmina", "El Fassi", "https://www.linkedin.com/in/y1")
    b = _conn("Karim", "El Fassi", "https://www.linkedin.com/in/k1")
    unique, log, report = deduplicate([a, b])
    assert report["unique_prospects"] == 2
    assert report["duplicates_removed"] == 0


def test_richer_record_is_kept_as_canonical():
    a = _conn("Nadia", "Tazi", "https://www.linkedin.com/in/nadia-tazi",
              company="Google", position="Product Manager", date="")
    b = _conn("Nadia", "Tazi", "https://www.linkedin.com/in/nadia-tazi",
              company="Google", position="Senior Product Manager", date="5 May 2024")
    unique, _, _ = deduplicate([a, b])
    assert len(unique) == 1
    assert unique[0].position == "Senior Product Manager"
    assert unique[0].connected_on == "5 May 2024"

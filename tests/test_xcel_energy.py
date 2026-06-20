"""
Unit tests for Xcel Energy Green Button ESPI parsing.
Run: python -m pytest /app/ross-house-backend/tests/test_xcel_energy.py -v
(offline — no network required)
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rental.xcel_energy_router import parse_espi_feed, extract_subscription_id

INTERVAL_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:espi="http://naesb.org/espi">
  <entry>
    <content>
      <espi:IntervalBlock>
        <espi:interval>
          <espi:start>1735689600</espi:start>
          <espi:duration>86400</espi:duration>
        </espi:interval>
        <espi:IntervalReading>
          <espi:timePeriod>
            <espi:start>1735689600</espi:start>
            <espi:duration>3600</espi:duration>
          </espi:timePeriod>
          <espi:value>1200</espi:value>
        </espi:IntervalReading>
        <espi:IntervalReading>
          <espi:timePeriod>
            <espi:start>1735693200</espi:start>
            <espi:duration>3600</espi:duration>
          </espi:timePeriod>
          <espi:value>800</espi:value>
        </espi:IntervalReading>
      </espi:IntervalBlock>
    </content>
  </entry>
</feed>"""

SUMMARY_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:espi="http://naesb.org/espi">
  <entry>
    <content>
      <espi:UsageSummary>
        <espi:billingPeriod>
          <espi:start>1733011200</espi:start>
          <espi:duration>2592000</espi:duration>
        </espi:billingPeriod>
        <espi:overallConsumptionLastPeriod>
          <espi:powerOfTenMultiplier>0</espi:powerOfTenMultiplier>
          <espi:value>450000</espi:value>
        </espi:overallConsumptionLastPeriod>
      </espi:UsageSummary>
    </content>
  </entry>
</feed>"""

MULTIPLIER_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:espi="http://naesb.org/espi">
  <entry>
    <content>
      <espi:ReadingType>
        <espi:powerOfTenMultiplier>3</espi:powerOfTenMultiplier>
        <espi:uom>72</espi:uom>
      </espi:ReadingType>
    </content>
  </entry>
  <entry>
    <content>
      <espi:IntervalBlock>
        <espi:IntervalReading>
          <espi:timePeriod>
            <espi:start>1735689600</espi:start>
            <espi:duration>900</espi:duration>
          </espi:timePeriod>
          <espi:value>2</espi:value>
        </espi:IntervalReading>
      </espi:IntervalBlock>
    </content>
  </entry>
</feed>"""


def test_parse_interval_readings():
    parsed = parse_espi_feed(INTERVAL_FEED)
    readings = parsed["interval_readings"]
    assert len(readings) == 2
    assert readings[0]["value_kwh"] == 1.2  # 1200 Wh -> 1.2 kWh
    assert readings[1]["value_kwh"] == 0.8
    assert readings[0]["start_epoch"] == 1735689600
    assert readings[0]["duration_seconds"] == 3600


def test_parse_usage_summary():
    parsed = parse_espi_feed(SUMMARY_FEED)
    summaries = parsed["usage_summaries"]
    assert len(summaries) == 1
    assert summaries[0]["total_kwh"] == 450.0  # 450000 Wh -> 450 kWh
    assert summaries[0]["duration_seconds"] == 2592000


def test_power_of_ten_multiplier():
    parsed = parse_espi_feed(MULTIPLIER_FEED)
    readings = parsed["interval_readings"]
    assert len(readings) == 1
    assert readings[0]["value_kwh"] == 2.0  # 2 * 10^3 Wh = 2000 Wh = 2 kWh


def test_invalid_xml_returns_empty():
    parsed = parse_espi_feed("not xml at all <<<")
    assert parsed["interval_readings"] == []
    assert parsed["usage_summaries"] == []


def test_extract_subscription_id_direct():
    assert extract_subscription_id({"subscription_id": "12345"}) == "12345"
    assert extract_subscription_id({"subscriptionId": 99}) == "99"


def test_extract_subscription_id_from_resource_uri():
    tj = {"resourceURI": "https://myenergy.xcelenergy.com/greenbutton-connect/gbc/espi/1_1/resource/Subscription/ABC123"}
    assert extract_subscription_id(tj) == "ABC123"
    tj2 = {"resourceURI": ".../Subscription/777/UsagePoint/5"}
    assert extract_subscription_id(tj2) == "777"


def test_extract_subscription_id_missing():
    assert extract_subscription_id({}) == ""

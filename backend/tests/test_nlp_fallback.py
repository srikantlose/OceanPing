from app.modules.nlp.classifier import _classify_keywords, detect_urgency


def test_keyword_classification_english():
    assert _classify_keywords("the road is under water near the beach").hazard_type == "coastal_flooding"
    assert _classify_keywords("huge waves crashing over the wall").hazard_type == "high_waves"
    assert _classify_keywords("black oil slick near the harbour").hazard_type == "oil_spill"


def test_keyword_classification_code_mixed():
    assert _classify_keywords("kadal thanni vandhu vellam aayiduchu").hazard_type == "coastal_flooding"
    assert _classify_keywords("paani bhar gaya sadak pe").hazard_type == "coastal_flooding"


def test_unclear_text_returns_none():
    assert _classify_keywords("hello how are you today").hazard_type is None


def test_urgency_detection():
    assert detect_urgency("people are trapped, please help!") == "high"
    assert detect_urgency("minor erosion I noticed yesterday") == "low"
    assert detect_urgency("water on the road") == "medium"
    assert detect_urgency(None) == "medium"

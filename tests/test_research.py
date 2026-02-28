from titanflow.modules.research.module import ResearchModule


def test_parse_llm_response_success():
    text = "SUMMARY: hello\nRELEVANCE: 0.8"
    summary, relevance = ResearchModule._parse_llm_response(text)
    assert summary == "hello"
    assert relevance == 0.8


def test_parse_llm_response_malformed_score():
    text = "SUMMARY: hello\nRELEVANCE: nope"
    summary, relevance = ResearchModule._parse_llm_response(text)
    assert summary == "hello"
    assert relevance == 0.5

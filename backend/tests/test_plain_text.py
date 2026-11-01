from app.agent.prompts import plain_text


def test_markdown_bullets_and_bold_become_plain_text():
    raw = "**1. Lebu - Tower B**\n*   **Price per sqm:** 79,608 ETB\n*   **Delivery Date:** Not provided\n\n\n\n**Recommendation:**\nTower B suits you."
    assert plain_text(raw) == (
        "1. Lebu - Tower B\n• Price per sqm: 79,608 ETB\n• Delivery Date: Not provided\n\nRecommendation:\nTower B suits you."
    )


def test_dashes_headings_and_nested_items():
    assert plain_text("## Options\n- one\n  - two\n+ three") == "Options\n• one\n  • two\n• three"


def test_ordinary_text_is_left_alone():
    text = "Price is 2 * 3 = 6 and a well-known -10% rate."
    assert plain_text(text) == text

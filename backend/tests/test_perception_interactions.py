"""Tests for UI element interaction capability inference."""
from agent.perception import format_ui_state


def _node(**kwargs):
    return {
        "className": "android.view.View",
        "text": "",
        "resourceId": "",
        "boundsInScreen": {"left": 10, "top": 20, "right": 110, "bottom": 70},
        "isEnabled": True,
        "isVisibleToUser": True,
        "children": [],
        **kwargs,
    }


def _elements(root):
    return format_ui_state({
        "device_context": {"screen_bounds": {"width": 400, "height": 800}},
        "a11y_tree": root,
    })[1]


def test_class_heuristic_does_not_claim_actual_clickable():
    button = _node(className="android.widget.ImageButton", isClickable=False)
    element = _elements(button)[0]
    assert element["clickable"] is False
    assert element["tap_candidate"] is True
    assert element["tap_evidence"] == "class_heuristic"
    assert "tap" in element["capabilities"]


def test_child_uses_nearest_clickable_parent_as_tap_target():
    child = _node(
        className="android.widget.TextView",
        text="Open",
        boundsInScreen={"left": 20, "top": 30, "right": 80, "bottom": 60},
    )
    parent = _node(
        className="android.view.ViewGroup",
        text="",
        isClickable=True,
        boundsInScreen={"left": 10, "top": 20, "right": 110, "bottom": 70},
        children=[child],
    )
    elements = _elements(parent)
    child_element = next(item for item in elements if item["text"] == "Open")
    assert child_element["clickable"] is False
    assert child_element["tap_candidate"] is True
    assert child_element["tap_evidence"] == "ancestor_clickable"
    assert child_element["tap_target"]["cx"] == 60
    assert child_element["tap_target"]["cy"] == 45


def test_edit_text_exposes_input_capability_not_clickability():
    edit = _node(className="android.widget.EditText", isClickable=False, isFocusable=True)
    element = _elements(edit)[0]
    assert element["editable"] is True
    assert "input" in element["capabilities"]
    assert element["clickable"] is False


def test_disabled_and_invisible_nodes_are_retained_as_non_operable_evidence():
    node = _node(
        className="android.widget.Button",
        text="Continue",
        isClickable=True,
        isEnabled=False,
        isVisibleToUser=False,
    )
    element = _elements(node)[0]
    assert element["enabled"] is False
    assert element["visible"] is False
    assert element["clickable"] is True

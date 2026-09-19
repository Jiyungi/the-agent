"""The fifteen planted defects: five criteria, three instances each.

Each instance sits on its own element, so no element carries two defects, and
one criterion per page, so defects cannot interfere with each other.

Two fixture-design rules learned the hard way, both from the TypeSafe gate
failure. The lesson there was that a fixture is worthless when the defect is
indistinguishable from correct behaviour in the evidence we collect.

  * **Never plant a keyboard trap on a modal dialog.** An ARIA modal dialog is
    *supposed* to cycle Tab within itself. A Tab-swallowing handler there is
    indistinguishable from the correct pattern, so it would test nothing. The
    three 2.1.2 instances go on a menu, a tablist and a text input, none of
    which may legitimately hold focus.
  * **Never break a control that a state reach depends on** in a way that stops
    the reach working. The 2.1.1 instances turn buttons into divs but keep the
    click handler, so `.click()` still opens the dialog and the state is still
    reachable. The defect is the lost focusability, not lost function.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Defect:
    """One planted instance. The manifest row the scorer reads."""

    criterion: str
    instance: int
    #: Where on the page, for the write-up and for the scorer's "did it find
    #: the second and third occurrence" question.
    region: str
    #: The state the Tab run must be in for this defect to be observable.
    state: str
    #: A CSS selector identifying the damaged element on the broken page.
    selector: str
    #: One sentence, in the terms the checker would use.
    expect: str
    #: Literal find/replace pairs applied to index.html.
    replacements: tuple[tuple[str, str], ...] = ()
    #: CSS appended inside a <style> block.
    css: str = ""
    #: JavaScript appended inside a <script> block at end of body.
    js: str = ""
    #: HTML appended just before </body>.
    html: str = ""


# --------------------------------------------------------------------------
# 2.1.1 Keyboard - three divs acting as buttons.
# Clickable, not focusable. Click behaviour is preserved deliberately so the
# dialog state stays reachable; what is lost is the keyboard.
# --------------------------------------------------------------------------
KEYBOARD = [
    Defect(
        criterion="2.1.1", instance=1, region="header, delivery section",
        state="loaded", selector="#ally-d1",
        expect="a div with a click handler, no tabindex and no interactive role, "
               "so Tab never reaches it",
        replacements=((
            '<button type="button" onclick="openDialog(\'dialog1\', this)">Add Delivery Address</button>',
            '<div id="ally-d1" class="fake-btn" onclick="openDialog(\'dialog1\', this)">Add Delivery Address</div>',
        ),),
    ),
    Defect(
        criterion="2.1.1", instance=2, region="signup form",
        state="loaded", selector="#ally-d2",
        expect="the form's submit control is a div, unreachable by keyboard",
        replacements=((
            '<button type="submit">Submit</button>',
            '<div id="ally-d2" class="fake-btn" onclick="document.getElementById(\'signup\')'
            '.dispatchEvent(new Event(\'submit\', {cancelable: true, bubbles: true}))">Submit</div>',
        ),),
    ),
    Defect(
        criterion="2.1.1", instance=3, region="preferences, page foot",
        state="loaded", selector='[role="switch"]',
        expect="the switch keeps its role and its click handler but loses tabindex, "
               "so it is announced but never reached",
        replacements=((
            '<div role="switch" aria-checked="false" tabindex="0">',
            '<div role="switch" aria-checked="false">',
        ),),
    ),
]

# --------------------------------------------------------------------------
# 2.1.2 No Keyboard Trap - three Tab-swallowing handlers.
# Deliberately NOT on the modal dialog: a modal is supposed to cycle Tab.
# --------------------------------------------------------------------------
TRAP = [
    Defect(
        criterion="2.1.2", instance=1, region="actions menu",
        state="menu-open", selector="#menu1",
        expect="Tab is swallowed inside the open menu, so focus repeats on the "
               "same menu item instead of leaving",
        js="document.getElementById('menu1').addEventListener('keydown', function (e) {\n"
           "  if (e.key === 'Tab') { e.preventDefault(); e.stopPropagation(); }\n"
           "}, true);",
    ),
    Defect(
        criterion="2.1.2", instance=2, region="composers tablist",
        state="tabs-focused", selector='[role="tablist"]',
        expect="Tab is swallowed in the tablist, so focus never moves on to the "
               "tab panel",
        js="document.querySelector('[role=\"tablist\"]').addEventListener('keydown', function (e) {\n"
           "  if (e.key === 'Tab') { e.preventDefault(); e.stopPropagation(); }\n"
           "}, true);",
    ),
    Defect(
        criterion="2.1.2", instance=3, region="signup form, email field",
        state="loaded", selector="#email",
        expect="Tab is swallowed on the email input, so focus stays on it",
        js="document.getElementById('email').addEventListener('keydown', function (e) {\n"
           "  if (e.key === 'Tab') { e.preventDefault(); e.stopPropagation(); }\n"
           "}, true);",
    ),
]

# --------------------------------------------------------------------------
# 2.4.3 Focus Order - three flex containers with reversed CSS order.
# Never a positive tabindex: axe's `tabindex` rule fires on that and the
# comparison claim stops being true.
# --------------------------------------------------------------------------
ORDER = [
    Defect(
        criterion="2.4.3", instance=1, region="signup form",
        state="loaded", selector="#email_item",
        expect="the email field is painted above the name field while DOM order "
               "still reaches name first, so tab order contradicts reading order",
        css="#signup { display: flex; flex-direction: column; }\n"
            "#email_item { order: -1; }",
    ),
    Defect(
        criterion="2.4.3", instance=2, region="delivery dialog footer",
        state="dialog-open", selector=".dialog_form_actions",
        expect="Add and Cancel are painted in the opposite order to the one Tab "
               "reaches them in",
        css=".dialog_form_actions { display: flex; flex-direction: row-reverse; "
            "justify-content: flex-end; }",
    ),
    # The tablist version of this defect was unobservable, and no checker could
    # ever have found it. The tabs use a roving tabindex, so exactly one of the
    # four is in the Tab sequence; reversing their row leaves that single stop's
    # rank unchanged. Measured on the deployed page: tab order [1..9], reading
    # order [1,2,3,5,4,6,7,8,9] -- position 7 is #tab-1 in both. The whole
    # defect produced no difference to detect.
    #
    # A positive tabindex instead, which is WCAG failure F44 and the way this
    # criterion is actually broken in the wild. It cannot be unobservable: the
    # switch is painted at the page foot and reached first, because positive
    # tabindex values are visited before every tabindex=0 element on the page.
    # Geometry is untouched, so the deviation is in the tab order alone.
    Defect(
        criterion="2.4.3", instance=3, region="preferences, page foot",
        state="loaded", selector='[role="switch"]',
        expect="the switch carries tabindex=1, so a keyboard user entering the "
               "page lands on the last control on it before any of the ones "
               "above",
        replacements=((
            '<div role="switch" aria-checked="false" tabindex="0">',
            '<div role="switch" aria-checked="false" tabindex="1">',
        ),),
    ),
]

# --------------------------------------------------------------------------
# 2.4.7 Focus Visible - outline removed on three controls.
# site.css sets `:focus { outline: 3px solid #0b5cad }` globally, so a more
# specific rule removing it leaves no visible indicator at all.
# --------------------------------------------------------------------------
VISIBLE = [
    Defect(
        criterion="2.4.7", instance=1, region="signup form, name field",
        state="loaded", selector="#full_name",
        expect="nothing on screen changes when focus lands on the name field",
        css="#full_name:focus { outline: none !important; }",
    ),
    Defect(
        criterion="2.4.7", instance=2, region="delivery dialog, city field",
        state="dialog-open", selector=".city_input",
        expect="nothing on screen changes when focus lands on the city field",
        css=".city_input:focus { outline: none !important; }",
    ),
    Defect(
        criterion="2.4.7", instance=3, region="preferences, page foot",
        state="loaded", selector='[role="switch"]',
        expect="nothing on screen changes when focus lands on the switch",
        # The switch's indicator is NOT an outline. switch.css already sets
        # outline:none on :focus and signals focus with padding, border-width
        # and two background colours instead. `outline: none` here was a no-op,
        # which is why the first fixture check passed while the defect was
        # absent. Every focused property is pinned back to its unfocused value.
        css=(
            '[role="switch"]:focus {\n'
            "  padding: 4px 4px 8px 8px !important;   /* base is 4px 4px 8px 8px */\n"
            "  border-width: 0 !important;            /* base is 0 */\n"
            "  background-color: transparent !important;\n"
            "  outline: none !important;\n"
            "}\n"
            '[role="switch"]:focus span.switch { background-color: transparent !important; }'
        ),
    ),
]

# --------------------------------------------------------------------------
# 2.4.11 Focus Not Obscured - three sticky elements drawn over controls.
# --------------------------------------------------------------------------
OBSCURED = [
    # Each cover is sized over exactly ONE control, so one cover maps to one
    # planted instance. The first version used three full-width fixed bars,
    # which obscured five controls between them: the check was right and the
    # fixture was wrong, and 2.4.11 scored 0/3 with eight reported targets.
    #
    # Two consequences of that, both deliberate here:
    #
    #   * `selector` names the COVERED control, not the cover. The check
    #     reports the element that is obscured, and a manifest recording the
    #     element doing the obscuring can never be matched against it.
    #   * Position is absolute in document coordinates, not fixed in the
    #     viewport. A fixed cover sits over whatever happens to be at that
    #     viewport position when focus scrolls the page, so it covers a
    #     different control from one stop to the next.
    #
    # Coordinates are measured from the control page: #action_output at
    # (223,337), #full_name at (124,473), #email at (124,533), each 185x21,
    # with Submit at y=565 left clear.
    Defect(
        criterion="2.4.11", instance=1, region="menu section, last action field",
        state="loaded", selector="#action_output",
        expect="a panel is painted over the last-action field, so the element "
               "with focus is not the element on screen at that point",
        css="#ally-cover-1 { position: absolute; left: 217px; top: 330px; "
            "width: 200px; height: 34px; background: #23150f; color: #fff; "
            "z-index: 9000; padding: 6px 10px; font-size: 12px; }",
        html='<div id="ally-cover-1">Cookie notice</div>',
    ),
    Defect(
        criterion="2.4.11", instance=2, region="signup form, name field",
        state="loaded", selector="#full_name",
        expect="a panel is painted over the name field",
        css="#ally-cover-2 { position: absolute; left: 118px; top: 466px; "
            "width: 200px; height: 34px; background: #b85632; color: #fff; "
            "z-index: 9000; padding: 6px 10px; font-size: 12px; }",
        html='<div id="ally-cover-2">Live chat</div>',
    ),
    Defect(
        criterion="2.4.11", instance=3, region="signup form, email field",
        state="loaded", selector="#email",
        expect="a panel is painted over the email field",
        css="#ally-cover-3 { position: absolute; left: 118px; top: 526px; "
            "width: 200px; height: 34px; background: #3f765c; color: #fff; "
            "z-index: 9000; padding: 6px 10px; font-size: 12px; }",
        html='<div id="ally-cover-3">Newsletter</div>',
    ),
]

PAGES: dict[str, list[Defect]] = {
    "2-1-1": KEYBOARD,
    "2-1-2": TRAP,
    "2-4-3": ORDER,
    "2-4-7": VISIBLE,
    "2-4-11": OBSCURED,
}

#: Shared styling for the div-as-button instances, so they look like the
#: buttons they replaced. A defect the eye can spot is not the defect we mean.
FAKE_BUTTON_CSS = """
.fake-btn {
  display: inline-block;
  font: inherit;
  padding: 1px 7px;
  border: 1px solid #767676;
  border-radius: 3px;
  background: #efefef;
  cursor: pointer;
}
"""

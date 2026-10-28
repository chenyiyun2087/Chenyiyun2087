import pytest
from selenium.common.exceptions import ElementClickInterceptedException

from sina.bs_detection import BSpointChecker as capture


class Page:
    def __init__(self, *, visible=False):
        self.visible = visible
        self.dismissals = 0

    def find_elements(self, by, selector):
        assert selector == ".SFA-dark-market-layer .SFA-dark-close"
        return [self] if self.visible else []

    def find_element(self, by, selector):
        assert selector == ".SFA-dark-market-layer"
        return self

    def is_displayed(self):
        return self.visible

    def click(self):
        self.visible = False
        self.dismissals += 1


def test_visible_promo_is_closed_before_chart_control():
    page = Page(visible=True)

    class ChartControl:
        def click(self):
            assert not page.visible

    capture.click_chart_control(page, ChartControl())
    assert page.dismissals == 1


def test_promo_injected_after_lookup_is_closed_and_retried():
    page = Page()

    class ChartControl:
        attempts = 0

        def click(self):
            self.attempts += 1
            if self.attempts == 1:
                page.visible = True
                raise ElementClickInterceptedException("promo appeared")
            assert not page.visible

    control = ChartControl()
    capture.click_chart_control(page, control)
    assert control.attempts == 2
    assert page.dismissals == 1


def test_unknown_interceptor_remains_a_failure():
    class ChartControl:
        def click(self):
            raise ElementClickInterceptedException("unknown overlay")

    with pytest.raises(ElementClickInterceptedException, match="unknown overlay"):
        capture.click_chart_control(Page(), ChartControl())

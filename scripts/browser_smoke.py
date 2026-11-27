"""Run against an already started local server; requires optional Playwright."""
from playwright.sync_api import sync_playwright, expect


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1100})
        failures = []
        page.on("pageerror", lambda error: failures.append(str(error)))
        page.goto("http://127.0.0.1:8010")
        expect(page.get_by_role("heading", name="策略研究", exact=True)).to_be_visible()
        expect(page.locator("#strategy-list tr")).to_have_count(1)
        page.locator("#strategy-list .row-action").first.click()
        expect(page.locator("#strategy")).to_contain_text("")
        page.wait_for_function("document.querySelector('#strategy').value.length > 50")
        page.locator("#name").fill("浏览器验证 · 日线")
        page.locator("#run-button").click()
        expect(page.locator("#status")).to_have_text("已完成", timeout=20000)
        expect(page.locator("#trades-value")).to_have_text("1")
        expect(page.locator("#chart svg")).to_be_visible()
        page.get_by_role("tab", name="成交", exact=True).click()
        expect(page.locator("#detail-content")).to_contain_text("000001.SZ")
        with page.expect_download() as download:
            page.locator("#export-button").click()
        assert download.value.suggested_filename.endswith(".json")
        page.locator("#frequency").select_option("1m")
        page.locator("#name").fill("浏览器验证 · 分钟")
        page.locator("#run-button").click()
        expect(page.locator("#result-name")).to_have_text("浏览器验证 · 分钟")
        expect(page.locator("#status")).to_have_text("已完成", timeout=20000)
        page.screenshot(path="/tmp/quantstone-desktop.png", full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path="/tmp/quantstone-mobile.png", full_page=True)
        page.set_viewport_size({"width": 1440, "height": 1100})
        original = page.locator("#strategy").input_value()
        page.locator("#strategy").fill("def handle_bar(c, bars):\n    while True: pass\n")
        page.locator("#run-button").click()
        expect(page.locator("#cancel-button")).to_be_visible()
        page.locator("#cancel-button").click()
        expect(page.locator("#status")).to_have_text("已取消", timeout=10000)
        page.locator("#strategy").fill(original)
        page.locator("#refresh-button").click()
        expect(page.locator("#history")).to_contain_text("浏览器验证")
        page.locator("#back-button").click()
        expect(page.locator("#strategy-list")).to_contain_text("我的第一个策略")
        page.locator("#strategy-search").fill("不存在的策略")
        expect(page.locator("#strategy-empty")).to_be_visible()
        page.locator("#strategy-search").fill("")
        page.locator("#new-strategy-button").click()
        page.locator("#new-name").fill("浏览器创建策略")
        page.locator("#new-description").fill("验证策略档案创建和列表入口")
        page.locator("#strategy-form button[type=submit]").click()
        expect(page.locator("#strategy-title")).to_have_text("浏览器创建策略")
        page.locator("#back-button").click()
        expect(page.locator("#strategy-list")).to_contain_text("浏览器创建策略")
        assert not failures, failures
        browser.close()
        print("Browser smoke passed: strategy list/create, daily/minute runs, trades, export, cancellation, responsive layout; no JS exceptions")


if __name__ == "__main__":
    main()

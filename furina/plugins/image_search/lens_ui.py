"""Current Lens upload UI. No cookies, login extraction or CAPTCHA bypass."""
import time
import uuid
from selenium.webdriver.common.by import By

class LensUIError(RuntimeError):
    pass

def page_error(driver):
    text = driver.execute_script('return document.body ? document.body.innerText : ""') or ''
    low = text.lower()
    if any(s in low for s in ['图片未找到', 'image not found', '未与您的账号关联', 'not associated with your account']):
        return 'upload_reference_invalid'
    if any(s in low for s in ['unusual traffic', '异常流量', '我们的系统检测到']):
        return 'verification_required'
    return ''

def open_upload(driver, navigate=None, check=None, timeout=45):
    navigate = navigate or (lambda fn: fn())
    check = check or (lambda: None)
    # Use the current camera dialog, not the retired /upload classic form.
    marker = uuid.uuid4().hex
    driver.execute_script('window.__qqbotLensNav = arguments[0]', marker)
    navigate(lambda: driver.get('https://www.google.com/imghp?hl=zh-CN'))
    deadline = time.monotonic() + timeout
    next_click = time.monotonic() + 2
    while time.monotonic() < deadline:
        check()
        # page_load_strategy=none must not reuse a file input from the old document.
        if driver.execute_script('return window.__qqbotLensNav === arguments[0] || document.readyState === "loading"', marker):
            time.sleep(.25)
            continue
        error = page_error(driver)
        if error == 'verification_required':
            raise LensUIError(error)
        for item in driver.find_elements(By.CSS_SELECTOR, 'input[type="file"]'):
            if item.is_enabled():
                return item
        body = driver.execute_script('return document.body ? document.body.innerText : ""') or ''
        dialog_open = any(s in body.lower() for s in ('上传文件', 'upload a file', 'upload an image', '上传图片'))
        if not dialog_open and time.monotonic() >= next_click:
            for item in driver.find_elements(By.CSS_SELECTOR, '[aria-label], [title]'):
                label = ((item.get_attribute('aria-label') or '') + ' ' + (item.get_attribute('title') or '')).lower()
                if any(s in label for s in ['以图搜图', '按图片搜索', '按图搜索', '按圖搜尋', '使用图片搜索', 'search by image', 'google lens', 'google 智能镜头']):
                    if item.is_displayed() and item.is_enabled():
                        navigate(lambda: item.click())
                        next_click = time.monotonic() + 4
                        break
        time.sleep(.25)
    raise LensUIError('upload_control_missing')

def upload(driver, image_path, navigate=None, check=None):
    navigate = navigate or (lambda fn: fn())
    element = open_upload(driver, navigate, check)
    navigate(lambda: element.send_keys(str(image_path)))

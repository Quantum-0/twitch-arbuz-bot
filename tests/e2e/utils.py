from pathlib import Path

import playwright.sync_api

__path = Path(__file__).resolve().parent / "screenshots"
__scr_iter = 0

def screenshot(page: playwright.sync_api.Page, full_page: bool = True) -> bytes:
    global __scr_iter
    __scr_iter += 1
    data = page.screenshot(full_page=full_page)
    # with open(__path / "screenshot_%03d.jpg".format(__scr_iter) , "wb+") as f:
    #     f.write(data)
    return data

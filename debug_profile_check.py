import os
from playwright.sync_api import sync_playwright

home = os.path.expanduser('~')
profile = os.path.join(home, '.browser_rank_profile')
print('HOME=', repr(home))
print('PROFILE=', repr(profile))
os.makedirs(profile, exist_ok=True)

with sync_playwright() as p:
    try:
        context = p.chromium.launch_persistent_context(profile, headless=False, channel='chrome', args=['--no-sandbox'])
        print('LAUNCHED_OK')
        context.close()
    except Exception as e:
        import traceback
        traceback.print_exc()
        print('ERROR_TYPE=', type(e).__name__)
        print('ERROR=', e)

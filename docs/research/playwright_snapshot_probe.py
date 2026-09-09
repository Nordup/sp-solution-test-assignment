import asyncio, json, re, importlib.metadata
from playwright.async_api import async_playwright
async def main():
 async with async_playwright() as p:
  browser=await p.chromium.launch(channel='chrome',headless=True)
  page=await browser.new_page()
  await page.set_content('<h1>Capability probe</h1><button onclick="this.textContent=\'Done\'">Execute probe</button><input aria-label="Example"><iframe srcdoc="&lt;button&gt;Frame probe&lt;/button&gt;"></iframe>')
  snap=await page.aria_snapshot(mode='ai',depth=8)
  print('playwright',importlib.metadata.version('playwright'));print(snap)
  ref=re.search(r'button "Execute probe" \[ref=([^\]]+)\]',snap).group(1)
  await page.locator('aria-ref='+ref).click()
  print('ref_click_verified',await page.get_by_role('button',name='Done').count()==1)
  frame_ref=re.search(r'button "Frame probe" \[ref=([^\]]+)\]',snap).group(1)
  try:
   await page.locator('aria-ref='+frame_ref).click(timeout=1500)
   print('iframe_ref_click_verified',True)
  except Exception as e:print('iframe_ref_click_verified',False,type(e).__name__)
  await page.set_content('<button>Replacement page</button>')
  try:
   await page.locator('aria-ref='+ref).click(timeout=500)
   print('stale_ref_rejected',False)
  except Exception: print('stale_ref_rejected',True)
  await browser.close()
asyncio.run(main())

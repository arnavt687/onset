// Screenshot pages to PNG (1920x1080 by default) with the installed Chrome.
//
//   node tools/video/shoot.cjs out.png URL [out2.png URL2 ...] [--transparent] [--wait 1500] [--w 1920 --h 1080]
//
// --transparent keeps the page's transparent background (for overlays such as lower-thirds).

const { chromium } = require('playwright');

(async () => {
  const v = process.argv.slice(2);
  const opt = { transparent: false, wait: 1500, w: 1920, h: 1080 };
  const pairs = [];
  for (let i = 0; i < v.length; i++) {
    if (v[i] === '--transparent') opt.transparent = true;
    else if (v[i] === '--wait') opt.wait = Number(v[++i]);
    else if (v[i] === '--w') opt.w = Number(v[++i]);
    else if (v[i] === '--h') opt.h = Number(v[++i]);
    else pairs.push([v[i], v[++i]]);
  }
  const browser = await chromium.launch({ channel: 'chrome', headless: true, args: ['--enable-gpu', '--ignore-gpu-blocklist', '--hide-scrollbars'] });
  const ctx = await browser.newContext({ viewport: { width: opt.w, height: opt.h }, colorScheme: 'dark' });
  for (const [out, url] of pairs) {
    const page = await ctx.newPage();
    await page.goto(url, { waitUntil: 'load' });
    await page.waitForTimeout(opt.wait);
    await page.screenshot({ path: out, omitBackground: opt.transparent });
    await page.close();
    console.log('wrote', out);
  }
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
